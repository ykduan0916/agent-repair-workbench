"""Loopback-only HTTP adapter for the bounded offline checker."""

import asyncio
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import cast

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from agent_workbench import __version__
from agent_workbench.checker import MAX_BYTES
from agent_workbench.cli import run_check
from agent_workbench.run_store.location import state_directory
from agent_workbench.web_analyses.service import AnalysisService
from agent_workbench.web_repairs.service import RepairService
from agent_workbench.web_runs.service import ReproductionService


def local_origins() -> frozenset[str]:
    port = os.environ.get("AWB_FRONTEND_PORT", "3010")
    if (
        not port.isascii()
        or not port.isdigit()
        or not 4 <= len(port) <= 5
        or not 1024 <= int(port) <= 65535
    ):
        raise ValueError("invalid_frontend_port")
    return frozenset({f"http://127.0.0.1:{int(port)}", f"http://localhost:{int(port)}"})


def error(code: str, status: int) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


def create_app() -> FastAPI:
    origins = local_origins()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.gate = asyncio.Semaphore(1)
        app.state.repair_reads = asyncio.Semaphore(2)
        app.state.workers = set()
        app.state.reproductions = ReproductionService(state_directory())
        app.state.analysis_runs = AnalysisService(state_directory())
        app.state.repair_runs = RepairService(state_directory())
        from agent_workbench.web_memory.engine import EngineService

        app.state.memory_engine = EngineService(state_directory())
        try:
            async with AsyncExitStack() as stack:
                from agent_workbench.web_memory.lifecycle import memory_lifespan

                await stack.enter_async_context(memory_lifespan(app))
                yield
        finally:
            await asyncio.gather(
                app.state.reproductions.close(),
                app.state.analysis_runs.close(),
                app.state.repair_runs.close(),
                app.state.memory_engine.close(),
            )
            if app.state.workers:
                await asyncio.gather(*app.state.workers, return_exceptions=True)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    app.state.local_origins = origins

    @app.middleware("http")
    async def browser_boundary(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        origin = request.headers.get("origin")
        if not re.fullmatch(
            r"(?:127\.0\.0\.1|localhost)(?::[0-9]{1,5})?", request.headers.get("host", "")
        ):
            response: Response = (
                error("forbidden_host", 403)
                if request.url.path.startswith("/api/repair-runs")
                else error("host_forbidden", 400)
            )
        elif (origin is not None and origin not in origins) or request.headers.get(
            "sec-fetch-site"
        ) == "cross-site":
            response = error(
                "forbidden_origin"
                if request.url.path.startswith("/api/repair-runs")
                else "origin_forbidden",
                403,
            )
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "checker_version": __version__}

    @app.post("/api/check")
    async def check(request: Request) -> Response:
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            return error("unsupported_media_type", 415)
        gate = cast(asyncio.Semaphore, request.app.state.gate)
        if gate.locked():
            return error("checker_busy", 429)
        await gate.acquire()
        transferred = False
        try:
            raw = bytearray()
            try:
                async with asyncio.timeout(5):
                    async for chunk in request.stream():
                        if len(raw) + len(chunk) > MAX_BYTES:
                            return error("input_limit_exceeded", 413)
                        raw.extend(chunk)
            except TimeoutError:
                return error("request_timeout", 408)
            except Exception:
                return error("request_unreadable", 400)

            async def execute() -> Response:
                try:
                    report = await asyncio.to_thread(run_check, bytes(raw), 5.0)
                    return JSONResponse(report.model_dump())
                except Exception:
                    return error("checker_unavailable", 503)
                finally:
                    gate.release()

            # The independent worker owns the slot. Cancelling the HTTP waiter
            # cannot free it before the bounded checker has actually finished.
            task = asyncio.create_task(execute())
            request.app.state.workers.add(task)
            task.add_done_callback(request.app.state.workers.discard)
            transferred = True
            return await asyncio.shield(task)
        finally:
            if not transferred:
                gate.release()

    from agent_workbench.analysis_store.api import router as analyses_router
    from agent_workbench.orchestration.catalog import router as contracts_router
    from agent_workbench.repair_reader.api import router as repairs_router
    from agent_workbench.run_store.api import router as runs_router
    from agent_workbench.web_analyses.api import router as analysis_runs_router
    from agent_workbench.web_repairs.api import router as repair_runs_router
    from agent_workbench.web_repairs.approval import router as approval_router
    from agent_workbench.web_runs.api import router as reproductions_router

    app.include_router(repair_runs_router)
    app.include_router(approval_router)
    app.include_router(repairs_router)
    app.include_router(analysis_runs_router)
    app.include_router(reproductions_router)
    app.include_router(contracts_router)
    app.include_router(runs_router)
    app.include_router(analyses_router)
    from agent_workbench.web_memory.agent_api import router as memory_agent_router
    from agent_workbench.web_memory.api import router as memory_router
    from agent_workbench.web_memory.engine_api import router as memory_engine_router
    from agent_workbench.web_memory.repair_draft_api import router as repair_draft_router

    app.include_router(repair_draft_router)
    app.include_router(memory_agent_router)
    app.include_router(memory_router)
    app.include_router(memory_engine_router)
    return app


app = create_app()
