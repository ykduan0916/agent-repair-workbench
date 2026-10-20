"""M4-R explicit index/search/cleanup controls, isolated from event intake."""

import os
from typing import Any, cast

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from agent_workbench.run_store import StoreError
from agent_workbench.run_store.location import state_directory

from .api import body, error, invoke
from .engine import EngineRequest, EngineService, EngineWebStore
from .store import WebMemoryStore

router = APIRouter(prefix="/api/memory/projects")


@router.post("/{project_id}/engine")
async def submit(project_id: str, request: Request) -> Response:
    raw = await body(request)
    if isinstance(raw, Response):
        return raw
    try:
        value = EngineRequest.model_validate_json(raw)
        service = cast(EngineService, request.app.state.memory_engine)
        report = await service.submit(project_id, value)
        return JSONResponse(report, status_code=202 if report["state"] == "running" else 200)
    except StoreError as exc:
        return error(exc.code, 429 if exc.code == "engine_busy" else 409)
    except ValueError:
        return error("invalid_request")
    except Exception:
        return error("engine_unavailable", 503)


@router.get("/{project_id}/engine/{request_id}")
async def status(project_id: str, request_id: str, request: Request) -> Response:
    def read(_store: WebMemoryStore) -> dict[str, Any]:
        store = EngineWebStore(state_directory())
        try:
            result = store.web_operation(project_id, request_id)
            if result["state"] == "running":
                service = cast(EngineService, request.app.state.memory_engine)
                try:
                    fd = service.fence()
                except StoreError:
                    return result
                try:
                    store.recover_operations()
                    return store.web_operation(project_id, request_id)
                finally:
                    os.close(fd)
            return result
        finally:
            store.close()

    return await invoke(request, read)
