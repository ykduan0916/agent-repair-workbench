"""M1-R SQLite acceptance; synthetic material only, no provider requests."""

import multiprocessing
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from pydantic import ValidationError

from agent_workbench.experience_memory import DraftInput, EventInput, MemoryStore
from agent_workbench.experience_memory import store as authority_module
from agent_workbench.run_store import RunStore, StoreError


@pytest.fixture(autouse=True)
def spawn_import_path(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))


def event(event_id="e1", **changes):
    return EventInput(
        event_id=event_id,
        event_type="test_completed",
        source_run_id="synthetic",
        occurred_at=1000.0,
        outcome=changes.get("outcome", "failed"),
    )


def draft(**changes):
    return DraftInput(
        **dict(
            kind="repair_experience",
            connection_id="c1",
            event_id="e1",
            tool_id="count",
            source_version="v1",
            title="Synthetic count",
            body="Synthetic: number returned as text.",
            **changes,
        )
    )


@pytest.fixture
def store(tmp_path):
    value = MemoryStore(tmp_path / "state")
    value.initialize()
    value.register_project("p1")
    value.register_project("p2")
    value.register_connection("p1", "c1", "synthetic")
    value.accept_event("p1", "c1", event())
    yield value
    value.close()


def rows(store, sql, args=()):
    with sqlite3.connect(store.path) as c:
        return c.execute(sql, args).fetchall()


def test_event_idempotency_scope_and_no_claim_of_internal_verification(store):
    assert store.accept_event("p1", "c1", event()) == "e1"
    assert len(rows(store, "SELECT * FROM memory_events")) == 1
    assert len(rows(store, "SELECT * FROM memory_jobs")) == 1
    with pytest.raises(StoreError, match="idempotency_conflict"):
        store.accept_event("p1", "c1", event(outcome="passed"))
    with pytest.raises(StoreError, match="not_found"):
        store.accept_event("p2", "c1", event())
    with pytest.raises(StoreError, match="invalid_origin"):
        store.register_connection("p1", "bad", "internally_verified")
    store.register_connection("p1", "c2", "external_report")
    store.accept_event("p1", "c2", event())
    assert len(rows(store, "SELECT * FROM memory_events")) == 2


def test_connection_pause_cas_and_immutable_project(store):
    with pytest.raises(StoreError, match="connection_conflict"):
        store.register_connection("p2", "c1", "synthetic")
    assert store.set_connection("p1", "c1", 1, enabled=False) == 2
    with pytest.raises(StoreError, match="version_conflict"):
        store.set_connection("p1", "c1", 1, enabled=True)
    with pytest.raises(StoreError, match="connection_paused"):
        store.accept_event("p1", "c1", event("e2"))
    assert store.claim_job("p1") is None
    store.set_connection("p1", "c1", 2, enabled=True)
    assert store.claim_job("p1") is not None


def test_lifecycle_receipts_survive_restart_and_delete_removes_all_bodies(store):
    created = store.create_memory("p1", "create", draft())
    assert created.state == "draft"
    assert store.create_memory("p1", "create", draft()) == created
    confirmed = store.change_memory("p1", created.resource_id, "confirm", 1, state="confirmed")
    edited = store.change_memory(
        "p1",
        created.resource_id,
        "edit",
        2,
        content=draft().model_copy(update={"body": "New synthetic guidance"}),
    )
    assert edited.state == "draft" and edited.revision == 2
    assert (
        store.change_memory("p1", created.resource_id, "confirm", 1, state="confirmed") == confirmed
    )
    revoked = store.change_memory("p1", created.resource_id, "revoke", 3, state="revoked")
    assert revoked.version == 4
    deleted = store.change_memory("p1", created.resource_id, "delete", 4, state="deleted")
    reopened = MemoryStore(store.directory)
    assert reopened.memory("p1", created.resource_id).receipt == deleted
    assert reopened.memory("p1", created.resource_id).content is None
    assert rows(store, "SELECT payload FROM memory_revisions") == [(None,), (None,)]
    assert reopened.create_memory("p1", "create", draft()) == created  # receipt, no resurrection
    with pytest.raises(StoreError, match="memory_deleted"):
        reopened.change_memory("p1", created.resource_id, "resurrect", 5, state="confirmed")
    with pytest.raises(StoreError, match="idempotency_conflict"):
        reopened.change_memory("p1", created.resource_id, "confirm", 4, state="revoked")
    with pytest.raises(StoreError, match="not_found"):
        reopened.memory("p2", created.resource_id)
    assert reopened.list_memories("p2") == []
    assert "Synthetic" not in reopened.list_memories("p1")[0].model_dump_json()
    reopened.close()


def test_source_cannot_cross_projects_or_reference_missing_event(store):
    store.register_connection("p2", "other", "external_report")
    for connection_id, event_id in [("other", "e1"), ("c1", "missing")]:
        value = draft().model_copy(update={"connection_id": connection_id, "event_id": event_id})
        with pytest.raises(StoreError, match="not_found"):
            store.create_memory("p1", "try", value)
    assert rows(store, "SELECT * FROM memory_heads") == []


def test_transaction_rollback_covers_head_revision_receipt_and_outbox(store, monkeypatch):
    def fail(*args, **kwargs):
        raise StoreError("injected_failure")

    monkeypatch.setattr(authority_module, "enqueue", fail)
    with pytest.raises(StoreError, match="injected_failure"):
        store.create_memory("p1", "create", draft())
    for table in ["memory_heads", "memory_revisions", "memory_receipts"]:
        assert rows(store, f"SELECT * FROM {table}") == []
    assert len(rows(store, "SELECT * FROM memory_jobs")) == 1


def _cas_process(path, memory_id, barrier, queue):
    store = MemoryStore(Path(path))
    barrier.wait(timeout=10)
    try:
        store.change_memory("p1", memory_id, str(uuid4()), 1, state="confirmed")
        queue.put("ok")
    except StoreError as error:
        queue.put(error.code)
    finally:
        store.close()


def test_two_process_cas_only_one_commits(store):
    created = store.create_memory("p1", "create", draft())
    context = multiprocessing.get_context("spawn")
    barrier, queue = context.Barrier(2), context.Queue()
    processes = [
        context.Process(
            target=_cas_process, args=(str(store.directory), created.resource_id, barrier, queue)
        )
        for _ in range(2)
    ]
    for p in processes:
        p.start()
    for p in processes:
        p.join(15)
        if p.is_alive():
            p.kill()
            pytest.fail("CAS process timeout")
        assert p.exitcode == 0
    assert sorted([queue.get(timeout=2), queue.get(timeout=2)]) == ["ok", "version_conflict"]
    assert store.memory("p1", created.resource_id).receipt.version == 2
    queue.close()


def _crash_before_commit(path):
    store = MemoryStore(Path(path))

    def crash(*args, **kwargs):
        os._exit(31)

    authority_module.enqueue = crash
    store.create_memory("p1", "crash", draft())


def test_process_death_rolls_back_uncommitted_memory(store):
    p = multiprocessing.get_context("spawn").Process(
        target=_crash_before_commit, args=(str(store.directory),)
    )
    p.start()
    p.join(10)
    if p.is_alive():
        p.kill()
        pytest.fail("crash test timeout")
    assert p.exitcode == 31
    assert store.list_memories("p1") == []
    assert rows(store, "SELECT * FROM memory_receipts") == []
    assert store.create_memory("p1", "crash", draft()).version == 1


def test_queue_failure_does_not_claim_deleted_or_erase_content(store):
    item = store.create_memory("p1", "create", draft())
    # Exhaust unrelated work; this memory's draft job has already completed/retired.
    with store._transaction(write=True) as c:
        c.exec_driver_sql("UPDATE memory_jobs SET state='done'")
        for i in range(1000):
            c.exec_driver_sql(
                "INSERT INTO memory_jobs(job_id,project_id,kind,connection_id,event_id,"
                "generation,state,created_at) VALUES(?,'p1','event','c1','e1',0,'pending',0)",
                (f"j{i}",),
            )
    with pytest.raises(StoreError, match="queue_limit"):
        store.change_memory("p1", item.resource_id, "del", 1, state="deleted")
    assert store.memory("p1", item.resource_id).content is not None
    assert store.memory("p1", item.resource_id).receipt.version == 1
    with pytest.raises(StoreError, match="queue_limit"):
        store.accept_event("p1", "c1", event("full"))
    assert rows(store, "SELECT event_id FROM memory_events WHERE event_id='full'") == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("body", "中" * 6000),
        ("body", ""),
        ("kind", "admin"),
        ("event_id", "../secret"),
        ("title", "x" * 161),
    ],
)
def test_invalid_model_copy_revalidated_at_boundary(store, field, value):
    with pytest.raises(StoreError, match="invalid_payload") as error:
        store.create_memory("p1", "bad", draft().model_copy(update={field: value}))
    assert str(error.value) == "invalid_payload"
    assert store.list_memories("p1") == []


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_nonfinite_or_negative_source_time_rejected(value):
    with pytest.raises(ValidationError):
        EventInput(
            event_id="e",
            event_type="test_completed",
            source_run_id="r",
            occurred_at=value,
            outcome="unknown",
        )


def test_limits_history_and_tamper_detection(store):
    item = store.create_memory("p1", "create", draft())
    with pytest.raises(StoreError, match="invalid_limit"):
        store.list_memories("p1", limit=101)
    with store._transaction(write=True) as c:
        c.exec_driver_sql("UPDATE memory_heads SET revision=64")
        c.exec_driver_sql("UPDATE memory_revisions SET revision=64")
    with pytest.raises(StoreError, match="revision_limit"):
        store.change_memory("p1", item.resource_id, "edit", 1, content=draft())
    with store._transaction(write=True) as c:
        c.exec_driver_sql("UPDATE memory_revisions SET payload=?", (b"{}",))
    with pytest.raises(StoreError, match="store_corrupt"):
        store.memory("p1", item.resource_id)


def test_migration_preserves_old_run_and_rejects_nonempty_downgrade(tmp_path):
    store = RunStore(tmp_path / "db")
    store.initialize()
    run = store.create("isolated-fixture", "pass", "a" * 64, "b" * 64)
    with store._transaction(write=True) as c:
        command.downgrade(store._migration(c), "0010_decision_events")
    with pytest.raises(StoreError, match="schema_incompatible"):
        MemoryStore(store.directory).list_memories("p1")
    store.initialize()
    assert store.detail(run.run_id).run == run
    memory = MemoryStore(store.directory)
    memory.register_project("p1")
    with pytest.raises(StoreError, match="migration_not_empty"):
        memory.downgrade_empty()
    assert store.detail(run.run_id).run == run


def test_threaded_exact_retransmission_does_not_duplicate(store):
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: store.create_memory("p1", "create", draft()), range(4)))
    assert all(r == receipts[0] for r in receipts)
    assert len(store.list_memories("p1")) == 1


@pytest.mark.parametrize("version", [True, 1.0, "1", 2])
def test_schema_version_has_exact_integer_type(store, version):
    payload = event().model_dump()
    payload["schema_version"] = version
    with pytest.raises(ValidationError):
        EventInput.model_validate(payload)
    with pytest.raises(StoreError, match="invalid_payload"):
        store.accept_event("p1", "c1", event("bad").model_copy(update={"schema_version": version}))
