"""M4-R engine admission is persistent and never retries unknown model work."""

import asyncio
import threading

import pytest

from agent_workbench.run_store import StoreError
from agent_workbench.web_memory.engine import EngineRequest, EngineService, EngineWebStore


@pytest.fixture
def store(tmp_path):
    value = EngineWebStore(tmp_path)
    value.initialize()
    value.register_project("p")
    yield value
    value.close()


def test_web_operation_idempotency_and_recovery(store):
    request = EngineRequest(request_id="r", operation="cleanup")
    assert store.admit_operation("p", request)
    assert not store.admit_operation("p", request)
    with pytest.raises(StoreError, match="idempotency_conflict"):
        store.admit_operation("p", EngineRequest(request_id="r", operation="index", memory_id="m"))
    store.recover_operations()
    assert store.web_operation("p", "r")["state"] == "unknown"
    assert not store.admit_operation("p", request)
    with pytest.raises(StoreError):
        store.web_operation("other", "r")


def test_shape_excludes_ambiguous_actions():
    with pytest.raises(ValueError):
        EngineRequest(request_id="r", operation="cleanup", memory_id="m")
    with pytest.raises(ValueError):
        EngineRequest(request_id="r", operation="search")


def test_submit_survives_client_and_single_owner(store):
    async def run():
        entered, release = threading.Event(), threading.Event()
        calls = []

        class Runtime:
            def cleanup(self, project):
                calls.append(project)
                entered.set()
                assert release.wait(5)
                return 2

        service = EngineService(store.path.parent, factory=lambda *_: Runtime())
        other = EngineService(store.path.parent, factory=lambda *_: Runtime())
        request = EngineRequest(request_id="r", operation="cleanup")
        report = await service.submit("p", request)
        assert report["state"] == "running"
        assert await asyncio.to_thread(entered.wait, 5)
        assert (await other.submit("p", request))["state"] == "running"
        with pytest.raises(StoreError, match="engine_busy"):
            await other.submit("p", EngineRequest(request_id="next", operation="cleanup"))
        release.set()
        await service.close()
        assert store.web_operation("p", "r")["result"] == {"removed_roots": 2}
        assert (await other.submit("p", request))["state"] == "done"
        assert calls == ["p"]
        await other.close()

    asyncio.run(run())


def test_failure_durable_and_not_replayed(store):
    async def run():
        calls = []

        def factory(*_):
            calls.append(1)
            raise RuntimeError("PRIVATE")

        service = EngineService(store.path.parent, factory=factory)
        request = EngineRequest(request_id="r", operation="cleanup")
        await service.submit("p", request)
        await asyncio.gather(*service.tasks)
        report = await service.submit("p", request)
        assert report["state"] == "unknown"
        assert "PRIVATE" not in str(report)
        assert len(calls) == 1
        await service.close()

    asyncio.run(run())
