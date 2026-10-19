"""M1-R real SQLite lease, accounting and source projection gates."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_workbench.experience_memory import DraftInput, EventInput, MemoryStore
from agent_workbench.run_store import StoreError


@pytest.fixture
def setup(tmp_path):
    clock = [1000.0]
    store = MemoryStore(tmp_path / "db", clock=lambda: clock[0])
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
    yield store, clock
    store.close()


def source():
    return DraftInput(
        kind="repair_experience",
        connection_id="c",
        event_id="e",
        tool_id="tool",
        source_version="v1",
        title="Synthetic",
        body="Synthetic repair hint",
    )


def ready_projection(store):
    event_lease = store.claim_job("p")
    store.finish_job("p", event_lease, succeeded=True)
    item = store.create_memory("p", "create", source())
    assert store.claim_job("p") is None  # draft may not trigger processing
    item = store.change_memory("p", item.resource_id, "confirm", 1, state="confirmed")
    lease = store.claim_job("p")
    return item, lease


def test_lease_recovery_fencing_and_attempt_limit(setup):
    store, clock = setup
    first = store.claim_job("p")
    assert store.claim_job("p") is None
    clock[0] += 20
    store.renew_job("p", first)
    clock[0] += 20
    assert store.claim_job("p") is None
    clock[0] += 11
    second = store.claim_job("p")
    assert second.attempt == 2 and second.token != first.token
    with pytest.raises(StoreError, match="lease_conflict"):
        store.finish_job("p", first, succeeded=True)
    clock[0] += 31
    third = store.claim_job("p")
    assert third.attempt == 3
    clock[0] += 31
    assert store.claim_job("p") is None
    assert store.job_status("p", first.job_id) == "failed"


def test_reservation_replay_is_not_permission_to_send_again(setup):
    store, _ = setup
    lease = store.claim_job("p")
    first = store.reserve_call("p", lease, "call", "llm", 100)
    assert first.admitted
    replay = store.reserve_call("p", lease, "call", "llm", 100)
    assert not replay.admitted and replay.state == "reserved"
    with pytest.raises(StoreError, match="idempotency_conflict"):
        store.reserve_call("p", lease, "call", "llm", 101)
    with pytest.raises(StoreError, match="usage_unsettled"):
        store.finish_job("p", lease, succeeded=True)
    actual = store.settle_call("p", lease, "call", 20)
    assert actual.actual_tokens == 20
    store.finish_job("p", lease, succeeded=True)
    assert store.settle_call("p", lease, "call", 20) == actual


@pytest.mark.parametrize("kind,limit", [("llm", 6), ("embedding", 40)])
def test_wire_count_caps_count_known_format_retries(setup, kind, limit):
    store, _ = setup
    lease = store.claim_job("p")
    for i in range(limit):
        assert store.reserve_call("p", lease, f"c{i}", kind, 100).admitted
        store.settle_call("p", lease, f"c{i}", 20)
    with pytest.raises(StoreError, match="budget_exceeded"):
        store.reserve_call("p", lease, "extra", kind, 100)


def test_token_envelope_is_not_refunded_after_small_response(setup):
    store, _ = setup
    lease = store.claim_job("p")
    store.reserve_call("p", lease, "a", "llm", 60000)
    store.settle_call("p", lease, "a", 1)
    with pytest.raises(StoreError, match="budget_exceeded"):
        store.reserve_call("p", lease, "b", "embedding", 50000)


def test_parallel_requests_do_not_double_reserve(setup):
    store, _ = setup
    lease = store.claim_job("p")
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(
            pool.map(lambda _: store.reserve_call("p", lease, "same", "llm", 60000), range(4))
        )
    assert sum(r.admitted for r in result) == 1


@pytest.mark.parametrize("finish", ["cancel", "expiry", "unknown", "overrun"])
def test_unknown_freezes_job_but_late_usage_is_recorded(setup, finish):
    store, clock = setup
    lease = store.claim_job("p")
    store.reserve_call("p", lease, "a", "llm", 100)
    actual = 101 if finish == "overrun" else 20
    if finish == "cancel":
        store.cancel_job("p", lease.job_id)
    elif finish == "expiry":
        clock[0] += 31
        assert store.claim_job("p") is None
    else:
        store.settle_call("p", lease, "a", 101 if finish == "overrun" else None)
    receipt = store.settle_call("p", lease, "a", actual)
    assert receipt.state == "unknown" and receipt.actual_tokens == actual
    assert store.job_status("p", lease.job_id) == "unknown"
    with pytest.raises(StoreError, match="lease_conflict"):
        store.reserve_call("p", lease, "b", "llm", 100)
    assert store.claim_job("p") is None
    reopened = MemoryStore(store.directory, clock=lambda: clock[0])
    assert reopened.job_status("p", lease.job_id) == "unknown"
    assert reopened.settle_call("p", lease, "a", actual) == receipt
    reopened.close()


def test_pausing_stops_processing_but_keeps_billing(setup):
    store, _ = setup
    lease = store.claim_job("p")
    store.reserve_call("p", lease, "a", "llm", 100)
    store.set_connection("p", "c", 1, enabled=False)
    assert store.settle_call("p", lease, "a", 20).actual_tokens == 20
    with pytest.raises(StoreError, match="job_obsolete"):
        store.reserve_call("p", lease, "b", "llm", 100)


def test_projection_scoped_generation_and_tombstone_check(setup):
    store, _ = setup
    item, lease = ready_projection(store)
    digest = store.memory("p", item.resource_id).digest
    store.register_projection("p", lease, "dataset", "data", digest)
    store.register_projection("p", lease, "dataset", "data", digest)
    assert store.resolve_projection("p", "dataset", "data") == item
    assert store.resolve_projection("other", "dataset", "data") is None
    store.finish_job("p", lease, succeeded=True)
    store.change_memory("p", item.resource_id, "edit", 2, content=source())
    assert store.resolve_projection("p", "dataset", "data") is None
    store.change_memory("p", item.resource_id, "again", 3, state="confirmed")
    assert store.resolve_projection("p", "dataset", "data") is None
    lease2 = store.claim_job("p")
    with pytest.raises(StoreError, match="projection_conflict"):
        store.register_projection(
            "p", lease2, "dataset", "data", store.memory("p", item.resource_id).digest
        )
    store.register_projection(
        "p", lease2, "dataset2", "data2", store.memory("p", item.resource_id).digest
    )
    store.change_memory("p", item.resource_id, "delete", 4, state="deleted")
    assert store.resolve_projection("p", "dataset2", "data2") is None
    with pytest.raises(StoreError, match="lease_conflict"):
        store.finish_job("p", lease2, succeeded=True)
    store.set_connection("p", "c", 1, enabled=False)
    cleanup = store.claim_job("p")
    assert cleanup.kind == "cleanup"
    store.finish_job("p", cleanup, succeeded=True)


def test_invalid_projection_and_paused_source_not_usable(setup):
    store, _ = setup
    item, lease = ready_projection(store)
    with pytest.raises(StoreError, match="projection_mismatch"):
        store.register_projection("p", lease, "dataset", "data", "a" * 64)
    with pytest.raises(StoreError, match="not_found"):
        store.register_projection("other", lease, "dataset", "data", "a" * 64)
    store.register_projection(
        "p", lease, "dataset", "data", store.memory("p", item.resource_id).digest
    )
    store.set_connection("p", "c", 1, enabled=False)
    assert store.resolve_projection("p", "dataset", "data") is None


def test_edit_invalidates_inflight_usage_without_discarding_late_bill(setup):
    store, _ = setup
    item, lease = ready_projection(store)
    store.reserve_call("p", lease, "a", "llm", 100)
    store.change_memory("p", item.resource_id, "edit", 2, content=source())
    receipt = store.settle_call("p", lease, "a", 20)
    assert receipt.state == "unknown" and receipt.actual_tokens == 20
    with pytest.raises(StoreError, match="lease_conflict"):
        store.register_projection(
            "p", lease, "dataset", "data", store.memory("p", item.resource_id).digest
        )


@pytest.mark.parametrize("tokens", [0, -1, 65537, True])
def test_invalid_reservation_limit_never_writes(setup, tokens):
    store, _ = setup
    lease = store.claim_job("p")
    with pytest.raises(StoreError, match="invalid_budget"):
        store.reserve_call("p", lease, "a", "llm", tokens)


@pytest.mark.parametrize("paused", [False, True])
@pytest.mark.parametrize("kind", ["llm", "embedding"])
def test_cleanup_never_reserves_model_but_can_finish_when_paused(setup, paused, kind):
    store, _ = setup
    item, _ = ready_projection(store)
    store.change_memory("p", item.resource_id, "delete", 2, state="deleted")
    if paused:
        store.set_connection("p", "c", 1, enabled=False)
    lease = store.claim_job("p")
    assert lease.kind == "cleanup"
    with pytest.raises(StoreError, match="cleanup_model_forbidden"):
        store.reserve_call("p", lease, "bad", kind, 100)
    store.finish_job("p", lease, succeeded=True)
    assert store.job_status("p", lease.job_id) == "done"


def test_owned_event_read_rechecks_scope_lease_and_integrity(setup):
    store, clock = setup
    lease = store.claim_job("p")
    assert store.event_for_job("p", lease).event_id == "e"
    with pytest.raises(StoreError, match="not_found"):
        store.event_for_job("other", lease)
    with store._transaction(write=True) as c:
        c.exec_driver_sql("UPDATE memory_events SET payload=?", (b"{}",))
    with pytest.raises(StoreError, match="store_corrupt"):
        store.event_for_job("p", lease)
    clock[0] += 31
    with pytest.raises(StoreError, match="lease_conflict"):
        store.event_for_job("p", lease)
