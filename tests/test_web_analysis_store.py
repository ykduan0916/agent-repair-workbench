"""P1-01b B1: real temporary SQLite admission/startup; no model network or containers."""

import asyncio
import select
import signal
import sqlite3
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import event

from agent_workbench.analysis_store.fencing import AnalysisFence
from agent_workbench.analysis_store.models import AnalysisEvent
from agent_workbench.controlled_tools.resources import load_case
from agent_workbench.mcp_config import canonical, digest
from agent_workbench.model_gateway.gateway import PreparedCall, Reply
from agent_workbench.orchestration.contracts import Usage
from agent_workbench.orchestration.policy import transition
from agent_workbench.orchestration.runtime import AnalysisRuntime
from agent_workbench.run_store.models import StoreError
from agent_workbench.run_store.store import RunStore
from agent_workbench.runner.models import SandboxConfig
from agent_workbench.web_analyses.fencing import AnalysisWebFence
from agent_workbench.web_analyses.models import WEB_BUDGET, AnalysisRequest
from agent_workbench.web_analyses.store import WebAnalysisStore


class CountingAdapter:
    config_sha256 = "a" * 64

    def __init__(self, store):
        self.store = store
        self.calls = 0

    def prepare(self, role, messages, output_cap):
        body = canonical(messages)
        return PreparedCall(body, digest(body), 1000, 32, "b" * 64)

    async def send(self, request, timeout):
        # The durable execution permission must already be consumed before external send.
        with sqlite3.connect(self.store._base.path) as connection:
            assert connection.execute("SELECT consumed FROM web_analysis_requests").fetchall() == [
                (1,)
            ]
        self.calls += 1
        return Reply(
            decision=canonical(
                {
                    "action": "conclude",
                    "assessment": "inconclusive",
                    "reason_code": "evidence_missing",
                    "evidence_refs": [],
                }
            ),
            input_tokens=10,
            output_tokens=10,
        )


@pytest.fixture
def host(tmp_path):
    store = WebAnalysisStore(tmp_path / "db")
    store.initialize()
    request = AnalysisRequest(
        request_id=str(uuid4()), case_name="output_mismatch", intent="diagnose"
    )
    registry = load_case(
        request.case_name, task_id=request.request_id, intent=request.intent, budget=WEB_BUDGET
    )
    adapter = CountingAdapter(store)
    runtime = AnalysisRuntime(registry, store, adapter)
    with AnalysisWebFence(store.directory) as owner:
        with AnalysisFence(store.directory, request.request_id) as fence:
            yield store, request, runtime, adapter, owner, fence
    store.close()


def admit(host):
    store, request, runtime, _, owner, fence = host
    return store.admit(
        request, runtime.initial_summary(), policy_sha256="c" * 64, owner=owner, fence=fence
    )


def sql(store, statement, params=()):
    with sqlite3.connect(store._base.path) as connection:
        return connection.execute(statement, params).fetchall()


def test_new_admission_is_atomic_private_and_replay_cannot_execute(host):
    store, request, runtime, adapter, owner, fence = host
    admitted = admit(host)
    assert admitted.claim is not None
    assert admitted.claim.token not in repr(admitted)
    assert sql(store, "SELECT claim_sha256, consumed FROM web_analysis_requests") == [
        (digest(admitted.claim.token.encode()), 0)
    ]
    assert store.active_id() == request.request_id
    assert [item.event.kind for item in store.events(request.request_id)] == ["created"]
    replay = admit(host)
    assert replay.claim is None and replay.saved.reused
    assert store.existing(request).claim is None
    result = asyncio.run(runtime.run_created(admitted.claim, admitted.saved, fence=fence))
    assert result.summary.state.outcome == "inconclusive" and adapter.calls == 1
    new_runtime = AnalysisRuntime(runtime.registry, store, adapter)
    with pytest.raises(StoreError, match="^admission_invalid$"):
        asyncio.run(new_runtime.run_created(admitted.claim, admitted.saved, fence=fence))
    assert adapter.calls == 1
    assert store.release_safe(owner)
    assert store.active_id() is None
    assert store.existing(request).claim is None


def test_cancel_before_start_is_durable_and_sends_zero_calls(host):
    store, request, runtime, adapter, owner, fence = host
    admitted = admit(host)
    # A second store needs neither owner nor task lock to commit cancellation.
    other = WebAnalysisStore(store.directory)
    try:
        assert other.request_cancel(request.request_id)
        assert other.request_cancel(request.request_id)
        assert other.cancellation_requested(request.request_id)
    finally:
        other.close()
    result = asyncio.run(runtime.run_created(admitted.claim, admitted.saved, fence=fence))
    assert result.summary.state.outcome == "cancelled"
    assert result.summary.usage.model_calls == adapter.calls == 0
    assert not store.request_cancel(request.request_id)
    assert store.release_safe(owner)


@pytest.mark.parametrize("change", ["token", "hash", "task", "summary", "case", "evidence"])
def test_invalid_startup_cannot_consume_claim_or_call_model(host, change):
    store, _, runtime, adapter, _, fence = host
    admitted = admit(host)
    claim, saved = admitted.claim, admitted.saved
    if change == "token":
        claim = replace(claim, token="0" * 64)
    elif change == "hash":
        claim = replace(claim, initial_sha256="0" * 64)
    elif change == "task":
        claim = replace(claim, task_id=str(uuid4()))
    elif change == "summary":
        saved = replace(saved, summary=saved.summary.model_copy(update={"summary_revision": 1}))
    elif change == "case":
        runtime.registry.case_name = "passing"
    else:
        runtime.registry.register({"events": []})
    with pytest.raises(StoreError):
        asyncio.run(runtime.run_created(claim, saved, fence=fence))
    assert adapter.calls == 0
    assert sql(store, "SELECT consumed FROM web_analysis_requests") == [(0,)]
    assert store.get(admitted.saved.summary.task_id).summary.summary_revision == 0


def test_duplicate_request_payload_conflicts_and_maybe_gone_never_restarts(host):
    store, request, _, _, _, _ = host
    admit(host)
    other = request.model_copy(update={"intent": "check_only"})
    with pytest.raises(StoreError, match="^request_conflict$"):
        store.existing(other)
    # Simulate a future history pruning operation; the independent tombstone is retained.
    sql(store, "DELETE FROM analysis_events")
    sql(store, "DELETE FROM analysis_summaries")
    with pytest.raises(StoreError, match="^request_gone$"):
        store.existing(request)


@pytest.mark.parametrize(
    "table", ["analysis_summaries", "analysis_events", "web_analysis_requests"]
)
def test_admission_partial_failure_rolls_back_all_three_tables(host, table):
    store, _, _, adapter, _, _ = host
    engine = store._base._database()

    def fail(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO " + table + " "):
            raise RuntimeError("injected persistence failure")

    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError):
            admit(host)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    for name in ("analysis_summaries", "analysis_events", "web_analysis_requests"):
        assert sql(store, "SELECT COUNT(*) FROM " + name) == [(0,)]
    assert adapter.calls == 0


def test_consumption_and_checking_transition_rollback_together(host):
    store, _, runtime, adapter, _, fence = host
    admitted = admit(host)
    engine = store._base._database()

    def fail(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE web_analysis_requests"):
            raise RuntimeError("injected claim consumption failure")

    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError):
            asyncio.run(runtime.run_created(admitted.claim, admitted.saved, fence=fence))
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert store.get(admitted.claim.task_id).summary.summary_revision == 0
    assert sql(store, "SELECT consumed FROM web_analysis_requests") == [(0,)]
    assert adapter.calls == 0


def test_active_unique_index_and_index_removal_fail_closed(host):
    store, _, _, _, _, _ = host
    admit(host)
    with pytest.raises(sqlite3.IntegrityError):
        sql(
            store,
            "INSERT INTO web_analysis_requests SELECT ?, payload_sha256, case_name, intent, "
            "policy_sha256, initial_sha256, claim_sha256, consumed, active, cancel_requested "
            "FROM web_analysis_requests",
            (str(uuid4()),),
        )
    sql(store, "DROP INDEX ux_web_analysis_active")
    with pytest.raises(StoreError, match="^schema_incompatible$"):
        store.active_id()


def reserve_link(store, saved, fence):
    request_id = str(uuid4())
    old = saved.summary
    updated = old.model_copy(
        update={
            "summary_revision": old.summary_revision + 1,
            "usage": Usage(tool_calls=old.usage.tool_calls + 1),
        }
    )
    return store.reserve_execution(
        task_id=old.task_id,
        request_id=request_id,
        payload_sha256="e" * 64,
        config=SandboxConfig(),
        case_sha256="f" * 64,
        summary=updated,
        expected_revision=old.summary_revision,
        expected_sha256=saved.sha256,
        fence=fence,
        event=AnalysisEvent(
            kind="tool_started",
            role="diagnosis",
            tool="request_reproduction",
            call_id=request_id,
            request_sha256="e" * 64,
        ),
    )


def test_analysis_unknown_does_not_release_unconfirmed_linked_runs(host):
    store, request, _, _, owner, fence = host
    admitted = admit(host)
    saved = store.start_created(
        admitted.claim, admitted.saved, fence=fence, case_name=request.case_name
    ).saved
    first = reserve_link(store, saved, fence)
    from agent_workbench.analysis_store.models import SaveResult

    second = reserve_link(store, SaveResult(first.summary, first.summary_sha256, False), fence)
    old = second.summary
    terminal = old.model_copy(
        update={
            "state": transition(
                old.state,
                "finished",
                expected_revision=old.state.revision,
                outcome="outcome_unknown",
            ),
            "summary_revision": old.summary_revision + 1,
            "finished_at": datetime.now(UTC),
        }
    )
    store.save(
        terminal,
        AnalysisEvent(kind="finished", error_code="INTERRUPTED"),
        expected_revision=old.summary_revision,
        expected_sha256=second.summary_sha256,
        fence=fence,
    )
    assert not store.release_safe(owner)
    base = RunStore(store.directory, clock=lambda: datetime.now(UTC).timestamp() + 121)
    for execution in (first, second):
        base.reconcile(execution.run.run_id, execution.run.version, executor_state="absent")
        if execution == first:
            assert not store.release_safe(owner)
    # An absent observation is not positive proof that a pending create ended.
    assert not store.release_safe(owner)
    assert store.active_id() == request.request_id
    base.close()


def test_global_owner_fences_another_process(host):
    store, *_ = host
    script = """
import sys
from pathlib import Path
from agent_workbench.web_analyses.fencing import AnalysisWebFence
from agent_workbench.run_store.models import StoreError
try:
    with AnalysisWebFence(Path(sys.argv[1])):
        print('acquired')
except StoreError as error:
    print(error.code)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(store.directory)],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert result.stdout.strip() == "analysis_busy"


def test_0006_upgrade_preserves_rows_and_rejects_nonempty_downgrade(tmp_path):
    base = RunStore(tmp_path / "migration")
    base.initialize()
    run = base.create("isolated-fixture", "pass", "a" * 64, "b" * 64)
    with base._database().begin() as connection:
        command.downgrade(base._migration(connection), "0006_reproductions")

    def rows():
        with sqlite3.connect(base.path) as connection:
            names = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name!='alembic_version'"
                )
                if row[0]
                not in (
                    "web_analysis_requests",
                    "web_repair_requests",
                    "repair_candidate_decisions",
                    "repair_candidate_decision_events",
                    "memory_projects",
                    "memory_connections",
                    "memory_events",
                    "memory_heads",
                    "memory_revisions",
                    "memory_receipts",
                    "memory_jobs",
                    "memory_projections",
                    "memory_calls",
                )
            ]
            return {
                name: connection.execute('SELECT * FROM "' + name + '" ORDER BY 1').fetchall()
                for name in names
            }

    before = rows()
    base.initialize()
    assert base.detail(run.run_id).run == run
    assert rows() == before
    base.close()


def test_nonempty_web_receipt_prevents_downgrade(host):
    store, *_ = host
    admit(host)
    with store._base._database().begin() as connection:
        with pytest.raises(ValueError, match="migration_not_empty"):
            command.downgrade(store._base._migration(connection), "0006_reproductions")


def test_check_only_runs_without_model_configuration(tmp_path):
    store = WebAnalysisStore(tmp_path / "offline")
    store.initialize()
    request = AnalysisRequest(request_id=str(uuid4()), case_name="passing", intent="check_only")
    runtime = AnalysisRuntime(
        load_case(
            request.case_name, task_id=request.request_id, intent=request.intent, budget=WEB_BUDGET
        ),
        store,
        None,
    )
    try:
        with AnalysisWebFence(store.directory) as owner:
            with AnalysisFence(store.directory, request.request_id) as fence:
                accepted = store.admit(
                    request,
                    runtime.initial_summary(),
                    policy_sha256="a" * 64,
                    owner=owner,
                    fence=fence,
                )
                result = asyncio.run(
                    runtime.run_created(accepted.claim, accepted.saved, fence=fence)
                )
                assert result.summary.state.outcome == "check_complete"
                assert result.summary.usage.model_calls == 0
                assert store.release_safe(owner)
    finally:
        store.close()


def test_cli_cannot_reuse_a_browser_initial_record(host, monkeypatch):
    store, _, runtime, adapter, _, fence = host
    accepted = admit(host)
    fence.close()
    monkeypatch.setattr(runtime, "initial_summary", lambda: accepted.saved.summary)
    with pytest.raises(StoreError, match="^analysis_conflict$"):
        asyncio.run(runtime.run())
    assert adapter.calls == 0
    assert store.get(accepted.claim.task_id).summary.summary_revision == 0


def test_owner_death_before_start_leaves_receipt_without_reclaim(tmp_path):
    store = WebAnalysisStore(tmp_path / "crash")
    store.initialize()
    request = AnalysisRequest(request_id=str(uuid4()), case_name="passing", intent="check_only")
    script = """
import sys,time
from pathlib import Path
from agent_workbench.analysis_store.fencing import AnalysisFence
from agent_workbench.controlled_tools.resources import load_case
from agent_workbench.orchestration.runtime import AnalysisRuntime
from agent_workbench.web_analyses.fencing import AnalysisWebFence
from agent_workbench.web_analyses.models import AnalysisRequest, WEB_BUDGET
from agent_workbench.web_analyses.store import WebAnalysisStore
store=WebAnalysisStore(Path(sys.argv[1]))
request=AnalysisRequest(request_id=sys.argv[2],case_name='passing',intent='check_only')
runtime=AnalysisRuntime(load_case('passing',task_id=request.request_id,intent='check_only',budget=WEB_BUDGET),store,None)
with AnalysisWebFence(store.directory) as owner:
 with AnalysisFence(store.directory,request.request_id) as fence:
  store.admit(request,runtime.initial_summary(),policy_sha256='a'*64,owner=owner,fence=fence)
  print('committed',flush=True)
  time.sleep(30)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(store.directory), request.request_id],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert select.select([process.stdout], [], [], 10)[0]
        assert process.stdout.readline().strip() == "committed"
        process.send_signal(signal.SIGSTOP)
        with pytest.raises(StoreError, match="^analysis_busy$"):
            with AnalysisWebFence(store.directory):
                pass
        with pytest.raises(StoreError, match="^analysis_busy$"):
            with AnalysisFence(store.directory, request.request_id):
                pass
        process.kill()
        process.wait(timeout=5)
        replay = store.existing(request)
        assert replay.claim is None and replay.saved.summary.summary_revision == 0
        with AnalysisWebFence(store.directory) as owner:
            assert not store.release_safe(owner)
            saved = store.get(request.request_id)
            reconciled = store.reconcile(
                request.request_id, saved.summary.summary_revision, saved.sha256
            )
            assert reconciled.summary.state.outcome == "outcome_unknown"
            assert store.release_safe(owner)
        assert store.existing(request).claim is None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdout.close()
        store.close()


@pytest.mark.parametrize("mode", ["safe", "unconfirmed", "corrupt_report", "missing_report"])
def test_terminal_analysis_requires_current_safe_report_for_finished_execution(host, mode):
    from agent_workbench.mcp_models import MCPReport
    from agent_workbench.models import Report
    from agent_workbench.runner.models import ExecutionEvidence, SandboxReport

    store, request, _, _, owner, fence = host
    accepted = admit(host)
    started = store.start_created(
        accepted.claim, accepted.saved, fence=fence, case_name=request.case_name
    ).saved
    execution = reserve_link(store, started, fence)
    old = execution.summary
    terminal = old.model_copy(
        update={
            "state": transition(
                old.state,
                "finished",
                expected_revision=old.state.revision,
                outcome="outcome_unknown",
            ),
            "summary_revision": old.summary_revision + 1,
            "finished_at": datetime.now(UTC),
        }
    )
    store.save(
        terminal,
        AnalysisEvent(kind="finished", error_code="INTERRUPTED"),
        expected_revision=old.summary_revision,
        expected_sha256=execution.summary_sha256,
        fence=fence,
    )
    claim = execution.claim
    report = SandboxReport(
        run_id=claim.run.attempt_id,
        record_id=claim.run.run_id,
        mcp=MCPReport(
            invocation_id="synthetic",
            adapter_outcome="completed",
            contract_report=Report(status="passed"),
        ),
        execution=ExecutionEvidence(cleanup="unconfirmed" if mode == "unconfirmed" else "removed"),
    )
    store._base.finish(
        claim.run.run_id,
        claim.run.attempt_id,
        claim.token,
        claim.run.version,
        "outcome_unknown",
        report,
        "unavailable" if mode == "unconfirmed" else "absent",
    )
    if mode == "corrupt_report":
        sql(store, "UPDATE artifacts SET payload=?", (b"{}",))
    elif mode == "missing_report":
        sql(store, "DELETE FROM artifacts")
    if mode in ("corrupt_report", "missing_report"):
        with pytest.raises(StoreError):
            store.release_safe(owner)
        assert store.active_id() == request.request_id
    else:
        assert store.release_safe(owner) is (mode == "safe")


def test_second_new_request_cannot_replace_an_unstarted_active_request(host):
    store, _, _, _, owner, _ = host
    admit(host)
    other = AnalysisRequest(request_id=str(uuid4()), case_name="passing", intent="check_only")
    runtime = AnalysisRuntime(
        load_case(
            other.case_name, task_id=other.request_id, intent=other.intent, budget=WEB_BUDGET
        ),
        store,
        None,
    )
    with AnalysisFence(store.directory, other.request_id) as fence:
        with pytest.raises(StoreError, match="^recovery_required$"):
            store.admit(
                other, runtime.initial_summary(), policy_sha256="b" * 64, owner=owner, fence=fence
            )
    assert len(store.list()) == 1


def test_cancel_persistence_failure_leaves_receipt_unchanged(host):
    store, request, _, _, _, _ = host
    admitted = admit(host)
    engine = store._base._database()

    def fail(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE web_analysis_requests"):
            raise RuntimeError("injected cancellation failure")

    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError):
            store.request_cancel(request.request_id)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert not store.cancellation_requested(request.request_id)
    assert store.get(request.request_id).sha256 == admitted.saved.sha256
    assert store.request_cancel(request.request_id)
    assert store.cancellation_requested(request.request_id)


def test_safe_slot_release_preserves_unknown_model_reservation(host, monkeypatch):
    store, request, runtime, adapter, owner, fence = host
    admitted = admit(host)

    async def unknown(request, timeout):
        adapter.calls += 1
        return Reply(error="MODEL_USAGE_UNKNOWN")

    monkeypatch.setattr(adapter, "send", unknown)
    result = asyncio.run(runtime.run_created(admitted.claim, admitted.saved, fence=fence))
    assert result.summary.state.phase == "finished"
    assert result.summary.state.outcome == "outcome_unknown"
    assert result.summary.usage.model_calls == adapter.calls == 1
    assert result.summary.usage.reserved_tokens == 1032
    assert not result.summary.usage.usage_complete
    assert result.summary.execution_record_ids == ()
    before = store.detail(request.request_id)
    assert store.release_safe(owner)
    assert store.active_id() is None
    assert store.detail(request.request_id) == before
    assert store.existing(request).claim is None


def test_0006_nonempty_analysis_upgrade_is_explicit_and_byte_preserving(tmp_path):
    store = WebAnalysisStore(tmp_path / "older")
    store.initialize()
    task_id = str(uuid4())
    runtime = AnalysisRuntime(
        load_case("passing", task_id=task_id, intent="check_only", budget=WEB_BUDGET),
        store,
        None,
    )
    with AnalysisFence(store.directory, task_id) as fence:
        saved = store.create(runtime.initial_summary(), AnalysisEvent(kind="created"), fence=fence)
    with store._transaction(write=True) as connection:
        command.downgrade(store._base._migration(connection), "0006_reproductions")
    names = ("analysis_summaries", "analysis_events", "analysis_execution_links")
    before = {name: sql(store, "SELECT * FROM " + name) for name in names}
    with pytest.raises(StoreError, match="^schema_incompatible$"):
        store.get(task_id)
    assert sql(store, "SELECT version_num FROM alembic_version") == [("0006_reproductions",)]
    store.initialize()
    assert {name: sql(store, "SELECT * FROM " + name) for name in names} == before
    assert store.get(task_id).summary == saved.summary
    assert store.active_id() is None
    store.close()


def test_cancel_persisted_after_graph_scheduling_prevents_first_model(host, monkeypatch):
    store, request, runtime, adapter, _, fence = host
    accepted = admit(host)
    original = runtime.turn

    async def cancel_at_turn_entry(role):
        assert store.request_cancel(request.request_id)
        return await original(role)

    monkeypatch.setattr(runtime, "turn", cancel_at_turn_entry)
    result = asyncio.run(runtime.run_created(accepted.claim, accepted.saved, fence=fence))
    assert result.summary.state.outcome == "cancelled"
    assert result.summary.usage.model_calls == adapter.calls == 0
    assert result.summary.usage.reserved_tokens == 0
    assert not any(item.event.kind == "model_started" for item in store.events(request.request_id))
