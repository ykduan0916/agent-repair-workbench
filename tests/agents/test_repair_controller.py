"""Bounded controller with real ledger/snapshots and explicitly synthetic oracle/model."""

import asyncio
import json
import sqlite3
from uuid import uuid4

import pytest

from agent_workbench.controlled_tools.resources import load_case
from agent_workbench.mcp_config import canonical, digest
from agent_workbench.model_gateway.gateway import PreparedCall, Reply
from agent_workbench.orchestration.contracts import Budget
from agent_workbench.repair_runtime.controller import RepairController
from agent_workbench.run_store.models import StoreError
from tests.agents.test_repair_tool_bridge import ctx as ctx

PATCH = (
    'def lookup_result(count: int, scenario: str) -> dict[str, Any]:\n    return {"count": count}\n'
)


def uid():
    return str(uuid4())


class Scripted:
    config_sha256 = "a" * 64

    def __init__(self, fixture, script):
        self.fixture, self.script = fixture, list(script)
        self.roles, self.contexts, self.calls, self.prepared = [], [], 0, []
        self.hook = None
        self.sent = []

    def prompt(self, role):
        return "synthetic host prompt " + role

    def prepare(self, role, messages, output_cap):
        self.roles.append(role)
        self.contexts.append(json.loads(messages[1]["content"]))
        assert output_cap == 8192
        raw = canonical(messages)
        prepared = PreparedCall(raw, digest(raw), 10000, output_cap, "c" * 64)
        self.prepared.append(prepared)
        return prepared

    def decision(self, action):
        value = self.contexts[-1]
        if callable(action):
            return action(value)
        if action == "needs_evidence":
            return dict(
                action="conclude",
                disposition="needs_evidence",
                reason_code="evidence_missing",
                evidence_refs=[],
            )
        if action == "apply_candidate":
            request = dict(
                tool=action,
                request_id=uid(),
                candidate_id=value["next_candidate_id"],
                expected_baseline_hash=value["baseline_tree_hash"],
                expected_file_hash=value["baseline_file_hash"],
                replacement=PATCH,
                evidence_refs=[value["evidence_records"][-1]["ref"]],
            )
        elif action == "compare_candidate":
            candidate = value["tool_materials"]["apply_candidate"]
            request = dict(
                tool=action,
                request_id=uid(),
                candidate_id=candidate["candidate_id"],
                expected_candidate_tree_hash=candidate["candidate_tree_hash"],
                evidence_refs=[value["evidence_records"][-1]["ref"]],
            )
        else:
            request = next(item for item in value["read_tool_templates"] if item["tool"] == action)
        self.sent.append(request)
        return dict(action="request_tool", request=request)

    async def send(self, prepared, timeout):
        task_id = self.contexts[-1]["task_id"]
        with sqlite3.connect(self.fixture.store._base.path) as db:
            rows = db.execute(
                "SELECT payload FROM repair_requests WHERE task_id=?", (task_id,)
            ).fetchall()
            pending = [json.loads(row[0]) for row in rows]
            assert sum(r["operation"] == "model" and r["status"] == "pending" for r in pending) == 1
        self.calls += 1
        if self.hook:
            await self.hook()
        action = self.script.pop(0) if self.script else "read_evidence"
        if isinstance(action, Reply):
            return action
        return Reply(decision=canonical(self.decision(action)), input_tokens=100, output_tokens=100)


def controller(ctx, script, mode="three_role", budget=None, task_id=None):
    budget = budget or Budget(total_tokens=2_000_000, output_tokens=8192)
    registry = load_case(
        "output_mismatch", task_id=task_id or uid(), intent="propose_repair", budget=budget
    )
    adapter = Scripted(ctx, script)
    runtime = RepairController(
        store=ctx.store,
        snapshots=ctx.snapshots,
        baseline=ctx.baseline,
        registry=registry,
        adapter=adapter,
        mode=mode,
        image_root=ctx.image,
    )
    return runtime, adapter


def execute(runtime):
    return asyncio.run(runtime.run())


@pytest.mark.parametrize("mode", ["three_role", "single_role"])
def test_full_flow_uses_durable_admission_and_host_verdict(ctx, mode):
    runtime, adapter = controller(
        ctx, ["request_reproduction", "read_source", "apply_candidate", "compare_candidate"], mode
    )
    result = execute(runtime)
    assert result.cursor.task.outcome == "candidate_verified"
    assert result.cursor.task.usage.model_calls == adapter.calls == 4
    assert result.cursor.task.usage.tool_calls == 4 and result.cursor.task.candidate_count == 1
    assert len(ctx.attempts) == 5
    assert not result.requires_reconcile
    assert adapter.roles == (
        ["diagnosis", "repair", "repair", "verification"]
        if mode == "three_role"
        else ["single"] * 4
    )
    assert all(p.output_reserved == 8192 for p in adapter.prepared)
    assert not runtime._materials
    for value in adapter.contexts:
        text = json.dumps(value)
        assert (
            "valid_zero" not in text
            and "valid_negative" not in text
            and "oracle_sha256" not in text
        )
        assert "replacement" not in text
    with pytest.raises(StoreError, match="repair_id_conflict"):
        execute(runtime)
    fresh, _ = controller(ctx, [], task_id=result.cursor.task.task_id)
    with pytest.raises(StoreError, match="repair_id_conflict"):
        execute(fresh)
    assert len(ctx.attempts) == 5


def test_rejected_first_candidate_routes_back_to_second(ctx):
    async def toggle():
        ctx.candidate_failure = len(ctx.attempts) <= 5

    ctx.oracle_hook = toggle
    runtime, adapter = controller(
        ctx,
        [
            "request_reproduction",
            "apply_candidate",
            "compare_candidate",
            "apply_candidate",
            "compare_candidate",
        ],
    )
    result = execute(runtime)
    assert (
        result.cursor.task.outcome == "candidate_verified"
        and result.cursor.task.candidate_count == 2
    )
    assert adapter.roles == ["diagnosis", "repair", "verification", "repair", "verification"]
    assert len(ctx.attempts) == 9
    assert [c["next_stage"] for c in adapter.contexts] == [
        "reproduce_failure",
        "propose_candidate",
        "compare_candidate",
        "revise_candidate",
        "compare_candidate",
    ]
    for c in (adapter.contexts[2], adapter.contexts[4]):
        assert c["candidate_application_binding"] is None
        assert (
            c["candidate_comparison_binding"]["candidate_id"]
            == c["tool_materials"]["apply_candidate"]["candidate_id"]
        )
        assert c["candidate_comparison_binding"]["candidate_id"] != c["next_candidate_id"]
    assert adapter.contexts[3]["candidate_comparison_binding"] is None


def test_two_rejected_candidates_stop_without_third(ctx):
    ctx.candidate_failure = True
    runtime, adapter = controller(
        ctx,
        [
            "request_reproduction",
            "apply_candidate",
            "compare_candidate",
            "apply_candidate",
            "compare_candidate",
            "apply_candidate",
        ],
    )
    result = execute(runtime)
    assert result.cursor.task.outcome == "inconclusive" and result.cursor.task.candidate_count == 2
    assert adapter.calls == 5 and len(ctx.attempts) == 9


@pytest.mark.parametrize("mode", ["single_role", "three_role"])
def test_identical_shared_model_budget(ctx, mode):
    def ignore_completed_reads(value):
        # A model may ignore progress hints; the actual budget must still stop it.
        items = value["read_tool_templates"]
        request = next((x for x in items if x["tool"] == "read_evidence"), None)
        if request is None:
            request = dict(value["completed_reads"][0]["request"])
        request["request_id"] = uid()
        return {"action": "request_tool", "request": request}

    runtime, adapter = controller(ctx, [ignore_completed_reads] * 12, mode)
    result = execute(runtime)
    assert result.cursor.task.outcome == "budget_exceeded"
    assert adapter.calls == result.cursor.task.usage.model_calls == 12
    assert result.cursor.task.usage.tool_calls == 12


@pytest.mark.parametrize(
    "budget",
    [
        Budget(total_tokens=8192, output_tokens=8192),
        Budget(total_tokens=2_000_000, output_tokens=8192, tool_calls=1),
    ],
)
def test_token_and_tool_limits_never_overshoot(ctx, budget):
    runtime, adapter = controller(ctx, ["read_evidence", "read_evidence"], budget=budget)
    result = execute(runtime)
    assert result.cursor.task.outcome == "budget_exceeded"
    assert result.cursor.task.usage.tool_calls <= budget.tool_calls
    if budget.total_tokens == 8192:
        assert adapter.calls == 0


def test_needs_evidence_is_inconclusive(ctx):
    runtime, adapter = controller(ctx, ["needs_evidence"])
    result = execute(runtime)
    assert result.cursor.task.outcome == "inconclusive" and adapter.calls == 1
    assert not ctx.attempts


def test_unknown_usage_retains_reservation_and_never_retries(ctx):
    runtime, adapter = controller(ctx, [Reply(error="MODEL_USAGE_UNKNOWN")])
    result = execute(runtime)
    assert result.cursor.task.outcome == "outcome_unknown" and adapter.calls == 1
    assert result.cursor.task.usage.reserved_tokens == 18192
    assert not result.cursor.task.usage.usage_complete


def test_role_forbidden_candidate_is_rejected_before_tool(ctx):
    def malicious(value):
        return dict(
            action="request_tool",
            request=dict(
                tool="apply_candidate",
                request_id=uid(),
                candidate_id=uid(),
                expected_baseline_hash=value["baseline_tree_hash"],
                expected_file_hash=value["baseline_file_hash"],
                replacement=PATCH,
                evidence_refs=[value["evidence_records"][0]["ref"]],
            ),
        )

    runtime, adapter = controller(ctx, [malicious, malicious])
    result = execute(runtime)
    assert result.cursor.task.outcome == "inconclusive" and result.cursor.task.candidate_count == 0
    assert result.cursor.task.usage.tool_calls == 0 and not ctx.attempts


def test_single_cannot_apply_without_live_t05_observation(ctx):
    runtime, adapter = controller(ctx, ["apply_candidate"] * 3, "single_role")
    result = execute(runtime)
    assert result.cursor.task.outcome == "inconclusive" and result.cursor.task.candidate_count == 0
    assert not ctx.attempts


def test_forged_evidence_reference_is_not_handoff(ctx):
    def forged(value):
        return dict(
            action="conclude",
            disposition="handoff",
            reason_code="contract_mismatch",
            evidence_refs=[dict(evidence_id=uid(), sha256="a" * 64)],
        )

    runtime, adapter = controller(ctx, [forged])
    result = execute(runtime)
    assert result.cursor.task.outcome == "inconclusive" and adapter.calls == 1


def test_cancel_during_model_is_unknown_once_and_joins_sender(ctx):
    runtime, adapter = controller(ctx, ["needs_evidence"])

    async def exercise():
        entered = asyncio.Event()
        closed = asyncio.Event()

        async def wait():
            entered.set()
            try:
                await asyncio.sleep(30)
            finally:
                closed.set()

        adapter.hook = wait
        task = asyncio.create_task(runtime.run())
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        task.cancel()
        result = await task
        assert closed.is_set()
        return result

    result = asyncio.run(exercise())
    assert result.cancelled and result.cursor.task.outcome == "outcome_unknown"
    assert result.cursor.task.usage.reserved_tokens == 18192 and adapter.calls == 1


def test_failed_settlement_stops_pending_and_releases_fence(ctx, monkeypatch):
    runtime, adapter = controller(ctx, ["needs_evidence"])

    def fail(*args, **kwargs):
        raise StoreError("store_unavailable")

    monkeypatch.setattr(ctx.store, "settle_model", fail)
    result = execute(runtime)
    assert result.requires_reconcile and result.cursor.task.phase == "model_pending"
    assert adapter.calls == 1
    from agent_workbench.repair_store.fencing import RepairFence

    with RepairFence(ctx.store.directory, result.cursor.task.task_id):
        pass


def test_same_tool_request_is_replayed_without_second_charge(ctx):
    recorded = []

    def once(value):
        if not recorded:
            recorded.append(
                next(x for x in value["read_tool_templates"] if x["tool"] == "read_evidence")
            )
        return dict(action="request_tool", request=recorded[0])

    runtime, adapter = controller(ctx, [once, once, "needs_evidence"])
    result = execute(runtime)
    assert result.cursor.task.usage.tool_calls == 1 and adapter.calls == 3


def test_same_id_different_tool_payload_is_rejected(ctx):
    recorded = []

    def first(value):
        recorded.append(
            next(x for x in value["read_tool_templates"] if x["tool"] == "read_evidence")
        )
        return dict(action="request_tool", request=recorded[0])

    def second(value):
        return dict(action="request_tool", request={**recorded[0], "section": "events"})

    runtime, adapter = controller(ctx, [first, second])
    result = execute(runtime)
    assert result.cursor.task.usage.tool_calls == 1 and adapter.calls == 2
    assert result.cursor.task.outcome == "inconclusive"


def test_terminal_write_failure_is_explicit_unknown_not_success(ctx, monkeypatch):
    runtime, adapter = controller(ctx, ["needs_evidence"])

    def fail(*args, **kwargs):
        raise StoreError("store_unavailable")

    monkeypatch.setattr(ctx.store, "finish_task", fail)
    result = execute(runtime)
    assert result.requires_reconcile and result.cursor.task.phase == "ready"
    assert adapter.calls == 1 and result.cursor.task.outcome is None


def test_candidate_disk_failure_does_not_repeat_model_or_apply(ctx, monkeypatch):
    runtime, adapter = controller(ctx, ["request_reproduction", "apply_candidate"])
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(1)
        raise OSError("synthetic private disk location must not escape")

    monkeypatch.setattr(ctx.snapshots, "create_candidate", fail)
    result = execute(runtime)
    assert result.requires_reconcile and result.cursor.task.phase == "candidate_preparing"
    assert adapter.calls == 2 and len(attempts) == 1
    assert result.cursor.task.candidate_count == 1
    assert "private disk" not in result.cursor.task.model_dump_json()


def test_read_settlement_failure_propagates_latest_pending_cursor(ctx, monkeypatch):
    runtime, adapter = controller(ctx, ["read_evidence"])

    def fail(*args, **kwargs):
        raise StoreError("store_unavailable")

    monkeypatch.setattr(ctx.store, "finish_tool", fail)
    result = execute(runtime)
    assert result.requires_reconcile and result.cursor.task.phase == "tool_pending"
    assert result.cursor.task.usage.tool_calls == adapter.calls == 1
    new, again = controller(ctx, [], task_id=result.cursor.task.task_id)
    with pytest.raises(StoreError):
        execute(new)
    assert again.calls == 0


def test_cancel_during_reproduction_joins_owned_operation(ctx):
    runtime, adapter = controller(ctx, ["request_reproduction", "apply_candidate"])

    async def exercise():
        entered, closed = asyncio.Event(), asyncio.Event()

        async def wait():
            entered.set()
            try:
                await asyncio.sleep(30)
            finally:
                closed.set()

        ctx.oracle_hook = wait
        task = asyncio.create_task(runtime.run())
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        result = await asyncio.wait_for(task, 3)
        assert closed.is_set()
        return result

    result = asyncio.run(exercise())
    assert result.cancelled and result.requires_reconcile
    assert result.cursor.task.phase == "tool_pending"
    assert adapter.calls == 1 and len(ctx.attempts) == 1
    assert result.cursor.task.candidate_count == 0


def test_expired_deadline_before_model_never_sends(ctx):
    runtime, adapter = controller(ctx, ["needs_evidence"])
    values = iter([0.0, 601.0])
    runtime.clock = lambda: next(values)
    result = execute(runtime)
    assert result.cursor.task.outcome == "budget_exceeded" and adapter.calls == 0
    assert result.cursor.task.usage.model_calls == 0


def test_tool_cleanup_reserve_prevents_late_oracle_launch(ctx):
    runtime, adapter = controller(ctx, ["request_reproduction"])
    now = [0.0]
    runtime.clock = lambda: now[0]

    async def time_passes():
        now[0] = 580.0

    adapter.hook = time_passes
    result = execute(runtime)
    assert result.cursor.task.outcome == "budget_exceeded" and adapter.calls == 1
    assert not ctx.attempts and result.cursor.task.usage.tool_calls == 0


def test_transport_timeout_retains_unknown_reservation(ctx):
    runtime, adapter = controller(ctx, [])

    async def timeout():
        raise TimeoutError

    adapter.hook = timeout
    result = execute(runtime)
    assert result.cursor.task.outcome == "outcome_unknown" and adapter.calls == 1
    assert result.cursor.task.usage.reserved_tokens == 18192


def test_model_configuration_drift_prevents_next_send(ctx):
    runtime, adapter = controller(ctx, ["read_evidence"])

    async def drift():
        adapter.config_sha256 = "b" * 64

    adapter.hook = drift
    result = execute(runtime)
    assert result.cursor.task.outcome == "failed" and adapter.calls == 1
    assert result.cursor.task.error_code == "MODEL_CONFIGURATION_INVALID"


def test_verified_claim_in_model_json_cannot_set_outcome(ctx):
    def malicious(value):
        return dict(action="conclude", outcome="candidate_verified")

    runtime, adapter = controller(ctx, [malicious, malicious])
    result = execute(runtime)
    assert result.cursor.task.outcome == "inconclusive" and not ctx.attempts
    assert result.cursor.task.candidate_count == 0


def test_context_retains_each_read_and_omits_completed_targets(ctx):
    from pydantic import TypeAdapter

    from agent_workbench.controlled_tools.contracts import ToolRequest, target_key

    runtime, adapter = controller(
        ctx,
        ["read_evidence", "read_evidence", "read_contract", "read_source", "needs_evidence"],
        "single_role",
    )
    execute(runtime)
    final = adapter.contexts[-1]
    assert len(final["completed_reads"]) == 4
    done = {
        target_key(TypeAdapter(ToolRequest).validate_python(item["request"]))
        for item in final["completed_reads"]
    }
    offered = {
        target_key(TypeAdapter(ToolRequest).validate_python(item))
        for item in final["read_tool_templates"]
    }
    assert not done & offered
    assert all(item["evidence_refs"] for item in final["completed_reads"])
    assert (
        final["completed_reads"][0]["request"]["section"]
        != final["completed_reads"][1]["request"]["section"]
    )
    assert not runtime._completed_reads


def test_context_progress_follows_real_host_feedback(ctx):
    runtime, adapter = controller(
        ctx, ["request_reproduction", "read_source", "apply_candidate", "compare_candidate"]
    )
    result = execute(runtime)
    assert result.cursor.task.outcome == "candidate_verified"
    assert adapter.contexts[1]["execution_progress"]["reproduction_observed"] is True
    assert adapter.contexts[-1]["execution_progress"]["candidate_available"] is True
    assert all(
        x["tool"] != "request_reproduction" for x in adapter.contexts[1]["read_tool_templates"]
    )


def wrong_source(value):
    request = dict(next(x for x in value["read_tool_templates"] if x["tool"] == "read_source"))
    request["start_line"] += 1
    return {"action": "request_tool", "request": request}


def test_unadmitted_bad_request_gets_bounded_safe_feedback(ctx):
    runtime, adapter = controller(
        ctx, [wrong_source, "read_source", "needs_evidence"], "single_role"
    )
    result = execute(runtime)
    assert result.cursor.task.usage.model_calls == 3
    assert result.cursor.task.usage.tool_calls == 1
    assert adapter.contexts[1]["last_tool_feedback"] == {
        "tool": "read_source",
        "error_code": "PERMISSION_DENIED",
        "accepted": False,
        "corrections_remaining": 1,
    }
    assert adapter.contexts[2]["last_tool_feedback"] is None
    assert runtime._last_tool_feedback is None


def test_unadmitted_correction_limit_is_shared_and_finite(ctx):
    runtime, adapter = controller(ctx, [wrong_source] * 4, "single_role")
    result = execute(runtime)
    assert result.cursor.task.outcome == "inconclusive"
    assert result.cursor.task.error_code == "TOOL_FAILED"
    assert adapter.calls == 3 and result.cursor.task.usage.tool_calls == 0


def test_stale_candidate_binding_can_be_corrected_without_double_admission(ctx):
    def wrong_hash(value):
        request = dict(value["candidate_application_binding"])
        request.update(
            tool="apply_candidate",
            request_id=uid(),
            expected_file_hash="0" * 64,
            replacement=PATCH,
            evidence_refs=[value["evidence_records"][-1]["ref"]],
        )
        return {"action": "request_tool", "request": request}

    runtime, adapter = controller(
        ctx,
        ["request_reproduction", wrong_hash, "apply_candidate", "compare_candidate"],
        "single_role",
    )
    result = execute(runtime)
    assert result.cursor.task.outcome == "candidate_verified"
    assert result.cursor.task.candidate_count == 1
    assert result.cursor.task.usage.tool_calls == 3
    assert adapter.contexts[2]["last_tool_feedback"]["error_code"] == "STALE_VERSION"
