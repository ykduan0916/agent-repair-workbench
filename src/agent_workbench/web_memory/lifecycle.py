"""Bounded durable-event sweeper, independent of request completion."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from agent_workbench.run_store import StoreError
from agent_workbench.run_store.location import state_directory

from .store import WebMemoryStore


def sweep() -> str:
    store = None
    try:
        store = WebMemoryStore(state_directory())
        store.process_pending_events()
        return "ready"
    except StoreError as exc:
        return exc.code
    except Exception:
        return "processing_unavailable"
    finally:
        if store is not None:
            store.close()


@asynccontextmanager
async def memory_lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.memory_reads = asyncio.Semaphore(4)
    app.state.memory_workers = set()
    stop = asyncio.Event()

    async def loop() -> None:
        while not stop.is_set():
            app.state.memory_processing_status = await asyncio.to_thread(sweep)
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                pass

    worker = asyncio.create_task(loop())
    try:
        yield
    finally:
        stop.set()
        await worker
        await asyncio.gather(*app.state.memory_workers, return_exceptions=True)
