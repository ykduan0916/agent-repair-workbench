"""B1a contracts on real temporary SQLite; never start a model or container."""

import json
import pickle
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import event

from agent_workbench.candidates.snapshots import SnapshotStore
from agent_workbench.orchestration.contracts import Usage
from agent_workbench.repair_store.fencing import RepairFence
from agent_workbench.repair_store.models import RepairTask
from agent_workbench.run_store.models import StoreError
from agent_workbench.web_repairs.fencing import RepairWebFence
from agent_workbench.web_repairs.models import WEB_BUDGET, RepairAdmissionClaim, RepairRequest
from agent_workbench.web_repairs.store import WebRepairStore

POLICY = "a" * 64


def uid():
    return str(uuid4())


def task_for(root, request, now):
    baseline = SnapshotStore(root / "snapshots").create_baseline(uid(), case_id=request.case_id)
    return RepairTask(
        task_id=request.request_id,
        mode=request.mode,
        baseline=baseline.ref,
        started_at=datetime.fromtimestamp(now, UTC),
        budget=WEB_BUDGET,
        model_config_sha256="b" * 64,
    )


@pytest.fixture
def host(tmp_path):
    clock = [1_700_000_000.0]
    store = WebRepairStore(tmp_path / "state", clock=lambda: clock[0])
    store.initialize()
    request = RepairRequest(request_id=uid(), case_id="output_mismatch", mode="three_role")
    task = task_for(tmp_path, request, clock[0])
    with (
        RepairWebFence(store.directory) as owner,
        RepairFence(store.directory, request.request_id) as fence,
    ):
        yield store, request, task, owner, fence, clock
    store.close()


def admit(host, **changes):
    store, request, task, owner, fence, _ = host
    return store.admit(
        request, task, **{"policy_sha256": POLICY, "owner": owner, "task_fence": fence, **changes}
    )


def consume(host, admission, **changes):
    store, request, _, _, fence, _ = host
    return store.consume(
        admission.claim,
        admission.saved,
        **{
            "case_id": request.case_id,
            "mode": request.mode,
            "policy_sha256": POLICY,
            "task_fence": fence,
            **changes,
        },
    )


def alter_claim(claim, **changes):
    fields = {key: getattr(claim, key) for key in claim.__slots__}
    return RepairAdmissionClaim(**{**fields, **changes})


def sql(store, query, args=()):
    with sqlite3.connect(store._base.path) as conn:
        return conn.execute(query, args).fetchall()


def counts(store):
    return [
        sql(store, f"SELECT count(*) FROM {name}")[0][0]
        for name in ("repair_tasks", "repair_events", "web_repair_requests")
    ]


@pytest.mark.parametrize("case", ["output_mismatch", "counter_offset"])
@pytest.mark.parametrize("mode", ["single_role", "three_role"])
def test_four_requests_once_only_without_changing_task(host, tmp_path, case, mode):
    store, old, _, owner, fence, clock = host
    request = old.model_copy(update={"case_id": case, "mode": mode})
    task = task_for(tmp_path, request, clock[0])
    admission = store.admit(request, task, policy_sha256=POLICY, owner=owner, task_fence=fence)
    before = sql(store, "SELECT payload FROM repair_tasks")
    started = store.consume(
        admission.claim,
        admission.saved,
        case_id=case,
        mode=mode,
        policy_sha256=POLICY,
        task_fence=fence,
    )
    assert started.executable and not started.cancel_requested
    assert started.saved.task.usage == Usage()
    assert sql(store, "SELECT payload FROM repair_tasks") == before
    assert counts(store) == [1, 1, 1]
    with pytest.raises(StoreError, match="admission_invalid"):
        store.consume(
            admission.claim,
            admission.saved,
            case_id=case,
            mode=mode,
            policy_sha256=POLICY,
            task_fence=fence,
        )
    assert store.receipt(request.request_id).consumed


def test_request_strict_and_private_claim(host):
    _, request, _, _, _, _ = host
    for fields in (
        {"command": "no"},
        {"case_id": "passing"},
        {"mode": "other"},
        {"request_id": request.request_id.upper()},
    ):
        with pytest.raises(ValueError):
            RepairRequest.model_validate({**request.model_dump(), **fields})
    admission = admit(host)
    assert admission.claim.token not in repr(admission)
    assert admission.claim.token not in repr(admission.claim)
    with pytest.raises(TypeError):
        json.dumps(admission.claim)
    with pytest.raises(TypeError, match="private_capability"):
        pickle.dumps(admission.claim)
    assert admission.claim.token.encode() not in host[0]._base.path.read_bytes()


def test_reuse_before_current_config_validation_and_conflict(host):
    store, request, task, owner, fence, clock = host
    first = admit(host)
    before = sql(store, "SELECT * FROM repair_tasks")
    # Same intent survives a now-unavailable model/config; no new claim or policy.
    second = store.admit(
        request,
        task.model_copy(update={"model_config_sha256": None}),
        policy_sha256="invalid",
        owner=owner,
        task_fence=fence,
    )
    assert second.claim is None and second.saved == first.saved
    changed = request.model_copy(update={"mode": "single_role"})
    with pytest.raises(StoreError, match="request_conflict"):
        store.existing(changed)
    assert sql(store, "SELECT * FROM repair_tasks") == before
    assert store.existing(request).claim is None


def test_cli_collision_and_missing_task_tombstone(host):
    store, request, task, _, fence, _ = host
    store.create_task(task, fence=fence)
    with pytest.raises(StoreError, match="request_conflict"):
        admit(host)
    assert counts(store) == [1, 1, 0]


def test_web_tombstone_never_recreates(host):
    store, request, _, _, _, _ = host
    admit(host)
    sql(store, "DELETE FROM repair_events")
    sql(store, "DELETE FROM repair_tasks")
    for operation in (
        lambda: store.existing(request),
        lambda: admit(host),
        lambda: store.receipt(request.request_id),
        lambda: store.request_cancel(request.request_id),
    ):
        with pytest.raises(StoreError, match="request_gone"):
            operation()
    assert counts(store) == [0, 0, 1]


@pytest.mark.parametrize("table", ["repair_tasks", "repair_events", "web_repair_requests"])
def test_admission_transaction_rolls_back_each_insert(host, table):
    store = host[0]

    def fail(conn, cursor, statement, parameters, context, many):
        if statement.startswith(f"INSERT INTO {table} "):
            raise RuntimeError("injected")

    engine = store._base._database()
    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            admit(host)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert counts(store) == [0, 0, 0]
    assert admit(host).claim


@pytest.mark.parametrize("expired", [False, True])
@pytest.mark.parametrize(
    "statement", ["UPDATE web_repair_requests", "UPDATE repair_tasks", "INSERT INTO repair_events"]
)
def test_cancel_timeout_and_claim_consumption_rollback_together(host, expired, statement):
    store, request, _, _, _, clock = host
    admission = admit(host)
    if expired:
        clock[0] += 600
    else:
        store.request_cancel(request.request_id)
    before = sql(store, "SELECT payload FROM repair_tasks")

    def fail(conn, cursor, sql_text, parameters, context, many):
        if sql_text.startswith(statement):
            raise RuntimeError("injected")

    engine = store._base._database()
    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            consume(host, admission)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert not store.receipt(request.request_id).consumed
    assert sql(store, "SELECT payload FROM repair_tasks") == before
    assert counts(store) == [1, 1, 1]
    result = consume(host, admission)
    assert not result.executable
    assert result.saved.task.outcome == ("budget_exceeded" if expired else "cancelled")
    assert result.saved.task.usage == Usage()
    assert store.receipt(request.request_id).active
    assert counts(store) == [1, 2, 1]


def test_cancel_repeat_and_after_consume_preserves_usage_and_task(host):
    store, request, _, _, _, _ = host
    admission = admit(host)
    result = consume(host, admission)
    assert result.executable
    before = sql(store, "SELECT payload FROM repair_tasks")
    for _ in range(2):
        assert store.request_cancel(request.request_id).cancel_requested
    assert sql(store, "SELECT payload FROM repair_tasks") == before
    assert store.receipt(request.request_id).consumed


def test_clock_from_admission_and_rollback_rejected(host):
    store, request, _, _, _, clock = host
    clock[0] += 100
    admission = admit(host)
    assert admission.saved.task.started_at.timestamp() == clock[0]
    clock[0] -= 1
    with pytest.raises(StoreError, match="invalid_clock"):
        consume(host, admission)
    assert not store.receipt(request.request_id).consumed
    clock[0] += 601
    result = consume(host, admission)
    assert not result.executable and result.saved.task.error_code == "DEADLINE_EXCEEDED"


@pytest.mark.parametrize(
    "change", [{"case_id": "counter_offset"}, {"mode": "single_role"}, {"policy_sha256": "c" * 64}]
)
def test_wrong_consume_binding_is_not_consumed(host, change):
    admission = admit(host)
    with pytest.raises(StoreError, match="admission_invalid"):
        consume(host, admission, **change)
    assert not host[0].receipt(host[1].request_id).consumed


def test_forged_token_initial_and_stale_owner(host):
    store, request, _, owner, _, _ = host
    admission = admit(host)
    for claim in (
        alter_claim(admission.claim, token="f" * 64),
        alter_claim(admission.claim, initial_sha256="f" * 64),
        alter_claim(admission.claim, nonce="f" * 64),
    ):
        with pytest.raises(StoreError):
            consume(host, replace(admission, claim=claim))
    owner.close()
    owner.acquire()
    with pytest.raises(StoreError, match="owner_lock_required"):
        consume(host, admission)
    assert not store.receipt(request.request_id).consumed
    assert store.existing(request).claim is None


def test_wrong_directory_and_owner(host, tmp_path):
    store, request, _, _, _, _ = host
    admission = admit(host)
    other = tmp_path / "other"
    other.mkdir()
    with RepairWebFence(other) as owner:
        with pytest.raises(StoreError, match="owner_lock_required"):
            consume(host, replace(admission, claim=alter_claim(admission.claim, owner=owner)))
    assert not store.receipt(request.request_id).consumed


def test_concurrent_consumers_one_authorization(host):
    admission = admit(host)
    barrier = Barrier(2)

    def worker():
        barrier.wait(timeout=5)
        try:
            return consume(host, admission).executable
        except StoreError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: worker(), range(2)))
    assert sorted(results, key=str) == [True, "admission_invalid"]
    assert counts(host[0]) == [1, 1, 1]


@pytest.mark.parametrize(
    "column,value",
    [
        ("payload_sha256", "a" * 64),
        ("initial_sha256", "b" * 64),
        ("policy_sha256", "bad"),
        ("claim_sha256", "bad"),
        ("mode", "single_role"),
        ("case_id", "counter_offset"),
        ("consumed", 3),
        ("cancel_requested", 3),
        ("active", 0),
    ],
)
def test_receipt_tampering_read_rejected(host, column, value):
    store, request, _, _, _, _ = host
    admit(host)
    try:
        with sqlite3.connect(store._base.path) as conn:
            conn.execute("PRAGMA ignore_check_constraints=ON")
            conn.execute(f"UPDATE web_repair_requests SET {column}=?", (value,))
    except sqlite3.IntegrityError:
        # Immutable metadata is rejected at the write boundary.
        assert store.receipt(request.request_id).saved.task.revision == 0
    else:
        with pytest.raises(StoreError, match="store_corrupt"):
            store.receipt(request.request_id)


def test_terminal_slot_is_not_implicitly_released(host, tmp_path):
    store, request, _, owner, _, _ = host
    admission = admit(host)
    store.request_cancel(request.request_id)
    result = consume(host, admission)
    assert not result.executable
    assert store.request_cancel(request.request_id).saved == result.saved
    new = request.model_copy(update={"request_id": uid()})
    with RepairFence(store.directory, new.request_id) as fence:
        with pytest.raises(StoreError, match="recovery_required"):
            store.admit(
                new,
                task_for(tmp_path, new, host[5][0]),
                policy_sha256=POLICY,
                owner=owner,
                task_fence=fence,
            )
    assert counts(store) == [1, 2, 1]


@pytest.mark.parametrize("operation", ["admit", "consume"])
def test_commit_acknowledgement_lost_never_reissues_claim(host, monkeypatch, operation):
    from contextlib import contextmanager

    store, request, _, _, _, _ = host
    admission = admit(host) if operation == "consume" else None
    transaction = store._base._transaction

    @contextmanager
    def lost_ack(**kwargs):
        with transaction(**kwargs) as connection:
            yield connection
        if kwargs.get("write"):
            raise StoreError("store_unavailable")

    monkeypatch.setattr(store._base, "_transaction", lost_ack)
    with pytest.raises(StoreError, match="store_unavailable"):
        consume(host, admission) if admission else admit(host)
    monkeypatch.setattr(store._base, "_transaction", transaction)
    assert store.existing(request).claim is None
    assert admit(host).claim is None
    assert store.receipt(request.request_id).consumed == (operation == "consume")
    if admission:
        with pytest.raises(StoreError, match="admission_invalid"):
            consume(host, admission)


def test_sql_unique_slot_exists_independently_of_owner(host):
    store = host[0]
    admit(host)
    with sqlite3.connect(store._base.path) as conn:
        row = list(conn.execute("SELECT * FROM web_repair_requests").fetchone())
        row[0] = uid()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO web_repair_requests VALUES (?,?,?,?,?,?,?,?,?,?,?)", row)
    assert counts(store) == [1, 1, 1]


def test_advanced_task_cannot_consume_old_initial_claim(host):
    store, request, _, _, fence, _ = host
    admission = admit(host)
    store.finish_task(
        request.request_id,
        "inconclusive",
        expected_revision=0,
        expected_sha256=admission.saved.task_sha256,
        fence=fence,
    )
    with pytest.raises(StoreError, match="store_corrupt"):
        consume(host, admission)
    assert sql(store, "SELECT consumed FROM web_repair_requests") == [(0,)]


def test_two_waiting_admissions_same_owner_only_first_gets_claim(host):
    barrier = Barrier(2)

    def worker():
        barrier.wait(timeout=5)
        return admit(host).claim is not None

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: worker(), range(2))) == [False, True]
    assert counts(host[0]) == [1, 1, 1]


def test_memory_bound_terminal_receipt_can_release_slot(host):
    from agent_workbench.experience_memory.agent_models import MemoryBinding, agent_budget

    store, request, task, owner, fence, _ = host
    binding = MemoryBinding(project_id="project", connection_id="source")
    request = request.model_copy(update={"memory": binding})
    task = task.model_copy(update={"budget": agent_budget(binding, WEB_BUDGET)})
    admission = store.admit(request, task, policy_sha256=POLICY, owner=owner, task_fence=fence)
    started = store.consume(
        admission.claim,
        admission.saved,
        case_id=request.case_id,
        mode=request.mode,
        policy_sha256=POLICY,
        task_fence=fence,
    )
    store.finish_task(
        request.request_id,
        "inconclusive",
        expected_revision=started.saved.task.revision,
        expected_sha256=started.saved.task_sha256,
        fence=fence,
    )
    stamp, settled, evidence = store.release_state(request.request_id)
    assert settled and evidence == {} and len(stamp) == 64
    store._release_cas(request.request_id, stamp, owner=owner, fence=fence)
    assert not store.receipt(request.request_id).active
