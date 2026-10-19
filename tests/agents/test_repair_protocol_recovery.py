"""REL-01/02: real ledger and controller, deterministic malformed model replies."""

import asyncio
import json
import sqlite3

import httpx
import pytest

from agent_workbench.model_gateway.gateway import PROFILE, Config, Reply
from agent_workbench.model_gateway.repair import RepairDeepSeekAdapter
from agent_workbench.orchestration.contracts import Budget
from agent_workbench.run_store.models import StoreError
from tests.agents.test_repair_controller import controller, execute
from tests.agents.test_repair_tool_bridge import ctx as ctx


def requests(ctx):
    with sqlite3.connect(ctx.store._base.path) as db:
        return [json.loads(row[0]) for row in db.execute("SELECT payload FROM repair_requests")]


@pytest.mark.parametrize("mode", ["single_role", "three_role"])
@pytest.mark.parametrize("first", ["read_source", "request_reproduction"])
@pytest.mark.parametrize(
    "bad,code",
    [
        (Reply(b'{"action":', 100, 100), "MODEL_DECISION_JSON_INVALID"),
        (Reply(b'{"action":"secret-invalid"}', 100, 100), "MODEL_ROLE_SCHEMA_INVALID"),
        (Reply(None, 100, 100, "MODEL_DECISION_INVALID"), "MODEL_DECISION_INVALID"),
    ],
)
def test_one_correction_retains_failure_and_completed_work(
    ctx, monkeypatch, mode, first, bad, code
):
    runtime, adapter = controller(
        ctx,
        [
            first,
            bad,
            "request_reproduction" if first == "read_source" else "read_source",
            "apply_candidate",
            "compare_candidate",
        ],
        mode,
    )
    sent_messages = []
    prepare = adapter.prepare

    def capture(role, messages, output_cap):
        sent_messages.append(messages)
        return prepare(role, messages, output_cap)

    monkeypatch.setattr(adapter, "prepare", capture)
    result = execute(runtime)
    assert result.cursor.task.outcome == "candidate_verified"
    assert result.cursor.task.usage.model_calls == 5
    assert result.cursor.task.usage.tool_calls == 4
    assert result.cursor.task.usage.known_input_tokens == 500
    assert len(ctx.attempts) == 5  # One reproduction, four frozen comparison slots.
    failed = [r for r in requests(ctx) if r["error_code"] == code]
    assert len(failed) == 1 and failed[0]["status"] == "failed"
    assert failed[0]["known_output_tokens"] == 100
    assert len(sent_messages[2]) == 3 and code in sent_messages[2][2]["content"]
    assert "secret-invalid" not in json.dumps(sent_messages)
    assert adapter.contexts[2]["completed_reads"] == adapter.contexts[1]["completed_reads"]
    assert all(len(m) == 2 for m in sent_messages[3:])
    with sqlite3.connect(ctx.store._base.path) as db:
        payloads = b"".join(row[0] for row in db.execute("SELECT payload FROM repair_events"))
    assert b"secret-invalid" not in payloads


def test_second_format_failure_stops_across_roles(ctx):
    bad = Reply(b"{}", 10, 20)
    runtime, adapter = controller(ctx, [bad, "request_reproduction", bad, "apply_candidate"])
    result = execute(runtime)
    assert result.cursor.task.error_code == "MODEL_ROLE_SCHEMA_INVALID"
    assert result.cursor.task.outcome == "inconclusive"
    assert adapter.calls == 3 and adapter.roles == ["diagnosis", "diagnosis", "repair"]
    assert result.cursor.task.usage.tool_calls == 1 and len(ctx.attempts) == 1
    assert result.cursor.task.candidate_count == 0


@pytest.mark.parametrize(
    "code",
    [
        "MODEL_OUTPUT_TRUNCATED",
        "MODEL_RESPONSE_INVALID",
        "MODEL_TIMEOUT",
        "MODEL_TRANSPORT_ERROR",
        "MODEL_CANCELLED",
    ],
)
def test_nonformat_failure_never_corrects_even_with_known_usage(ctx, code):
    runtime, adapter = controller(ctx, [Reply(None, 10, 20, code), "request_reproduction"])
    result = execute(runtime)
    assert adapter.calls == 1 and not ctx.attempts
    assert result.cursor.task.error_code == code
    assert result.cursor.task.usage.known_input_tokens == 10


def test_unknown_usage_preserves_failure_reason_and_reservation(ctx):
    runtime, adapter = controller(ctx, [Reply(error="MODEL_TIMEOUT")])
    result = execute(runtime)
    assert adapter.calls == 1 and result.cursor.task.outcome == "outcome_unknown"
    assert result.cursor.task.error_code == "MODEL_TIMEOUT"
    assert result.cursor.task.usage.reserved_tokens == 18192
    assert requests(ctx)[0]["status"] == "outcome_unknown"


@pytest.mark.parametrize("limit", ["model_calls", "total_tokens"])
def test_correction_requires_remaining_original_budget(ctx, limit):
    budget = Budget(
        total_tokens=18192 if limit == "total_tokens" else 2_000_000,
        output_tokens=8192,
        model_calls=1 if limit == "model_calls" else 12,
    )
    runtime, adapter = controller(ctx, [Reply(b"{}", 100, 100)], budget=budget)
    result = execute(runtime)
    assert result.cursor.task.outcome == "budget_exceeded"
    assert adapter.calls == 1 and not ctx.attempts
    assert requests(ctx)[0]["error_code"] == "MODEL_ROLE_SCHEMA_INVALID"


def test_needs_evidence_is_not_format_failure(ctx):
    runtime, adapter = controller(ctx, ["needs_evidence"])
    result = execute(runtime)
    assert result.cursor.task.error_code == "MODEL_EVIDENCE_REQUIRED"
    assert adapter.calls == 1


def test_correction_does_not_extend_original_deadline(ctx, monkeypatch):
    runtime, adapter = controller(ctx, [Reply(b"{}", 10, 20)])
    now = [0.0]
    runtime.clock = lambda: now[0]
    original = ctx.store.settle_model

    def settle(*args, **kwargs):
        saved = original(*args, **kwargs)
        now[0] = 601.0
        return saved

    monkeypatch.setattr(ctx.store, "settle_model", settle)
    result = execute(runtime)
    assert adapter.calls == 1 and not ctx.attempts
    assert result.cursor.task.error_code == "DEADLINE_EXCEEDED"
    assert requests(ctx)[0]["error_code"] == "MODEL_ROLE_SCHEMA_INVALID"


def test_empty_handoff_evidence_never_retries(ctx):
    runtime, adapter = controller(
        ctx,
        [
            lambda _: dict(
                action="conclude",
                disposition="handoff",
                reason_code="evidence_missing",
                evidence_refs=[],
            )
        ],
    )
    result = execute(runtime)
    assert result.cursor.task.error_code == "MODEL_EVIDENCE_REQUIRED"
    assert adapter.calls == 1


@pytest.mark.parametrize(
    "arguments,code,corrects",
    [
        ('{"decision":', "MODEL_DECISION_JSON_INVALID", True),
        ('{"decision":{},"decision":{}}', "MODEL_DECISION_JSON_INVALID", True),
        ('{"wrong":{}}', "MODEL_DECISION_INVALID", True),
        ('{"decision":{"x":' + "[" * 25 + "0" + "]" * 25 + "}}", "MODEL_DECISION_LIMIT", False),
        ('{"decision":{"action":"private-invalid"}}', "MODEL_ROLE_SCHEMA_INVALID", True),
        ('{"decision":{"x":' + "[" * 35 + "0" + "]" * 35 + "}}", "MODEL_DECISION_LIMIT", False),
        ('{"decision":{"x":"' + "x" * 66000 + '"}}', "MODEL_DECISION_LIMIT", False),
    ],
)
def test_real_http_decoder_and_controller_classify_and_correct(ctx, arguments, code, corrects):
    from tests.agents.test_gateway import response

    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        envelope = json.loads(response())
        envelope["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = (
            arguments
            if len(bodies) == 1
            else json.dumps(
                {
                    "decision": dict(
                        action="conclude",
                        disposition="needs_evidence",
                        reason_code="evidence_missing",
                        evidence_refs=[],
                    )
                }
            )
        )
        return httpx.Response(200, json=envelope)

    runtime, _ = controller(ctx, [])
    runtime.adapter = RepairDeepSeekAdapter(
        Config("https://example.invalid", "deepseek-flash", "test-only", PROFILE),
        transport=httpx.MockTransport(handle),
    )
    result = execute(runtime)
    assert len(bodies) == (2 if corrects else 1)
    assert result.cursor.task.usage.known_input_tokens == 32 * len(bodies)
    assert any(r["error_code"] == code for r in requests(ctx))
    assert not ctx.attempts and result.cursor.task.usage.tool_calls == 0
    if corrects:
        assert code in bodies[1]["messages"][-1]["content"]
        assert "private-invalid" not in json.dumps(bodies[1])


@pytest.mark.parametrize("bad_ref", [False, True])
def test_handoff_diagnostics_are_durable_not_corrected(ctx, bad_ref):
    def conclude(value):
        ref = value["evidence_records"][0]["ref"].copy()
        if bad_ref:
            ref["sha256"] = "f" * 64
        return dict(
            action="conclude",
            disposition="handoff",
            reason_code="contract_mismatch",
            evidence_refs=[ref],
        )

    runtime, adapter = controller(ctx, [conclude])
    result = execute(runtime)
    code = "MODEL_EVIDENCE_INVALID" if bad_ref else "MODEL_HANDOFF_INVALID"
    assert result.cursor.task.error_code == code and adapter.calls == 1
    assert requests(ctx)[0]["error_code"] == code


@pytest.mark.parametrize("cancel", [False, True])
def test_no_correction_after_settlement_ack_loss_or_cancellation(ctx, monkeypatch, cancel):
    runtime, adapter = controller(ctx, [Reply(b"{}", 10, 20)])
    original = ctx.store.settle_model

    def settle(*args, **kwargs):
        result = original(*args, **kwargs)
        if cancel:
            asyncio.current_task().cancel()
            return result
        raise StoreError("store_unavailable")

    monkeypatch.setattr(ctx.store, "settle_model", settle)
    result = execute(runtime)
    assert adapter.calls == 1 and not ctx.attempts
    assert result.cancelled if cancel else result.requires_reconcile
    assert requests(ctx)[0]["error_code"] == "MODEL_ROLE_SCHEMA_INVALID"
