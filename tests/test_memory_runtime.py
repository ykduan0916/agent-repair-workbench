"""M2-R transport validation and actual subprocess/watchdog resource tests, no external models."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from agent_workbench.experience_memory.engine_store import EngineStore
from agent_workbench.providers.cognee import runtime as runtime_module
from agent_workbench.providers.cognee.gateway import Gateway, Providers
from agent_workbench.providers.cognee.runtime import CogneeRuntime
from agent_workbench.run_store import StoreError
from tests.test_memory_engine import memory, prepare_index, providers
from tests.test_memory_engine import state as engine_state


@pytest.fixture
def state(tmp_path):
    yield from engine_state.__wrapped__(tmp_path)


@pytest.mark.parametrize(
    "data",
    [
        [],
        [{"embedding": [0.0]}],
        [{"embedding": [float("nan")] * 1024}],
        [{"embedding": [True] * 1024}],
        [{"embedding": [0.0] * 1024, "index": 1}],
        [{"embedding": [0.0] * 1024, "index": False}],
    ],
)
def test_embedding_validation_after_usage_settlement(state, monkeypatch, data):
    store, _ = state
    lease = store.claim_job("p", job_id=prepare_index(store, "p", memory(store).resource_id))
    gate = Gateway(store, "p", lease, providers(), time.monotonic() + 5)

    async def response(*args):
        return 200, json.dumps({"usage": {"total_tokens": 30}, "data": data}).encode()

    monkeypatch.setattr(gate, "request", response)
    assert gate.forward("/v1/embeddings", b'{"input":"synthetic"}')[0] == 503
    assert gate.stopped.is_set()
    bill = store.accounting("p", lease.job_id)
    assert bill["calls"][0]["actual_tokens"] == 30
    assert bill["failure_code"] == "embedding_invalid"


def test_configuration_binding_rejects_false_cleanup(state, tmp_path):
    store, _ = state
    root = tmp_path / "engine"
    CogneeRuntime(store, root, Path(sys.executable), providers())
    with pytest.raises(StoreError, match="engine_root_changed"):
        CogneeRuntime(store, tmp_path / "another", Path(sys.executable), providers())
    other = EngineStore(tmp_path / "other_db")
    other.initialize()
    with pytest.raises(StoreError, match="engine_authority_changed"):
        CogneeRuntime(other, root, Path(sys.executable), providers())
    other.close()


def test_embedding_profile_changed_requires_reindex(state):
    from tests.test_memory_engine import index, query

    store, _ = state
    index(store, memory(store))
    changed = Providers(
        "http://127.0.0.1/chat", "key", "model", "http://127.0.0.2/embed", "key", "bge-m3", True
    )
    with pytest.raises(StoreError, match="embedding_profile_changed"):
        store.prepare_search("p", query(), profile_digest=changed.embedding_fingerprint)
    # A key rotation must not invalidate the model-space signature.
    original = providers()
    rotated = Providers(
        original.model_endpoint,
        "rotated",
        original.model_name,
        original.embedding_endpoint,
        "rotated",
        "bge-m3",
        True,
    )
    assert rotated.embedding_fingerprint == original.embedding_fingerprint


def test_cancel_in_last_result_gap_cannot_publish(state, tmp_path, monkeypatch):
    store, _ = state
    runtime = CogneeRuntime(store, tmp_path / "engine", Path(sys.executable), providers())
    cancel = threading.Event()

    def worker(*args):
        cancel.set()
        return {"dataset_id": "dataset", "data_id": "data"}

    monkeypatch.setattr(runtime, "_worker", worker)
    with pytest.raises(StoreError, match="index_failed"):
        runtime.index("p", memory(store).resource_id, cancel=cancel)
    with store._transaction() as c:
        assert (
            c.exec_driver_sql(
                "SELECT count(*) FROM memory_engine_roots WHERE state='ready'"
            ).scalar()
            == 0
        )


def test_real_child_cancel_reaps_group_and_releases_writer(state, tmp_path, monkeypatch):
    store, _ = state
    runtime = CogneeRuntime(store, tmp_path / "engine", Path(sys.executable), providers())
    native = subprocess.Popen
    children = []

    def sleeping_child(args, **kwargs):
        # Real process with the exact session/fd/environment controls of the adapter.
        assert "test-key" not in repr(kwargs["env"])
        child = native([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(runtime_module.subprocess, "Popen", sleeping_child)
    cancel = threading.Event()
    timer = threading.Timer(0.2, cancel.set)
    timer.start()
    try:
        with pytest.raises(StoreError, match="index_failed"):
            runtime.index("p", memory(store).resource_id, cancel=cancel)
    finally:
        timer.cancel()
    assert len(children) == 1 and children[0].poll() is not None
    with runtime.writer():
        pass


@pytest.mark.parametrize("mode", ["parent_death", "deadline"])
def test_real_watchdog_kills_orphan_group(tmp_path, mode):
    watchdog = Path(runtime_module.__file__).with_name("watchdog.py")
    ready = tmp_path / "pid"
    # Parent owns only the pipe writer. Child and watchdog share a new process group.
    launcher = tmp_path / "launcher.py"
    launcher.write_text("""import os, subprocess, sys, time
r,w=os.pipe()
child=subprocess.Popen([sys.executable, sys.argv[1], str(r), sys.argv[3]],
                       pass_fds=(r,), start_new_session=True)
open(sys.argv[2], 'w').write(str(child.pid))
time.sleep(60)
""")
    parent = subprocess.Popen(
        [
            sys.executable,
            str(launcher),
            str(watchdog),
            str(ready),
            "2" if mode == "deadline" else "20",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pid = None
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        pid = int(ready.read_text())
        if mode == "parent_death":
            parent.kill()
            parent.wait(timeout=3)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            proc = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
            )
            if not proc.stdout.strip() or proc.stdout.strip().startswith("Z"):
                break
            time.sleep(0.05)
        else:
            pytest.fail("watchdog stayed alive")
    finally:
        parent.kill()
        parent.wait(timeout=3)
        if pid is not None:
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_reindex_new_profile_with_smaller_uuid_is_selected(state, monkeypatch):
    from uuid import UUID

    from agent_workbench.experience_memory import common
    from tests.test_memory_engine import query

    store, _ = state
    item = memory(store)
    ids = iter(
        [UUID("ffffffff-ffff-ffff-ffff-ffffffffffff"), UUID("00000000-0000-0000-0000-000000000001")]
    )
    monkeypatch.setattr(common, "uuid4", lambda: next(ids))
    jobs = []
    for fingerprint in ("f" * 64, providers().embedding_fingerprint):
        job = store.prepare_index("p", item.resource_id, profile_digest=fingerprint)
        lease = store.claim_job("p", job_id=job)
        store.publish_index("p", lease, "ds_" + job, "data_" + job)
        store.finish_job("p", lease, succeeded=True)
        jobs.append(job)
    monkeypatch.undo()
    job = store.prepare_search("p", query(), profile_digest=providers().embedding_fingerprint)
    lease = store.claim_job("p", job_id=job)
    assert store.snapshot("p", lease)["refs"][0]["root_id"] == jobs[1]


def test_cleanup_needs_no_provider_configuration(state, tmp_path):
    store, _ = state
    runtime = CogneeRuntime(store, tmp_path / "engine", Path(sys.executable), None)
    assert runtime.cleanup("p") == 0
    with pytest.raises(StoreError, match="provider_configuration_required"):
        runtime.index("p", memory(store).resource_id)


@pytest.mark.skipif(os.environ.get("AWB_COGNEE_CONTRACT") != "1", reason="opt-in pinned SDK")
def test_installed_sqlite_cache_failure_never_falls_back(tmp_path):
    root = Path(__file__).resolve().parents[1]
    sdk = root / "artifacts/mr01/venv/bin/python"
    code = """import importlib, os
os.umask(0o077)
module = importlib.import_module('cognee.infrastructure.databases.cache.get_cache_engine')
def forbidden(*args, **kwargs):
    raise AssertionError('filesystem_fallback')
module.FSCacheAdapter = forbidden
try:
    module.get_cache_engine()
except AssertionError:
    raise
except Exception:
    pass
else:
    raise AssertionError('invalid_cache_accepted')
"""
    env = {
        "HOME": str(tmp_path),
        "CACHE_BACKEND": "sqlite",
        "CACHE_DB_URL": "invalid-url",
        "COGNEE_LOG_FILE": "false",
        "TELEMETRY_DISABLED": "true",
        "COGNEE_TELEMETRY_ENABLED": "false",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "SYSTEM_ROOT_DIRECTORY": str(tmp_path / "system"),
        "DATA_ROOT_DIRECTORY": str(tmp_path / "data"),
    }
    result = subprocess.run(
        [str(sdk), "-c", code],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=45,
    )
    assert result.returncode == 0


def test_cli_loads_explicit_embedding_revision(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from ops.memory_engine import load_providers

    model = tmp_path / "model.json"
    embedding = tmp_path / "embedding.json"
    model.write_text(
        json.dumps(
            {
                "AWB_MODEL_BASE_URL": "http://127.0.0.1:9",
                "AWB_MODEL_API_KEY": "dummy",
                "AWB_MODEL_NAME": "configured-model",
            }
        )
    )
    model.chmod(0o600)
    config = {
        "endpoint": "http://127.0.0.1:9/embed",
        "api_key": "dummy",
        "model": "bge-m3",
        "dimensions": 1024,
        "revision": "deployment-v2",
    }
    embedding.write_text(json.dumps(config))
    embedding.chmod(0o600)
    first = load_providers(model, embedding, profile=True)
    assert first.embedding_revision == "deployment-v2"
    config["revision"] = "deployment-v3"
    embedding.write_text(json.dumps(config))
    second = load_providers(model, embedding, profile=True)
    assert first.embedding_fingerprint != second.embedding_fingerprint
    config["dimensions"] = 1024.0
    embedding.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="invalid_embedding_configuration"):
        load_providers(model, embedding, profile=True)


@pytest.mark.skipif(os.environ.get("AWB_COGNEE_CONTRACT") != "1", reason="opt-in pinned SDK")
def test_installed_add_keeps_paths_and_urls_literal(tmp_path):
    root = Path(__file__).resolve().parents[1]
    sdk = root / "artifacts/mr01/venv/bin/python"
    code = """import asyncio, os
from pathlib import Path
from urllib.parse import urlparse
os.umask(0o077)
import cognee
from cognee.tasks.ingestion.data_item import DataItem
async def run():
    secret = Path.cwd() / 'not_authorized.txt'
    secret.write_text('synthetic file content must not be ingested')
    values = [str(secret), 'http://127.0.0.1:9/do-not-fetch', 's3://do-not-fetch/body']
    for i,value in enumerate(values):
        name = 'literal_' + str(i)
        await cognee.add(DataItem(data=value, literal_text=True), dataset_name=name)
        ds = next(d for d in await cognee.datasets.list_datasets() if d.name == name)
        data = await cognee.datasets.list_data(ds.id)
        assert len(data) == 1
        location = data[0].raw_data_location
        location = urlparse(location).path if location.startswith('file:') else location
        assert Path(location).read_text() == value
asyncio.run(run())
"""
    env = {
        "HOME": str(tmp_path),
        "CACHE_BACKEND": "sqlite",
        "ALLOW_HTTP_REQUESTS": "false",
        "COGNEE_SKIP_CONNECTION_TEST": "true",
        "COGNEE_LOG_FILE": "false",
        "TELEMETRY_DISABLED": "true",
        "COGNEE_TELEMETRY_ENABLED": "false",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "SYSTEM_ROOT_DIRECTORY": str(tmp_path / "system"),
        "DATA_ROOT_DIRECTORY": str(tmp_path / "data"),
        "DB_PROVIDER": "sqlite",
        "LLM_PROVIDER": "custom",
        "LLM_MODEL": "openai/configured-model",
        "LLM_API_KEY": "dummy",
        "LLM_ENDPOINT": "http://127.0.0.1:9/v1",
        "EMBEDDING_PROVIDER": "openai_compatible",
        "EMBEDDING_MODEL": "bge-m3",
        "EMBEDDING_API_KEY": "dummy",
        "EMBEDDING_ENDPOINT": "http://127.0.0.1:9/v1",
        "EMBEDDING_DIMENSIONS": "1024",
    }
    result = subprocess.run(
        [str(sdk), "-c", code],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=45,
    )
    assert result.returncode == 0
