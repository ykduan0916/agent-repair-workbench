"""BH-06 real ledgers/snapshots with deterministic synthetic model/oracle fixture."""

import asyncio
import sqlite3
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import tests.agents.test_repair_tool_bridge as tool_bridge
import tests.test_repair_reader as reader_tests
from agent_workbench.experience_memory import MemoryStore
from agent_workbench.experience_memory.repair_drafts import RepairDraftInput, RepairDraftStore
from agent_workbench.mcp_config import canonical, digest
from agent_workbench.repair_store.fencing import RepairFence
from agent_workbench.run_store import StoreError
from agent_workbench.web_memory.repair_draft_api import router

ctx = tool_bridge.ctx
completed = reader_tests.completed


@pytest.fixture
def setup(completed):
    ctx = completed
    store = RepairDraftStore(ctx.store.directory)
    memory = MemoryStore(ctx.store.directory)
    memory.register_project("project")
    memory.register_project("other")
    memory.register_connection("project", "local", "local_user")
    memory.register_connection("project", "external", "external_report")
    candidate = store.candidates(ctx.task_id)[0]
    comparison = store.comparisons(ctx.task_id)[0]
    payload = RepairDraftInput(
        request_id="save",
        project_id="project",
        connection_id="local",
        expected_decision_revision=1,
        expected_candidate_sha256=candidate.snapshot.tree_sha256,
        expected_comparison_sha256=digest(canonical(comparison.model_dump(mode="json"))),
    )
    yield ctx, store, memory, payload
    store.close()
    memory.close()


def decide(setup, decision="accepted", revision=0):
    ctx, store, _, payload = setup
    return store.record(
        ctx.task_id,
        ctx.candidate_id,
        request_id=str(uuid4()),
        decision=decision,
        expected_revision=revision,
        expected_candidate_sha256=payload.expected_candidate_sha256,
        expected_comparison_sha256=payload.expected_comparison_sha256,
        image_root=ctx.image,
    )


def save(setup, payload=None):
    ctx, store, _, original = setup
    return store.save_verified_repair_draft(
        ctx.task_id, ctx.candidate_id, payload or original, image_root=ctx.image
    )


def test_accepted_proof_creates_one_draft_and_never_confirms_or_authorizes(setup):
    ctx, store, memory, payload = setup
    decide(setup)
    result = save(setup)
    assert result["state"] == "draft" and result["source_current_valid"]
    assert result == save(setup)
    assert save(setup, payload.model_copy(update={"request_id": "another"})) == result
    view = memory.memory("project", result["memory_id"])
    assert view.content.tool_id == "lookup"
    assert view.content.source_version == ctx.baseline.ref.file_sha256
    assert "does not prove future versions pass" in view.content.body
    assert "return {" not in view.content.body
    with sqlite3.connect(memory.path) as c:
        assert c.execute("SELECT count(*) FROM memory_heads").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM memory_repair_drafts").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM memory_events").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM memory_grants").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM memory_engine_roots").fetchone()[0] == 0
    decide(setup, "rejected", 1)
    proof = store.proof(ctx.task_id, ctx.candidate_id, "project", image_root=ctx.image)
    assert proof["current_decision"] == "rejected" and not proof["source_current_valid"]
    assert not save(setup)["source_current_valid"]
    memory.change_memory("project", result["memory_id"], "delete", 1, state="deleted")
    proof = save(setup)
    assert proof["state"] == "deleted" and "body" not in str(proof)


@pytest.mark.parametrize(
    "kind",
    [
        "unaccepted",
        "rejected",
        "stale_revision",
        "candidate_hash",
        "comparison_hash",
        "external",
        "paused",
        "cross_project",
        "candidate_tamper",
        "image_tamper",
    ],
)
def test_negative_evidence_source_and_version_cases_do_not_write(setup, kind):
    ctx, store, memory, payload = setup
    if kind != "unaccepted":
        decide(setup, "rejected" if kind == "rejected" else "accepted")
    if kind == "stale_revision":
        payload = payload.model_copy(update={"expected_decision_revision": 2})
    if kind == "candidate_hash":
        payload = payload.model_copy(update={"expected_candidate_sha256": "a" * 64})
    if kind == "comparison_hash":
        payload = payload.model_copy(update={"expected_comparison_sha256": "b" * 64})
    if kind == "external":
        payload = payload.model_copy(update={"connection_id": "external"})
    if kind == "cross_project":
        payload = payload.model_copy(update={"project_id": "other"})
    if kind == "paused":
        memory.set_connection("project", "local", 1, enabled=False)
    if kind == "candidate_tamper":
        path = ctx.snapshots.root / ctx.candidate_id / "server.py"
        path.chmod(0o600)
        path.write_text("SECRET_DO_NOT_DISCLOSE")
    if kind == "image_tamper":
        (ctx.image / "containers/p0_06/supervisor.py").write_text("changed")
    with pytest.raises(StoreError):
        save(setup, payload)
    with sqlite3.connect(memory.path) as c:
        for table in ("memory_heads", "memory_events", "memory_repair_drafts", "memory_receipts"):
            assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_owner_fence_and_failure_rollback(setup, monkeypatch):
    ctx, store, memory, _ = setup
    decide(setup)
    with RepairFence(store.directory, ctx.task_id):
        with pytest.raises(StoreError, match="repair_busy"):
            save(setup)

    def fail(*args):
        raise StoreError("synthetic_write_failure")

    monkeypatch.setattr(MemoryStore, "_save_receipt", fail)
    with pytest.raises(StoreError, match="synthetic_write_failure"):
        save(setup)
    with sqlite3.connect(memory.path) as c:
        for table in (
            "memory_heads",
            "memory_events",
            "memory_repair_drafts",
            "memory_revisions",
            "memory_jobs",
        ):
            assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_idempotency_cannot_move_candidate_to_another_source_or_project(setup):
    _, _, _, payload = setup
    decide(setup)
    save(setup)
    for changes in (
        {"connection_id": "external"},
        {"project_id": "other"},
        {"expected_decision_revision": 2},
    ):
        with pytest.raises(StoreError, match="idempotency_conflict"):
            save(setup, payload.model_copy(update=changes | {"request_id": "new"}))


def test_api_requires_local_management_and_hides_raw_input(setup, monkeypatch):
    ctx, store, _, payload = setup
    decide(setup)
    monkeypatch.setattr("agent_workbench.web_memory.api.state_directory", lambda: store.directory)
    monkeypatch.setattr("agent_workbench.web_memory.repair_draft_api.ROOT", ctx.image)

    @asynccontextmanager
    async def lifespan(app):
        app.state.local_origins = {"http://127.0.0.1:3010"}
        app.state.memory_reads = asyncio.Semaphore(4)
        app.state.memory_workers = set()
        yield
        await asyncio.gather(*app.state.memory_workers, return_exceptions=True)

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    path = f"/api/repairs/{ctx.task_id}/candidates/{ctx.candidate_id}/memory-draft"
    headers = {"origin": "http://127.0.0.1:3010", "x-awb-local": "1"}
    with TestClient(app) as client:
        assert client.post(path, json=payload.model_dump(mode="json")).status_code == 403
        response = client.post(
            path, json=payload.model_dump(mode="json") | {"body": "PRIVATE_INPUT"}, headers=headers
        )
        assert response.status_code == 400 and "PRIVATE_INPUT" not in response.text
        assert (
            client.post(path, json=payload.model_dump(mode="json"), headers=headers).status_code
            == 200
        )
        assert client.get(path, params={"project_id": "project"}).json()["source_current_valid"]
        assert client.get(path, params={"project_id": "other"}).status_code == 404
        assert client.get(path + "?project_id=project&project_id=other").status_code == 400


def test_source_proof_endpoint_and_non_bh06_draft(setup, monkeypatch):
    from agent_workbench.experience_memory.models import DraftInput, EventInput

    ctx, store, memory, _ = setup
    decide(setup)
    result = save(setup)
    assert (
        store.memory_source("project", result["memory_id"], image_root=ctx.image)["source"][
            "task_id"
        ]
        == ctx.task_id
    )
    with pytest.raises(StoreError, match="not_found"):
        store.memory_source("other", result["memory_id"], image_root=ctx.image)
    memory.accept_event(
        "project",
        "local",
        EventInput(
            event_id="ordinary",
            event_type="user_correction",
            source_run_id="manual",
            occurred_at=1.0,
            outcome="unknown",
        ),
    )
    ordinary = memory.create_memory(
        "project",
        "ordinary",
        DraftInput(
            kind="episode",
            connection_id="local",
            event_id="ordinary",
            tool_id="t",
            source_version="v1",
            title="ordinary",
            body="Synthetic ordinary memory",
        ),
    )
    assert store.memory_source("project", ordinary.resource_id, image_root=ctx.image) == {
        "source": None
    }
    monkeypatch.setattr("agent_workbench.web_memory.api.state_directory", lambda: store.directory)
    monkeypatch.setattr("agent_workbench.web_memory.repair_draft_api.ROOT", ctx.image)

    @asynccontextmanager
    async def lifespan(app):
        app.state.memory_reads = asyncio.Semaphore(4)
        app.state.memory_workers = set()
        yield
        await asyncio.gather(*app.state.memory_workers, return_exceptions=True)

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    path = f"/api/memory/projects/project/memories/{result['memory_id']}/repair-source"
    with TestClient(app) as client:
        assert client.get(path).json()["source"]["source_current_valid"]
        decide(setup, "rejected", 1)
        proof = client.get(path).json()["source"]
        assert not proof["source_current_valid"] and proof["current_decision"] == "rejected"
        assert client.get(path.replace("/project/", "/other/")).status_code == 404
        assert client.get(path + "?extra=1").status_code == 400
