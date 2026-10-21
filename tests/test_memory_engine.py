"""M2-R: real SQLite authority under adversarial state changes, no provider calls."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_workbench.experience_memory import DraftInput, EventInput
from agent_workbench.experience_memory.engine_models import Grant, Policy, Query
from agent_workbench.experience_memory.engine_store import EngineStore
from agent_workbench.providers.cognee.gateway import Gateway, Providers
from agent_workbench.providers.cognee.runtime import CogneeRuntime, private_tree
from agent_workbench.run_store import StoreError


def prepare_index(store, project, memory_id):
    return store.prepare_index(project, memory_id, profile_digest=providers().embedding_fingerprint)


def prepare_search(store, project, q):
    return store.prepare_search(project, q, profile_digest=providers().embedding_fingerprint)


@pytest.fixture
def state(tmp_path):
    clock = [1000.0]
    store = EngineStore(tmp_path / "db", clock=lambda: clock[0])
    store.initialize()
    store.register_project("p")
    store.register_project("other")
    store.register_connection("p", "c", "synthetic")
    store.accept_event(
        "p",
        "c",
        EventInput(
            event_id="e",
            event_type="test_completed",
            source_run_id="r",
            occurred_at=900.0,
            outcome="failed",
        ),
    )
    for kind in ("query", "repair_experience", "project_constraint"):
        for purpose in ("index", "retrieve"):
            store.set_grant(
                "p",
                Grant(
                    connection_id="c",
                    content_type=kind,
                    purpose=purpose,
                    enabled=True,
                    expires_at=2000.0,
                ),
                expected_version=0,
            )
    yield store, clock
    store.close()


def memory(store, suffix="a", kind="repair_experience", historical=True, conflict=None):
    draft = store.create_memory(
        "p",
        "create_" + suffix,
        DraftInput(
            kind=kind,
            connection_id="c",
            event_id="e",
            tool_id="tool",
            source_version="v1",
            title="Synthetic",
            body="Synthetic hint " + suffix,
        ),
    )
    item = store.change_memory("p", draft.resource_id, "confirm_" + suffix, 1, state="confirmed")
    store.set_policy(
        "p",
        item.resource_id,
        Policy(
            expires_at=1900.0,
            allow_historical=historical,
            conflict_key="rule" if conflict else None,
            conflict_value=conflict,
        ),
        expected_generation=2,
        expected_version=0,
    )
    return item


def index(store, item, finish=True):
    job = prepare_index(store, "p", item.resource_id)
    lease = store.claim_job("p", job_id=job)
    store.publish_index("p", lease, "dataset_" + job, "data_" + job)
    if finish:
        store.finish_job("p", lease, succeeded=True)
    return lease


def query(version="v1", historical=False):
    return Query(
        connection_id="c",
        event_id="e",
        tool_id="tool",
        source_version=version,
        text="Synthetic failing tool",
        allow_historical=historical,
    )


def search_lease(store, q=None):
    job = prepare_search(store, "p", q or query())
    return store.claim_job("p", job_id=job)


def providers():
    return Providers(
        "http://127.0.0.1:9/chat",
        "test-key",
        "configured-model",
        "http://127.0.0.1:9/embed",
        "test-embedding-key",
        "bge-m3",
        True,
    )


@pytest.mark.parametrize(
    "change", ["pause", "revoke", "delete", "grant", "policy", "expire", "tamper"]
)
def test_no_egress_after_authority_change(state, change):
    store, clock = state
    item = memory(store)
    job = prepare_index(store, "p", item.resource_id)
    lease = store.claim_job("p", job_id=job)
    if change == "pause":
        store.set_connection("p", "c", 1, enabled=False)
    elif change in ("revoke", "delete"):
        store.change_memory(
            "p", item.resource_id, "change", 2, state="revoked" if change == "revoke" else "deleted"
        )
    elif change == "grant":
        store.set_grant(
            "p",
            Grant(
                connection_id="c",
                content_type="repair_experience",
                purpose="index",
                enabled=False,
                expires_at=2000.0,
            ),
            expected_version=1,
        )
    elif change == "policy":
        store.set_policy(
            "p",
            item.resource_id,
            Policy(expires_at=1800.0),
            expected_generation=2,
            expected_version=1,
        )
    elif change == "expire":
        clock[0] = 1901.0
    else:
        with store._transaction(write=True) as c:
            c.exec_driver_sql("UPDATE memory_revisions SET digest=?", ("0" * 64,))
    with pytest.raises(StoreError):
        store.reserve_call("p", lease, "wire", "embedding", 100)
    assert store.accounting("p", job)["calls"] == []


def test_scoped_grants_cas_and_unconfirmed(state):
    store, _ = state
    item = memory(store)
    with pytest.raises(StoreError, match="not_found"):
        prepare_index(store, "other", item.resource_id)
    with pytest.raises(StoreError, match="version_conflict"):
        store.set_grant(
            "p",
            Grant(
                connection_id="c",
                content_type="query",
                purpose="retrieve",
                enabled=True,
                expires_at=3000.0,
            ),
            expected_version=0,
        )
    store.change_memory("p", item.resource_id, "revoke", 2, state="revoked")
    with pytest.raises(StoreError, match="memory_ineligible"):
        prepare_index(store, "p", item.resource_id)


@pytest.mark.parametrize(
    "version,historical,policy,expected",
    [
        ("v1", False, False, "exact"),
        ("v2", True, True, "historical_hint"),
        ("v2", False, True, None),
        ("v2", True, False, None),
    ],
)
def test_reference_classification(state, version, historical, policy, expected):
    store, _ = state
    item = memory(store, historical=policy)
    indexed = index(store, item)
    lease = search_lease(store, query(version, historical))
    refs = store.snapshot("p", lease)["refs"]
    assert len(refs) == (1 if expected else 0)
    if expected:
        r = refs[0]
        result = store.references("p", lease, [(indexed.job_id, r["dataset_id"], r["data_id"])])
        assert result[0].classification == expected
        assert result[0].body == "Synthetic hint a"
    with pytest.raises(StoreError, match="unmapped_evidence"):
        store.references("p", lease, [("wrong", "wrong", "wrong")])


def test_full_scope_conflict_before_semantic_topk(state):
    store, _ = state
    for suffix, value in (("a", "integer"), ("b", "string")):
        index(store, memory(store, suffix, kind="project_constraint", conflict=value))
    with pytest.raises(StoreError, match="scope_conflict"):
        search_lease(store)


def test_scope_changes_block_frozen_query_reservation(state):
    store, _ = state
    index(store, memory(store))
    lease = search_lease(store)
    index(store, memory(store, "new"))
    with pytest.raises(StoreError, match="references_changed"):
        store.reserve_call("p", lease, "wire", "embedding", 100)


def test_published_but_unfinished_index_not_retrievable(state):
    store, _ = state
    item = memory(store)
    indexed = index(store, item, finish=False)
    with pytest.raises(StoreError, match="index_required"):
        search_lease(store)
    store.cancel_job("p", indexed.job_id)
    assert indexed.job_id in store.roots_for_cleanup("p")


def test_revoke_after_query_preparation_blocks_cached_result(state):
    store, _ = state
    item = memory(store)
    indexed = index(store, item)
    lease = search_lease(store)
    r = store.snapshot("p", lease)["refs"][0]
    store.change_memory("p", item.resource_id, "revoked", 2, state="revoked")
    with pytest.raises(StoreError, match="references_changed"):
        store.references("p", lease, [(indexed.job_id, r["dataset_id"], r["data_id"])])


@pytest.mark.parametrize("status,usage", [(500, None), (200, None), (200, True), (200, 1000000)])
def test_wire_unknown_stops_followups_and_retains_cost(state, monkeypatch, status, usage):
    import json

    store, _ = state
    item = memory(store)
    lease = store.claim_job("p", job_id=prepare_index(store, "p", item.resource_id))
    gate = Gateway(store, "p", lease, providers(), time.monotonic() + 5)
    calls = []

    async def response(*args):
        calls.append(args)
        return status, json.dumps({"usage": {"total_tokens": usage}}).encode()

    monkeypatch.setattr(gate, "request", response)
    assert gate.forward("/v1/embeddings", b'{"input":"synthetic"}')[0] == 503
    assert gate.forward("/v1/embeddings", b'{"input":"synthetic"}')[0] == 503
    assert len(calls) == 1
    bill = store.accounting("p", lease.job_id)
    assert bill["state"] == "unknown" and len(bill["calls"]) == 1
    assert bill["calls"][0]["actual_tokens"] == (usage if type(usage) is int else None)


def test_wire_success_counts_retries_and_masks_secrets(state, monkeypatch):
    store, _ = state
    lease = store.claim_job("p", job_id=prepare_index(store, "p", memory(store).resource_id))
    gate = Gateway(store, "p", lease, providers(), time.monotonic() + 10)

    async def response(*args):
        import json

        return 200, json.dumps(
            {"usage": {"total_tokens": 20}, "data": [{"embedding": [0.1] * 1024}]}
        ).encode()

    monkeypatch.setattr(gate, "request", response)
    for _ in range(3):
        assert gate.forward("/v1/embeddings", b'{"input":"synthetic"}')[0] == 200
    bill = store.accounting("p", lease.job_id)
    assert len(bill["calls"]) == 3
    assert bill["reserved_tokens"] > 60
    assert "test-key" not in repr(gate.providers)
    assert bill["monetary_cost"] == "unknown"


def test_reservation_race_and_grant_revoke_retains_late_bill(state):
    store, _ = state
    lease = store.claim_job("p", job_id=prepare_index(store, "p", memory(store).resource_id))
    with ThreadPoolExecutor(2) as pool:
        calls = list(
            pool.map(lambda _: store.reserve_call("p", lease, "same", "llm", 100), range(2))
        )
    assert sum(call.admitted for call in calls) == 1
    store.set_grant(
        "p",
        Grant(
            connection_id="c",
            content_type="repair_experience",
            purpose="index",
            enabled=False,
            expires_at=2000.0,
        ),
        expected_version=1,
    )
    assert store.settle_call("p", lease, "same", 50).actual_tokens == 50
    with pytest.raises(StoreError, match="authorization_required"):
        store.reserve_call("p", lease, "new", "llm", 100)


def test_private_tree_and_writer_lock(state, tmp_path):
    store, _ = state
    runtime = CogneeRuntime(store, tmp_path / "engine", Path(os.sys.executable), providers())
    with runtime.writer():
        with pytest.raises(StoreError, match="engine_busy"), runtime.writer():
            pass
    link = runtime.directory / "link"
    link.symlink_to(tmp_path / "db")
    with pytest.raises(StoreError, match="unsafe_engine_permissions"):
        private_tree(runtime.directory)
    link.unlink()
    (runtime.directory / "public").write_text("unsafe")
    (runtime.directory / "public").chmod(0o644)
    with pytest.raises(StoreError, match="unsafe_engine_permissions"):
        private_tree(runtime.directory)


def test_cleanup_partial_index_and_revoked_source_is_idempotent(state, tmp_path):
    store, _ = state
    runtime = CogneeRuntime(store, tmp_path / "engine", Path(os.sys.executable), providers())
    item = memory(store)
    job = prepare_index(store, "p", item.resource_id)
    root = runtime.directory / job
    root.mkdir(mode=0o700)
    store.set_connection("p", "c", 1, enabled=False)
    assert runtime.cleanup("p") == 1
    assert not root.exists()
    assert runtime.cleanup("p") == 0


def test_runtime_search_refuses_missing_index_without_subprocess(state, tmp_path, monkeypatch):
    store, _ = state
    memory(store)
    runtime = CogneeRuntime(store, tmp_path / "engine", Path(os.sys.executable), providers())

    def unexpected(*args):
        pytest.fail("must not enter engine")

    monkeypatch.setattr(runtime, "_worker", unexpected)
    result = runtime.search("p", query())
    assert result.status == "limited" and result.reason == "index_required"
    assert not result.references and result.current_verification is False


def test_pre_cancelled_index_does_not_execute(state, tmp_path, monkeypatch):
    store, _ = state
    runtime = CogneeRuntime(store, tmp_path / "engine", Path(os.sys.executable), providers())
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(StoreError, match="index_failed"):
        runtime.index("p", memory(store).resource_id, cancel=cancel)


@pytest.mark.parametrize("change", ["query_grant", "source", "policy", "grant_expiry"])
def test_review_cached_query_revalidates_all_authority(state, change):
    store, clock = state
    item = memory(store)
    indexed = index(store, item)
    lease = search_lease(store)
    ref = store.snapshot("p", lease)["refs"][0]
    if change == "query_grant":
        store.set_grant(
            "p",
            Grant(
                connection_id="c",
                content_type="query",
                purpose="retrieve",
                enabled=False,
                expires_at=2000.0,
            ),
            expected_version=1,
        )
    elif change == "source":
        store.set_connection("p", "c", 1, enabled=False)
        store.set_connection("p", "c", 2, enabled=True)
    elif change == "policy":
        store.set_policy(
            "p",
            item.resource_id,
            Policy(expires_at=1800.0),
            expected_generation=2,
            expected_version=1,
        )
    else:
        # Keep policy live while the independent query grant expires.
        store.set_grant(
            "p",
            Grant(
                connection_id="c",
                content_type="query",
                purpose="retrieve",
                enabled=True,
                expires_at=1001.0,
            ),
            expected_version=1,
        )
        lease = search_lease(store)
        clock[0] = 1002.0
    with pytest.raises(StoreError):
        store.reserve_call("p", lease, "wire", "embedding", 100)
    with pytest.raises(StoreError):
        store.references("p", lease, [(indexed.job_id, ref["dataset_id"], ref["data_id"])])
    assert store.accounting("p", lease.job_id)["calls"] == []


def test_review_scope_limit_prevents_partial_reference_set(state):
    store, _ = state
    for number in range(9):
        index(store, memory(store, str(number)))
    with pytest.raises(StoreError, match="scope_limit"):
        search_lease(store)


def test_review_context_bytes_limit_is_atomic(state):
    store, _ = state
    original = memory(store)
    content = store.memory("p", original.resource_id).content
    edited = store.change_memory(
        "p",
        original.resource_id,
        "edit",
        2,
        content=content.model_copy(update={"body": "中" * 3000}),
    )
    confirmed = store.change_memory(
        "p", original.resource_id, "confirm_again", edited.version, state="confirmed"
    )
    store.set_policy(
        "p",
        original.resource_id,
        Policy(expires_at=1900.0),
        expected_generation=confirmed.version,
        expected_version=1,
    )
    indexed = index(store, confirmed)
    lease = search_lease(store)
    ref = store.snapshot("p", lease)["refs"][0]
    with pytest.raises(StoreError, match="context_limit"):
        store.references("p", lease, [(indexed.job_id, ref["dataset_id"], ref["data_id"])])


def test_review_unknown_index_bill_never_becomes_retrievable(state):
    store, _ = state
    item = memory(store)
    job = prepare_index(store, "p", item.resource_id)
    lease = store.claim_job("p", job_id=job)
    store.reserve_call("p", lease, "wire", "llm", 100)
    store.publish_index("p", lease, "dataset", "data")
    with pytest.raises(StoreError, match="usage_unsettled"):
        store.finish_job("p", lease, succeeded=True)
    store.settle_call("p", lease, "wire", None)
    with pytest.raises(StoreError, match="index_required"):
        search_lease(store)
    assert store.accounting("p", job)["state"] == "unknown"
    assert job in store.roots_for_cleanup("p")


def test_review_0011_upgrade_preserves_populated_authority(tmp_path):
    from alembic import command

    from agent_workbench.run_store.migrations.versions.v0011_memory_authority import TABLES

    store = EngineStore(tmp_path / "db")
    store.initialize()
    store.register_project("p")
    store.register_connection("p", "c", "synthetic")
    store.accept_event(
        "p",
        "c",
        EventInput(
            event_id="e",
            event_type="test_completed",
            source_run_id="r",
            occurred_at=900.0,
            outcome="failed",
        ),
    )
    store.create_memory(
        "p",
        "create",
        DraftInput(
            kind="repair_experience",
            connection_id="c",
            event_id="e",
            tool_id="tool",
            source_version="v1",
            title="Synthetic",
            body="Synthetic migration evidence",
        ),
    )
    with store._transaction(write=True) as c:
        before = {table: c.exec_driver_sql(f"SELECT * FROM {table}").all() for table in TABLES}
        command.downgrade(store._migration(c), "0011_memory_authority")
    store.initialize()
    with store._transaction() as c:
        after = {table: c.exec_driver_sql(f"SELECT * FROM {table}").all() for table in TABLES}
        assert (
            c.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
            == "0015_embedding_retries"
        )
    assert before == after
    store.close()


def test_review_failure_reason_whitelist_scope_and_first_reason(state):
    store, _ = state
    job = prepare_index(store, "p", memory(store).resource_id)
    lease = store.claim_job("p", job_id=job)
    with pytest.raises(StoreError, match="invalid_failure_code"):
        store.record_failure("p", lease, "raw-exception-with-private-data")
    with pytest.raises(StoreError, match="not_found"):
        store.record_failure("other", lease, "engine_failed")
    with pytest.raises(StoreError, match="lease_conflict"):
        store.record_failure("p", lease.model_copy(update={"token": "wrong"}), "engine_failed")
    assert store.accounting("p", job)["failure_code"] is None
    store.reserve_call("p", lease, "wire", "embedding", 100)
    store.settle_call("p", lease, "wire", 20)
    store.record_failure("p", lease, "embedding_invalid")
    store.record_failure("p", lease, "engine_failed")
    bill = store.accounting("p", job)
    assert bill["failure_code"] == "embedding_invalid"
    assert bill["calls"][0]["actual_tokens"] == 20


@pytest.mark.parametrize(
    "error,expected",
    [
        (
            {"type": "new_api_error", "code": "do_request_failed", "message": "private-key"},
            "provider_upstream_request_failed",
        ),
        (
            {"type": "other", "code": "do_request_failed", "message": "private-key"},
            "provider_http_error",
        ),
    ],
)
def test_gateway_classifies_upstream_failure_with_bounded_retry(
    state, monkeypatch, error, expected
):
    import json

    store, _ = state
    lease = store.claim_job("p", job_id=prepare_index(store, "p", memory(store).resource_id))
    gate = Gateway(store, "p", lease, providers(), time.monotonic() + 10)
    calls = []

    async def response(*args):
        calls.append(1)
        return 500, json.dumps({"error": error}).encode()

    monkeypatch.setattr(gate, "request", response)
    for _ in range(2):
        status, body = gate.forward("/v1/embeddings", b'{"input":"synthetic"}')
        assert status == 503 and b"private-key" not in body
    assert calls == [1] * (3 if expected == "provider_upstream_request_failed" else 1)
    bill = store.accounting("p", lease.job_id)
    assert bill["failure_code"] == expected
    assert bill["calls"][0]["actual_tokens"] is None
    assert "private-key" not in json.dumps(bill)


@pytest.mark.parametrize("failure", ["engine_cognify_failed", "engine_tokenizer_cache_missing"])
def test_runtime_index_persists_safe_worker_failure(state, tmp_path, monkeypatch, failure):
    from agent_workbench.providers.cognee.tokenizer_cache import WORKER_FAILURES

    assert failure in WORKER_FAILURES
    store, _ = state
    runtime = CogneeRuntime(store, tmp_path / "engine", Path(__file__), providers())
    jobs = []

    def broken(*args):
        raise StoreError(failure)

    monkeypatch.setattr(runtime, "_worker", broken)
    with pytest.raises(StoreError, match="index_failed"):
        runtime.index("p", memory(store).resource_id, on_job=jobs.append)
    assert store.accounting("p", jobs[0])["failure_code"] == failure
    assert store.accounting("p", jobs[0])["calls"] == []


def test_embedding_retry_success_keeps_unknown_bill_and_allows_finish(state, monkeypatch):
    import json

    store, _ = state
    lease = store.claim_job("p", job_id=prepare_index(store, "p", memory(store).resource_id))
    gate = Gateway(store, "p", lease, providers(), time.monotonic() + 10)
    calls = []

    async def response(*args):
        calls.append(args[-1])
        if len(calls) == 1:
            return 500, b'{"error":{"type":"new_api_error","code":"do_request_failed"}}'
        return 200, json.dumps(
            {"usage": {"total_tokens": 4}, "data": [{"embedding": [0.1] * 1024}]}
        ).encode()

    monkeypatch.setattr(gate, "request", response)
    assert gate.forward("/v1/embeddings", b'{"input":"synthetic"}')[0] == 200
    assert len(calls) == 2 and calls[0] == calls[1]
    store.finish_job("p", lease, succeeded=True)
    bill = store.accounting("p", lease.job_id)
    assert bill["state"] == "done"
    failed = next(c for c in bill["calls"] if c["state"] == "unknown")
    succeeded = next(c for c in bill["calls"] if c["state"] == "received")
    assert failed["actual_tokens"] is None and failed["recovered_by"] == succeeded["call_id"]
    assert bill["reserved_tokens"] == sum(c["reserved_tokens"] for c in bill["calls"])


@pytest.mark.parametrize(
    "change", ["cancel", "revoke", "deadline", "budget", "bad_vector", "timeout"]
)
def test_embedding_retry_interruption_and_invalid_response_never_publish(
    state, monkeypatch, change
):
    import json

    store, _ = state
    lease = store.claim_job("p", job_id=prepare_index(store, "p", memory(store).resource_id))
    gate = Gateway(store, "p", lease, providers(), time.monotonic() + 10)
    calls = []

    async def response(*args):
        calls.append(1)
        if len(calls) == 1:
            if change == "cancel":
                gate.stopped.set()
            if change == "revoke":
                store.set_connection("p", "c", 1, enabled=False)
            if change == "deadline":
                gate.deadline = time.monotonic() - 1
            if change == "budget":
                with store._transaction(write=True) as c:
                    c.exec_driver_sql(
                        "UPDATE memory_jobs SET tokens=99999 WHERE job_id=?", (lease.job_id,)
                    )
            return 500, b'{"error":{"type":"new_api_error","code":"do_request_failed"}}'
        if change == "timeout":
            raise TimeoutError("private")
        return 200, json.dumps(
            {"usage": {"total_tokens": 4}, "data": [{"embedding": [0.1]}]}
        ).encode()

    monkeypatch.setattr(gate, "request", response)
    assert gate.forward("/v1/embeddings", b'{"input":"synthetic"}')[0] == 503
    assert len(calls) == (2 if change in {"bad_vector", "timeout"} else 1)
    bill = store.accounting("p", lease.job_id)
    assert all(c["recovered_by"] is None for c in bill["calls"])
    with pytest.raises(StoreError):
        store.finish_job("p", lease, succeeded=True)


def test_embedding_retry_bound_to_payload_and_three_attempts(state):
    store, _ = state
    lease = store.claim_job("p", job_id=prepare_index(store, "p", memory(store).resource_id))
    store.reserve_call("p", lease, "first", "embedding", 100, embedding_digest="a" * 64)
    store.record_embedding_failure("p", lease, "first")
    for digest, group in [("b" * 64, "first"), ("a" * 64, "other")]:
        with pytest.raises(StoreError, match="invalid_retry"):
            store.reserve_call(
                "p", lease, "wrong", "embedding", 100, embedding_digest=digest, retry_group=group
            )
    with pytest.raises(StoreError, match="usage_unsettled"):
        store.reserve_call("p", lease, "unrelated", "llm", 100)
    for name in ["second", "third"]:
        store.reserve_call(
            "p", lease, name, "embedding", 100, embedding_digest="a" * 64, retry_group="first"
        )
        store.record_embedding_failure("p", lease, name)
    with pytest.raises(StoreError, match="invalid_retry"):
        store.reserve_call(
            "p", lease, "fourth", "embedding", 100, embedding_digest="a" * 64, retry_group="first"
        )
    assert len(store.accounting("p", lease.job_id)["calls"]) == 3


def test_retry_migration_preserves_legacy_unknown_and_cannot_downgrade_used_ledger(state):
    from alembic import command

    store, _ = state
    lease = store.claim_job("p", job_id=prepare_index(store, "p", memory(store).resource_id))
    store.reserve_call("p", lease, "legacy", "embedding", 100)
    store.settle_call("p", lease, "legacy", None)
    with store._transaction(write=True) as c:
        command.downgrade(store._migration(c), "0014_agent_memory")
    store.initialize()
    bill = store.accounting("p", lease.job_id)
    assert bill["calls"][0]["actual_tokens"] is None
    assert bill["calls"][0]["failure_code"] is None
    assert bill["calls"][0]["recovered_by"] is None
    assert store.claim_job("p", job_id=lease.job_id) is None
    next_lease = store.claim_job(
        "p", job_id=prepare_index(store, "p", memory(store, "next").resource_id)
    )
    store.reserve_call("p", next_lease, "first", "embedding", 100, embedding_digest="a" * 64)
    with pytest.raises(ValueError, match="migration_not_empty"):
        with store._transaction(write=True) as c:
            command.downgrade(store._migration(c), "0014_agent_memory")


def test_retry_duplicate_receipt_cannot_change_digest(state):
    store, _ = state
    lease = store.claim_job("p", job_id=prepare_index(store, "p", memory(store).resource_id))
    store.reserve_call("p", lease, "first", "embedding", 100, embedding_digest="a" * 64)
    with pytest.raises(StoreError, match="idempotency_conflict"):
        store.reserve_call("p", lease, "first", "embedding", 100, embedding_digest="b" * 64)
