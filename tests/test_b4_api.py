"""B4 HTTP boundary tests; no model, Docker, or project write-back."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from agent_workbench import api
from agent_workbench.repair_store import decision_models
from agent_workbench.web_repairs import approval

ORIGIN = "http://127.0.0.1:3010"


def receipt(task_id: str, candidate_id: str) -> decision_models.DecisionReceipt:
    return decision_models.DecisionReceipt(
        task_id=task_id,
        candidate_id=candidate_id,
        decision=None,
        candidate_sha256="a" * 64,
        comparison_sha256="b" * 64,
        revision=0,
        current_valid=True,
        can_accept=True,
        can_reject=True,
        can_download=False,
    )


class FakeDecisionStore:
    calls: list[tuple[str, ...]] = []
    task_id = str(uuid4())
    candidate_id = str(uuid4())
    value = receipt(task_id, candidate_id)

    def __init__(self, directory):
        self.directory = directory

    def close(self):
        return None

    def decision(self, task_id: str, candidate_id: str):
        self.calls.append(("get", task_id, candidate_id))
        return self.value

    def record(self, task_id: str, candidate_id: str, **kwargs):
        self.calls.append(("post", task_id, candidate_id, kwargs["request_id"]))
        return self.value

    def download(self, task_id: str, candidate_id: str, image_root):
        self.calls.append(("download", task_id, candidate_id))
        return "agent-repair-1.patch", b"diff --git a/a b/a\n"


@pytest.fixture
def client(monkeypatch, tmp_path):
    FakeDecisionStore.calls = []
    monkeypatch.setenv("AWB_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(approval, "DecisionStore", FakeDecisionStore)
    with TestClient(api.create_app(), base_url=ORIGIN) as value:
        yield value


def path(suffix: str = "decision") -> str:
    return (
        f"/api/repairs/{FakeDecisionStore.task_id}/candidates/"
        f"{FakeDecisionStore.candidate_id}/{suffix}"
    )


def body(**updates):
    return {
        "request_id": str(uuid4()),
        "decision": "accepted",
        "expected_revision": 0,
        "expected_candidate_sha256": "a" * 64,
        "expected_comparison_sha256": "b" * 64,
        **updates,
    }


def test_decision_get_post_and_download_are_local_and_strict(client):
    get = client.get(path(), headers={"origin": ORIGIN})
    assert get.status_code == 200 and get.json()["revision"] == 0
    post = client.post(path(), json=body(), headers={"origin": ORIGIN})
    assert post.status_code == 200 and post.json()["current_valid"] is True
    download = client.get(path("download"), headers={"origin": ORIGIN})
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/x-diff")
    assert download.content.startswith(b"diff --git")
    assert [item[0] for item in FakeDecisionStore.calls] == ["get", "post", "download"]


@pytest.mark.parametrize("method", ["get", "post"])
def test_decision_requires_exact_local_origin(client, method):
    kwargs = {"json": body()} if method == "post" else {}
    response = getattr(client, method)(path(), headers={"origin": "https://evil.invalid"}, **kwargs)
    assert response.status_code == 403
    assert FakeDecisionStore.calls == []


def test_decision_rejects_invalid_ids_queries_and_extra_body(client):
    assert (
        client.get(
            "/api/repairs/bad/candidates/bad/decision", headers={"origin": ORIGIN}
        ).status_code
        == 400
    )
    assert client.get(path() + "?debug=1", headers={"origin": ORIGIN}).status_code == 400
    assert (
        client.get(path("download") + "?format=raw", headers={"origin": ORIGIN}).status_code == 400
    )
    extra = client.post(path(), json=body(extra="reject"), headers={"origin": ORIGIN})
    assert extra.status_code == 400 and extra.json() == {"error": "invalid_decision"}
    assert FakeDecisionStore.calls == []


def test_decision_rejects_bad_content_type_and_host(client):
    bad_type = client.post(
        path(), content="{}", headers={"origin": ORIGIN, "content-type": "text/plain"}
    )
    assert bad_type.status_code == 415
    assert client.get(path(), headers={"origin": ORIGIN, "host": "evil.invalid"}).status_code == 400
    assert FakeDecisionStore.calls == []
