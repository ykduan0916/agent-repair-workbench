"""Independent M4-R regressions for admission responsiveness and worker ownership."""

import asyncio
import os
import sqlite3
import threading
import time

import pytest

from agent_workbench.run_store import StoreError
from agent_workbench.web_memory.engine import EngineRequest, EngineService, EngineWebStore


def test_admission_sqlite_wait_does_not_block_event_loop(tmp_path):
    async def check():
        store = EngineWebStore(tmp_path)
        store.initialize()
        store.register_project("p")
        database = sqlite3.connect(store.path)
        database.execute("BEGIN IMMEDIATE")
        service = EngineService(tmp_path)
        ticks = []

        async def heartbeat():
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.02)

        pulse = asyncio.create_task(heartbeat())
        await asyncio.sleep(0.03)
        try:
            with pytest.raises(StoreError):
                await service.submit("p", EngineRequest(request_id="r", operation="cleanup"))
            await asyncio.sleep(0.03)
            assert max(b - a for a, b in zip(ticks, ticks[1:], strict=False)) < 0.3
        finally:
            pulse.cancel()
            await asyncio.gather(pulse, return_exceptions=True)
            database.rollback()
            database.close()
            await service.close()
            store.close()

    asyncio.run(check())


def test_repeated_cancellation_keeps_fence_until_actual_worker_exits(tmp_path):
    async def check():
        store = EngineWebStore(tmp_path)
        store.initialize()
        store.register_project("p")
        entered, release = threading.Event(), threading.Event()

        class Runtime:
            def cleanup(self, _project):
                entered.set()
                assert release.wait(5)
                return 1

        service = EngineService(tmp_path, factory=lambda *_: Runtime())
        other = EngineService(tmp_path)
        await service.submit("p", EngineRequest(request_id="r", operation="cleanup"))
        assert await asyncio.to_thread(entered.wait, 2)
        task = next(iter(service.tasks))
        try:
            task.cancel()
            await asyncio.sleep(0.03)
            task.cancel()
            await asyncio.sleep(0.03)
            with pytest.raises(StoreError, match="engine_busy"):
                fd = other.fence()
                os.close(fd)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await service.close()
            await other.close()
            store.close()

    asyncio.run(check())


def test_shutdown_cancellation_preserves_admission_handoff(tmp_path):
    async def check():
        store = EngineWebStore(tmp_path)
        store.initialize()
        store.register_project("p")
        service = EngineService(tmp_path)
        other = EngineService(tmp_path)
        entered, release, returned = threading.Event(), threading.Event(), threading.Event()
        original = service._admit
        captured = []

        def slow_admit(*args):
            result = original(*args)
            captured.append(result[1])
            entered.set()
            assert release.wait(5)
            returned.set()
            return result

        service._admit = slow_admit
        caller = asyncio.create_task(
            service.submit("p", EngineRequest(request_id="r", operation="cleanup"))
        )
        assert await asyncio.to_thread(entered.wait, 2)
        shutdown = asyncio.create_task(service.close())
        await asyncio.sleep(0.03)
        shutdown.cancel()
        await asyncio.sleep(0.03)
        release.set()
        assert await asyncio.to_thread(returned.wait, 2)
        await asyncio.gather(shutdown, caller, return_exceptions=True)
        try:
            await service.close()
            fd = other.fence()
            os.close(fd)
        finally:
            for fd in captured:
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            store.close()

    asyncio.run(check())


def test_admission_queue_is_bounded(tmp_path):
    async def check():
        service = EngineService(tmp_path)
        release = threading.Event()
        entered = 0
        lock = threading.Lock()

        def admit(*_args):
            nonlocal entered
            with lock:
                entered += 1
            assert release.wait(5)
            return {"state": "done"}, None

        service._admit = admit
        callers = [
            asyncio.create_task(
                service.submit("p", EngineRequest(request_id=f"r{i}", operation="cleanup"))
            )
            for i in range(4)
        ]
        try:
            for _ in range(100):
                if entered == 4:
                    break
                await asyncio.sleep(0.01)
            assert entered == 4
            with pytest.raises(StoreError, match="engine_busy"):
                await service.submit("p", EngineRequest(request_id="extra", operation="cleanup"))
            assert entered == 4
        finally:
            release.set()
            await asyncio.gather(*callers)
            await service.close()

    asyncio.run(check())
