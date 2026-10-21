"""M3-R actual SQLite authority, durable call rollback, cancellation and role isolation."""

import asyncio
import json
import threading
import time
from uuid import uuid4

import pytest
from alembic import command

from agent_workbench.experience_memory.agent_models import MemoryBinding, agent_budget
from agent_workbench.experience_memory.agent_runtime import MemorySession
from agent_workbench.experience_memory.agent_store import AgentMemoryStore
from agent_workbench.experience_memory.engine_models import Grant, Retrieval
from agent_workbench.model_gateway.gateway import PreparedCall
from agent_workbench.orchestration.contracts import Budget
from agent_workbench.run_store import StoreError
from tests.test_memory_engine import index, memory, providers, query
from tests.test_memory_engine import state as engine_state
from tests.test_repair_store import cas
from tests.test_repair_store import ctx as repair_ctx


@pytest.fixture
def state(tmp_path):
    iterator = engine_state.__wrapped__(tmp_path)
    old, clock = next(iterator)
    store = AgentMemoryStore(old.directory, clock=lambda: clock[0])
    yield store, clock
    store.close()
    try:
        next(iterator)
    except StopIteration:
        pass


def grant(store):
    store.set_grant(
        "p",
        Grant(
            connection_id="c",
            content_type="repair_experience",
            purpose="agent_context",
            enabled=True,
            expires_at=2000.0,
        ),
        expected_version=0,
    )


def prepared_context(store, *, kind="analysis", task=None, authorized=True):
    item = memory(store)
    index(store, item)
    if authorized:
        grant(store)
    task = task or str(uuid4())
    q = query().model_copy(update={"event_id": "query-" + task})
    store.begin_task(kind, task, MemoryBinding(project_id="p", connection_id="c"), q)
    job = store.prepare_search("p", q, profile_digest=providers().embedding_fingerprint)
    store.attach_job(kind, task, job)
    lease = store.claim_job("p", job_id=job)
    snapshot = store.snapshot("p", lease)
    evidence = [(r["root_id"], r["dataset_id"], r["data_id"]) for r in snapshot["refs"]]
    refs = store.references("p", lease, evidence)
    store.finish_job("p", lease, succeeded=True)
    assert store.finish_task(
        kind, task, Retrieval(status="available", reason="matched", job_id=job, references=refs)
    )
    return task, item


def test_context_permissions_not_inherited_from_retrieve(state):
    store, _ = state
    task, _ = prepared_context(store, authorized=False)
    with pytest.raises(StoreError, match="authorization_required"):
        store.prepare_context("analysis", task, "diagnosis")
    assert not store.prepare_context("analysis", task, "verification").references


@pytest.mark.parametrize(
    "mutation", ["revoke", "delete", "edit", "pause", "grant", "policy", "expire"]
)
def test_prepared_but_not_admitted_reference_is_rechecked(state, mutation):
    store, clock = state
    task, item = prepared_context(store)
    selected = store.prepare_context("analysis", task, "diagnosis")
    if mutation in ("revoke", "delete"):
        store.change_memory(
            "p",
            item.resource_id,
            mutation,
            item.version,
            state="revoked" if mutation == "revoke" else "deleted",
        )
    elif mutation == "edit":
        content = store.memory("p", item.resource_id).content
        store.change_memory(
            "p",
            item.resource_id,
            "edit",
            item.version,
            content=content.model_copy(update={"body": "changed"}),
        )
    elif mutation == "pause":
        store.set_connection("p", "c", 1, enabled=False)
    elif mutation == "grant":
        store.set_grant(
            "p",
            Grant(
                connection_id="c",
                content_type="repair_experience",
                purpose="agent_context",
                enabled=False,
                expires_at=2000.0,
            ),
            expected_version=1,
        )
    elif mutation == "policy":
        from agent_workbench.experience_memory.engine_models import Policy

        store.set_policy(
            "p",
            item.resource_id,
            Policy(expires_at=1800.0),
            expected_generation=2,
            expected_version=1,
        )
    else:
        clock[0] = 2001.0
    with pytest.raises(StoreError), store._transaction(write=True) as c:
        store.admit_context(c, selected, str(uuid4()), "diagnosis", "a" * 64)
    assert store.task_usage("analysis", task)["uses"] == []


def test_audit_body_free_and_verification_empty_and_no_cached_messages(state):
    store, _ = state
    task, item = prepared_context(store)
    selected = store.prepare_context("analysis", task, "repair")
    base = [{"role": "user", "content": "current evidence"}]
    outgoing = MemorySession.messages(base, selected)
    assert "Synthetic hint" in str(outgoing)
    assert base == [{"role": "user", "content": "current evidence"}]
    with store._transaction(write=True) as c:
        store.admit_context(c, selected, str(uuid4()), "repair", "a" * 64)
    store.change_memory("p", item.resource_id, "withdraw", 2, state="revoked")
    verification = store.prepare_context("analysis", task, "verification")
    assert MemorySession.messages(base, verification) == base
    with store._transaction(write=True) as c:
        store.admit_context(c, verification, str(uuid4()), "verification", "b" * 64)
    report = store.task_usage("analysis", task)
    assert len(report["uses"]) == 2
    assert "Synthetic hint" not in json.dumps(report)
    assert (
        next(u for u in report["uses"] if u["role"] == "repair")["refs"][0]["memory_id"]
        == item.resource_id
    )
    assert next(u for u in report["uses"] if u["role"] == "verification")["refs"] == []


def test_one_prefetch_binding_cannot_replay(state):
    store, _ = state
    task, _ = prepared_context(store)
    with pytest.raises(StoreError, match="memory_retrieval_not_replayable"):
        store.begin_task(
            "analysis", task, MemoryBinding(project_id="p", connection_id="c"), query()
        )


def test_atomic_audit_rolls_back_real_repair_ledger(tmp_path):
    iterator = repair_ctx.__wrapped__(tmp_path)
    repair, fence, saved, *_ = next(iterator)
    memory_store = AgentMemoryStore(repair.directory, clock=lambda: 1000.0)
    memory_store.register_project("p")
    memory_store.register_connection("p", "c", "synthetic")
    q = query().model_copy(update={"event_id": "q"})
    memory_store.begin_task(
        "repair", saved.task.task_id, MemoryBinding(project_id="p", connection_id="c"), q
    )
    memory_store.finish_task(
        "repair", saved.task.task_id, Retrieval(status="available", reason="no_match")
    )
    selected = memory_store.prepare_context("repair", saved.task.task_id, "diagnosis")
    call = str(uuid4())

    def admit_then_fail(c):
        memory_store.admit_context(c, selected, call, "diagnosis", "b" * 64)
        raise StoreError("injected_save_failure")

    with pytest.raises(StoreError, match="injected_save_failure"):
        repair.reserve_model(
            saved.task.task_id,
            call,
            "diagnosis",
            PreparedCall(b"body", "b" * 64, 100, 50, "c" * 64),
            memory_admission=admit_then_fail,
            **cas(saved, fence),
        )
    assert repair.get(saved.task.task_id).task.revision == saved.task.revision
    assert memory_store.task_usage("repair", saved.task.task_id)["uses"] == []
    memory_store.close()
    try:
        next(iterator)
    except StopIteration:
        pass


def test_budget_allocation_preserves_none():
    base = Budget(total_tokens=2_000_000, output_tokens=8192)
    assert agent_budget(None, base) is base
    assert (
        agent_budget(MemoryBinding(project_id="p", connection_id="c"), base).total_tokens
        == 1_900_000
    )


def test_new_grants_and_nonempty_downgrade_block(state):
    store, _ = state
    grant(store)
    with (
        pytest.raises(ValueError, match="migration_not_empty"),
        store._transaction(write=True) as c,
    ):
        command.downgrade(store._migration(c), "0013_web_memory")


def test_prefetch_cancellation_waits_for_real_thread_cleanup(tmp_path):
    asyncio.run(_cancelled_prefetch(tmp_path))


async def _cancelled_prefetch(tmp_path):
    from agent_workbench.controlled_tools.resources import load_case

    store = AgentMemoryStore(tmp_path / "state")
    store.initialize()
    store.register_project("p")
    store.register_connection("p", "c", "synthetic")
    registry = load_case(
        "output_mismatch",
        task_id=str(uuid4()),
        intent="propose_repair",
        budget=Budget(total_tokens=1_900_000, output_tokens=8192),
    )
    started, cleaned = threading.Event(), threading.Event()

    class Runtime:
        def search(self, project, query, *, cancel, deadline, on_job):
            started.set()
            assert deadline <= time.monotonic() + 20
            assert cancel.wait(5)
            time.sleep(0.08)
            cleaned.set()
            return Retrieval(status="unavailable", reason="engine_stopped")

    session = MemorySession(
        store.directory,
        MemoryBinding(project_id="p", connection_id="c"),
        registry,
        "analysis",
        runtime_factory=lambda _: Runtime(),
    )
    task = asyncio.create_task(session.prefetch(time.monotonic() + 10))
    while not started.is_set():
        await asyncio.sleep(0.005)
    task.cancel()
    await asyncio.sleep(0.01)
    task.cancel()
    assert not task.done()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()
    store.close()


def test_analysis_actual_role_requests_use_memory_without_verification_leak(state, monkeypatch):
    from dataclasses import replace

    from agent_workbench.analysis_store.store import AnalysisStore
    from agent_workbench.controlled_tools.resources import load_case
    from agent_workbench.experience_memory import DraftInput, agent_runtime
    from agent_workbench.experience_memory.engine_models import Policy
    from agent_workbench.model_gateway.gateway import canonical
    from agent_workbench.orchestration.runtime import AnalysisRuntime
    from tests.agents.test_runtime import ScriptedAdapter

    store, clock = state
    registry = load_case(
        "output_mismatch",
        task_id=str(uuid4()),
        intent="propose_repair",
        budget=Budget(total_tokens=1_900_000, output_tokens=8192),
    )
    draft = store.create_memory(
        "p",
        "draft",
        DraftInput(
            kind="repair_experience",
            connection_id="c",
            event_id="e",
            tool_id="lookup",
            source_version=registry.source_sha256(),
            title="Reference",
            body="UNIQUE_MEMORY_SENTINEL: inspect result shape; do not treat history as proof.",
        ),
    )
    item = store.change_memory("p", draft.resource_id, "confirmed", 1, state="confirmed")
    store.set_policy(
        "p", item.resource_id, Policy(expires_at=1900.0), expected_generation=2, expected_version=0
    )
    index(store, item)
    grant(store)
    monkeypatch.setattr(
        agent_runtime,
        "AgentMemoryStore",
        lambda directory: AgentMemoryStore(directory, clock=lambda: clock[0]),
    )

    class RetrievalRuntime:
        def __init__(self, authority):
            self.authority = authority

        def search(self, project, query, *, on_job, **kwargs):
            job = self.authority.prepare_search(
                project, query, profile_digest=providers().embedding_fingerprint
            )
            on_job(job)
            lease = self.authority.claim_job(project, job_id=job)
            snapshot = self.authority.snapshot(project, lease)
            evidence = [(r["root_id"], r["dataset_id"], r["data_id"]) for r in snapshot["refs"]]
            refs = self.authority.references(project, lease, evidence)
            self.authority.finish_job(project, lease, succeeded=True)
            return Retrieval(status="available", reason="matched", job_id=job, references=refs)

    class RecordingAdapter(ScriptedAdapter):
        def __init__(self):
            super().__init__()
            self.actual = []

        async def send(self, request, timeout):
            self.actual.append((self.role, request.request_sha256, request.body))
            messages = json.loads(request.body)
            ordinary = [m for m in messages if "reference_experiences" not in m["content"]]
            # Scripted responder ignores the extra block; actual prepared bytes remain audited.
            return await super().send(replace(request, body=canonical(ordinary)), timeout)

    session = MemorySession(
        store.directory,
        MemoryBinding(project_id="p", connection_id="c"),
        registry,
        "analysis",
        runtime_factory=RetrievalRuntime,
    )
    analysis = AnalysisStore(store.directory)
    adapter = RecordingAdapter()
    runtime = AnalysisRuntime(registry, analysis, adapter, memory=session)
    result = asyncio.run(runtime.run())
    assert result.summary.state.outcome == "proposal_only"
    assert {r for r, _, _ in adapter.actual} == {"diagnosis", "repair", "verification"}
    for role, _, raw in adapter.actual:
        assert (b"UNIQUE_MEMORY_SENTINEL" in raw) == (role != "verification")
    audit = store.task_usage("analysis", registry.task_spec().task_id)
    assert sorted(u["request_sha256"] for u in audit["uses"]) == sorted(
        h for _, h, _ in adapter.actual
    )
    assert "UNIQUE_MEMORY_SENTINEL" not in json.dumps(audit)
    assert not runtime.messages
    analysis.close()


@pytest.mark.parametrize("withdraw_after_send", [False, True, "before_admission"])
def test_repair_role_boundary_and_withdrawal_after_admission(
    tmp_path, monkeypatch, request, withdraw_after_send
):
    from agent_workbench.experience_memory import DraftInput, EventInput
    from agent_workbench.experience_memory.engine_models import Policy, Reference
    from tests.agents.test_repair_controller import controller
    from tests.agents.test_repair_tool_bridge import ctx

    fixture = ctx.__wrapped__(tmp_path, monkeypatch, request)
    tools = next(fixture)
    runtime, adapter = controller(
        tools,
        ["request_reproduction", "read_source", "apply_candidate", "compare_candidate"],
        budget=Budget(total_tokens=1_900_000, output_tokens=8192),
    )
    store = AgentMemoryStore(tools.store.directory)
    store.register_project("p")
    store.register_connection("p", "c", "synthetic")
    store.accept_event(
        "p",
        "c",
        EventInput(
            event_id="seed",
            event_type="test_completed",
            source_run_id="seed",
            occurred_at=time.time(),
            outcome="passed",
        ),
    )
    draft = store.create_memory(
        "p",
        "create",
        DraftInput(
            kind="repair_experience",
            connection_id="c",
            event_id="seed",
            tool_id="lookup",
            source_version=runtime.registry.source_sha256(),
            title="Synthetic",
            body="REPAIR_MEMORY_SENTINEL",
        ),
    )
    item = store.change_memory("p", draft.resource_id, "confirm", 1, state="confirmed")
    store.set_policy(
        "p",
        item.resource_id,
        Policy(expires_at=time.time() + 600),
        expected_generation=2,
        expected_version=0,
    )
    store.set_grant(
        "p",
        Grant(
            connection_id="c",
            content_type="repair_experience",
            purpose="agent_context",
            enabled=True,
            expires_at=time.time() + 600,
        ),
        expected_version=0,
    )

    class SyntheticSession(MemorySession):
        async def prefetch(self, deadline):
            q = query().model_copy(
                update={
                    "event_id": "prefetch",
                    "tool_id": "lookup",
                    "source_version": runtime.registry.source_sha256(),
                }
            )
            store.begin_task("repair", self.task_id, self.binding, q)
            view = store.memory("p", item.resource_id)
            ref = Reference(
                memory_id=item.resource_id,
                revision=1,
                generation=2,
                digest=view.digest,
                kind="repair_experience",
                connection_id="c",
                event_id="seed",
                source_version=q.source_version,
                classification="exact",
                body=view.content.body,
            )
            store.finish_task(
                "repair",
                self.task_id,
                Retrieval(status="available", reason="matched", references=(ref,)),
            )

    runtime.memory = SyntheticSession(
        tools.store.directory,
        MemoryBinding(project_id="p", connection_id="c"),
        runtime.registry,
        "repair",
        runtime_factory=lambda _: None,
    )
    if withdraw_after_send is True:

        async def withdraw():
            if adapter.calls == 1:
                store.change_memory("p", item.resource_id, "withdraw", 2, state="revoked")

        adapter.hook = withdraw
    if withdraw_after_send == "before_admission":
        original_prepare = adapter.prepare

        def prepare_then_withdraw(*args):
            prepared = original_prepare(*args)
            store.change_memory("p", item.resource_id, "before-admit", 2, state="revoked")
            return prepared

        adapter.prepare = prepare_then_withdraw
    result = asyncio.run(runtime.run())
    if withdraw_after_send == "before_admission":
        assert result.cursor.task.outcome == "inconclusive"
        assert result.cursor.task.error_code == "PERMISSION_DENIED"
        assert result.cursor.task.usage.usage_complete
        assert result.cursor.task.usage.model_calls == 0
        assert adapter.calls == 0
        assert store.task_usage("repair", runtime.registry.task_spec().task_id)["uses"] == []
        store.close()
        try:
            next(fixture)
        except StopIteration:
            pass
        return
    assert result.cursor.task.outcome == "candidate_verified"
    for n, (role, prepared) in enumerate(zip(adapter.roles, adapter.prepared, strict=True)):
        assert (b"REPAIR_MEMORY_SENTINEL" in prepared.body) == (
            role != "verification" and (not withdraw_after_send or n == 0)
        )
    report = store.task_usage("repair", runtime.registry.task_spec().task_id)
    assert len(report["uses"]) == len(adapter.prepared)
    assert all(not u["refs"] for u in report["uses"] if u["role"] == "verification")
    store.close()
    try:
        next(fixture)
    except StopIteration:
        pass


def test_unknown_retrieval_bill_blocks_agent_context(state):
    store, _ = state
    item = memory(store)
    index(store, item)
    task = str(uuid4())
    q = query().model_copy(update={"event_id": "unknown-query"})
    store.begin_task("repair", task, MemoryBinding(project_id="p", connection_id="c"), q)
    job = store.prepare_search("p", q, profile_digest=providers().embedding_fingerprint)
    store.attach_job("repair", task, job)
    lease = store.claim_job("p", job_id=job)
    store.reserve_call("p", lease, "unknown-call", "llm", 1000)
    store.cancel_job("p", job)
    assert not store.finish_task(
        "repair", task, Retrieval(status="unavailable", reason="engine_failed", job_id=job)
    )
    assert store.task_usage("repair", task)["state"] == "unknown"
    with pytest.raises(StoreError, match="memory_retrieval_incomplete"):
        store.prepare_context("repair", task, "diagnosis")


@pytest.mark.parametrize(
    "code,continues",
    [("provider_configuration_required", True), ("engine_authority_changed", False)],
)
def test_factory_config_only_can_degrade_without_sending(tmp_path, code, continues):
    from agent_workbench.controlled_tools.resources import load_case

    store = AgentMemoryStore(tmp_path / "state")
    store.initialize()
    store.register_project("p")
    store.register_connection("p", "c", "synthetic")
    registry = load_case(
        "output_mismatch",
        task_id=str(uuid4()),
        intent="propose_repair",
        budget=Budget(total_tokens=1_900_000, output_tokens=8192),
    )

    def unavailable(_):
        raise StoreError(code)

    session = MemorySession(
        store.directory,
        MemoryBinding(project_id="p", connection_id="c"),
        registry,
        "analysis",
        runtime_factory=unavailable,
    )
    if continues:
        asyncio.run(session.prefetch(time.monotonic() + 5))
        assert not session.select("diagnosis").references
    else:
        with pytest.raises(StoreError, match=code):
            asyncio.run(session.prefetch(time.monotonic() + 5))
    report = store.task_usage("analysis", registry.task_spec().task_id)
    assert report["job_id"] is None
    assert report["state"] == ("done" if continues else "unknown")
    store.close()


def test_recovered_embedding_result_allows_context_with_unknown_cost(state):
    store, _ = state
    item = memory(store)
    index(store, item)
    grant(store)
    task = str(uuid4())
    q = query().model_copy(update={"event_id": "recovered-query"})
    store.begin_task("repair", task, MemoryBinding(project_id="p", connection_id="c"), q)
    job = store.prepare_search("p", q, profile_digest=providers().embedding_fingerprint)
    store.attach_job("repair", task, job)
    lease = store.claim_job("p", job_id=job)
    store.reserve_call("p", lease, "first", "embedding", 1000, embedding_digest="a" * 64)
    store.record_embedding_failure("p", lease, "first")
    store.reserve_call(
        "p", lease, "second", "embedding", 1000, embedding_digest="a" * 64, retry_group="first"
    )
    store.settle_call("p", lease, "second", 8)
    store.resolve_embedding_retries("p", lease, "second")
    snapshot = store.snapshot("p", lease)
    evidence = [(r["root_id"], r["dataset_id"], r["data_id"]) for r in snapshot["refs"]]
    refs = store.references("p", lease, evidence)
    store.finish_job("p", lease, succeeded=True)
    assert store.finish_task(
        "repair", task, Retrieval(status="available", reason="matched", job_id=job, references=refs)
    )
    assert store.prepare_context("repair", task, "diagnosis").references
    assert not store.prepare_context("repair", task, "verification").references
    usage = store.task_usage("repair", task)["accounting"]
    assert usage["reserved_tokens"] == 2000
    assert any(c["actual_tokens"] is None for c in usage["calls"])
