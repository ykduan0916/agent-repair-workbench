"""M4-R real SQLite/API tests; no model/provider/mock storage."""

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_workbench.experience_memory.models import EventInput
from agent_workbench.run_store import StoreError
from agent_workbench.web_memory.api import router
from agent_workbench.web_memory.store import WebMemoryStore


@pytest.fixture
def store(tmp_path):
    s = WebMemoryStore(tmp_path / "state")
    s.initialize()
    s.register_project("p")
    s.register_project("other")
    s.register_connection("p", "c", "external_report")
    yield s
    s.close()


def event(outcome="failed"):
    return EventInput(
        event_id="e",
        event_type="test_completed",
        source_run_id="r",
        occurred_at=1.0,
        outcome=outcome,
    )


def test_token_hash_rotation_revocation_scope(store):
    first = store.credential("p", "c", 0, rotate=True)
    token = first["token"]
    assert token not in str(store.overview("p"))
    assert token.encode() not in store.path.read_bytes()
    assert store.ingest("c", event(), token=token)["processing"] == "pending"
    with pytest.raises(StoreError, match="not_found"):
        store.credential("other", "c", 1, rotate=True)
    second = store.credential("p", "c", 1, rotate=True)
    with pytest.raises(StoreError, match="unauthorized"):
        store.ingest("c", event(), token=token)
    assert store.event_receipt("c", "e", second["token"])["accepted"]
    store.credential("p", "c", 2, rotate=False)
    with pytest.raises(StoreError, match="unauthorized"):
        store.event_receipt("c", "e", second["token"])
    with pytest.raises(StoreError, match="version_conflict"):
        store.credential("p", "c", 2, rotate=True)


def test_ingest_idempotency_processing_restart_and_pause(store):
    token = store.credential("p", "c", 0, rotate=True)["token"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: store.ingest("c", event(), token=token), range(2)))
    assert receipts[0] == receipts[1]
    with pytest.raises(StoreError, match="idempotency_conflict"):
        store.ingest("c", event("passed"), token=token)
    version = store.overview("p")["connections"][0]["version"]
    store.set_connection("p", "c", version, enabled=False)
    with pytest.raises(StoreError, match="connection_paused"):
        store.ingest("c", event(), token=token)
    assert store.process_pending_events() == 0
    store.set_connection("p", "c", version + 1, enabled=True)
    reopened = WebMemoryStore(store.directory)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sum(pool.map(lambda _: reopened.process_pending_events(), range(2))) == 1
        view = reopened.overview("p")
        assert len(view["memories"]) == 1
        assert view["memories"][0]["receipt"]["state"] == "draft"
        assert "not an internally verified result" in view["memories"][0]["content"]["body"]
        assert view["events"][0]["processing"] == "done"
        assert view["jobs"][0]["state"] == "done"
        assert view["grants"] == []
    finally:
        reopened.close()


def test_processing_atomic_rollback(store, monkeypatch):
    store.ingest("c", event(), project_id="p")

    def failure(*args):
        raise StoreError("synthetic_failure")

    monkeypatch.setattr(store, "_revision", failure)
    with pytest.raises(StoreError, match="synthetic_failure"):
        store.process_pending_events()
    assert store.list_memories("p") == []
    assert store.overview("p")["events"][0]["processing"] == "pending"


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setattr("agent_workbench.web_memory.api.state_directory", lambda: store.directory)

    @asynccontextmanager
    async def lifespan(app):
        app.state.local_origins = {"http://127.0.0.1:3010"}
        app.state.memory_reads = asyncio.Semaphore(4)
        app.state.memory_workers = set()
        yield
        await asyncio.gather(*app.state.memory_workers, return_exceptions=True)

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    with TestClient(app) as c:
        yield c


ADMIN = {"origin": "http://127.0.0.1:3010", "x-awb-local": "1"}


def test_api_origin_no_secret_echo_and_external_no_admin(client, store):
    path = "/api/memory/projects"
    for headers in ({}, {"origin": "https://evil.example"}, {"origin": ADMIN["origin"]}):
        response = client.post(path, json={"project_id": "new"}, headers=headers)
        assert response.status_code == 403
    assert client.post(path, json={"project_id": "new"}, headers=ADMIN).status_code == 200
    token = store.credential("p", "c", 0, rotate=True)["token"]
    external = {"authorization": f"Bearer {token}"}
    assert client.post(path, json={"project_id": "bad"}, headers=external).status_code == 403
    response = client.post(
        "/api/integrations/c/events", json=event().model_dump(mode="json"), headers=external
    )
    assert response.status_code == 200 and response.json()["processing"] == "pending"
    assert client.get("/api/integrations/c/events/e", headers=external).status_code == 200
    invalid = event().model_dump(mode="json") | {"unexpected_secret": token}
    response = client.post("/api/integrations/c/events", json=invalid, headers=external)
    assert response.status_code == 400 and token not in response.text
    assert client.get("/api/integrations/c/events/e").status_code == 401


@pytest.mark.parametrize(
    "raw,status",
    [
        ('{"project_id":"p","project_id":"q"}', 400),
        ('{"project_id":NaN}', 400),
        ("[]", 400),
        ('{"project_id":"' + "x" * 33000 + '"}', 413),
        ('{"project_id":"../../bad"}', 400),
    ],
)
def test_api_payload_boundaries(client, raw, status):
    response = client.post(
        "/api/memory/projects", content=raw, headers=ADMIN | {"content-type": "application/json"}
    )
    assert response.status_code == status


def test_api_memory_lifecycle_and_explicit_policy_grant(client, store):
    base = "/api/memory/projects/p"
    assert (
        client.post(
            base + "/connections/c/events", json=event().model_dump(mode="json"), headers=ADMIN
        ).status_code
        == 200
    )
    assert client.post(base + "/connections/c/process", json={}, headers=ADMIN).json() == {
        "processed": 1
    }
    view = client.get(base + "/overview").json()
    memory = view["memories"][0]
    mid = memory["receipt"]["resource_id"]
    payload = {"request_id": "confirm", "expected_version": 1, "state": "confirmed"}
    assert (
        client.patch(base + "/memories/" + mid, json=payload, headers=ADMIN).json()["state"]
        == "confirmed"
    )
    assert client.patch(base + "/memories/" + mid, json=payload, headers=ADMIN).status_code == 200
    assert (
        client.patch(
            base + "/memories/" + mid, json=payload | {"request_id": "stale"}, headers=ADMIN
        ).status_code
        == 409
    )
    assert client.get(base + "/overview").json()["grants"] == []
    response = client.post(
        base + "/memories/" + mid + "/policy",
        json={
            "expected_version": 0,
            "expected_generation": 2,
            "policy": {"expires_at": 9999999999.0},
        },
        headers=ADMIN,
    )
    assert response.status_code == 200
    assert (
        client.post(
            base + "/grants",
            json={
                "expected_version": 0,
                "grant": {
                    "connection_id": "c",
                    "content_type": "episode",
                    "purpose": "index",
                    "enabled": True,
                    "expires_at": 9999999999.0,
                },
            },
            headers=ADMIN,
        ).status_code
        == 200
    )
    assert (
        client.patch(
            base + "/memories/" + mid,
            json={"request_id": "delete", "expected_version": 2, "state": "deleted"},
            headers=ADMIN,
        ).status_code
        == 200
    )
    assert client.get(base + "/memories/" + mid).json()["content"] is None
    with sqlite3.connect(store.path) as db:
        assert (
            db.execute("SELECT payload FROM memory_revisions WHERE memory_id=?", (mid,)).fetchone()[
                0
            ]
            is None
        )
    assert client.get("/api/memory/projects/other/memories/" + mid).status_code == 404


def test_0012_migration_preserves_authority_and_nonempty_downgrade_rejected(store):
    from alembic import command

    from agent_workbench.run_store.migrations.versions.v0011_memory_authority import TABLES

    with store._transaction(write=True) as c:
        before = {t: c.exec_driver_sql(f"SELECT * FROM {t}").all() for t in TABLES}
        command.downgrade(store._migration(c), "0012_memory_engine")
    store.initialize()
    with store._transaction() as c:
        assert {t: c.exec_driver_sql(f"SELECT * FROM {t}").all() for t in TABLES} == before
    store.credential("p", "c", 0, rotate=True)
    with pytest.raises(ValueError, match="migration_not_empty"):
        with store._transaction(write=True) as c:
            command.downgrade(store._migration(c), "0012_memory_engine")


def test_processor_does_not_read_arbitrary_external_fields(client, store):
    external = store.credential("p", "c", 0, rotate=True)["token"]
    body = event().model_dump(mode="json") | {
        "command": "cat /private/secret",
        "project_id": "other",
    }
    response = client.post(
        "/api/integrations/c/events", json=body, headers={"authorization": f"Bearer {external}"}
    )
    assert response.status_code == 400
    assert store.overview("p")["events"] == []


def test_request_cancel_keeps_concurrency_slot_until_thread_exit(store, monkeypatch):
    async def scenario():
        import threading
        from types import SimpleNamespace

        from starlette.requests import Request

        from agent_workbench.web_memory.api import invoke

        monkeypatch.setattr(
            "agent_workbench.web_memory.api.state_directory", lambda: store.directory
        )
        gate = asyncio.Semaphore(1)
        app = SimpleNamespace(state=SimpleNamespace(memory_reads=gate, memory_workers=set()))
        request = Request({"type": "http", "query_string": b"", "app": app})
        entered = threading.Event()
        release = threading.Event()

        def action(s):
            entered.set()
            release.wait(3)
            return {"done": True}

        task = asyncio.create_task(invoke(request, action))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert gate.locked()
            assert (await invoke(request, action)).status_code == 429
        finally:
            release.set()
            await asyncio.gather(*app.state.memory_workers)
        assert not gate.locked()

    asyncio.run(scenario())


def test_full_project_does_not_starve_healthy_events(store):
    from agent_workbench.experience_memory.models import DraftInput

    # Fill capacity using genuine authority records, then queue twenty older events.
    store.ingest("c", event(), project_id="p")
    base = store.create_memory(
        "p",
        "base",
        DraftInput(
            kind="episode",
            connection_id="c",
            event_id="e",
            tool_id="t",
            source_version="v1",
            title="Fixture",
            body="Synthetic capacity fixture",
        ),
    )
    with store._transaction(write=True) as c:
        c.exec_driver_sql(
            "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<9999) "
            "INSERT INTO memory_heads SELECT 'full_'||x,'p','episode',1,1,'draft',0 FROM n"
        )
        c.exec_driver_sql(
            "INSERT INTO memory_revisions SELECT h.memory_id,r.revision,r.connection_id,"
            "r.event_id,r.digest,r.payload FROM memory_heads h CROSS JOIN memory_revisions r "
            "WHERE h.memory_id LIKE 'full_%' AND r.memory_id=?",
            (base.resource_id,),
        )
    for i in range(20):
        store.ingest("c", event().model_copy(update={"event_id": f"old{i}"}), project_id="p")
    store.register_connection("other", "healthy", "external_report")
    store.ingest("healthy", event(), project_id="other")
    assert store.process_pending_events() == 1
    assert store.overview("other")["events"][0]["processing"] == "done"
    assert store.overview("p")["events"][0]["processing"] == "capacity_limited"
    token = store.credential("p", "c", 0, rotate=True)["token"]
    assert store.event_receipt("c", "e", token)["processing"] == "capacity_limited"
    assert store.process_pending_events() == 0


def test_all_inventory_sections_page_without_silent_truncation(client, store):
    from agent_workbench.experience_memory.engine_models import Grant, Policy

    for i in range(101):
        cid = f"page_{i:03}"
        store.register_project(cid)
        store.register_connection("p", cid, "external_report")
        store.ingest(cid, event(), project_id="p")
        store.set_grant(
            "p",
            Grant(
                connection_id=cid,
                content_type="episode",
                purpose="index",
                enabled=True,
                expires_at=9999999999.0,
            ),
            expected_version=0,
        )
    while store.process_pending_events():
        pass
    # Policies are tied to each real memory generation, independently paginated.
    with store._transaction() as c:
        ids = [row[0] for row in c.exec_driver_sql("SELECT memory_id FROM memory_heads")]
    for mid in ids:
        store.change_memory("p", mid, "confirm_" + mid, 1, state="confirmed")
        store.set_policy(
            "p", mid, Policy(expires_at=9999999999.0), expected_generation=2, expected_version=0
        )
    first = client.get("/api/memory/projects").json()
    assert len(first["projects"]) == 100 and first["pagination"]["has_more"]
    second = client.get("/api/memory/projects?page=1").json()
    assert len(second["projects"]) == 3 and not second["pagination"]["has_more"]
    sections = ("connections", "events", "memories", "grants", "policies", "jobs")
    first = client.get("/api/memory/projects/p/overview").json()
    second = client.get(
        "/api/memory/projects/p/overview", params={name + "_page": "1" for name in sections}
    ).json()
    for name in sections:
        assert len(first[name]) == 100 and first["pagination"][name]["has_more"]
        assert second[name]
        assert second["pagination"][name]["has_more"] == (name == "jobs")
    memory = second["memories"][0]
    mid = memory["receipt"]["resource_id"]
    context = client.get("/api/memory/projects/p/memories/" + mid + "/context").json()
    assert context["policy"]["version"] == 1 and len(context["grants"]) == 1
    assert client.get("/api/memory/projects/other/memories/" + mid + "/context").status_code == 404
    cid = memory["content"]["connection_id"]
    assert (
        len(client.get("/api/memory/projects/p/connections/" + cid + "/grants").json()["grants"])
        == 1
    )


@pytest.mark.parametrize(
    "query", ["page=-1", "page=100001", "page=1&page=2", "page=NaN", "secret=1"]
)
def test_invalid_pagination_is_rejected(client, query):
    assert client.get("/api/memory/projects?" + query).status_code == 400
