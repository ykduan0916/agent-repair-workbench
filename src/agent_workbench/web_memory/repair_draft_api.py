"""BH-06 local management actions; external source tokens never grant this permission."""

from typing import Any

from fastapi import APIRouter, Request, Response

from agent_workbench.experience_memory.repair_drafts import RepairDraftInput, RepairDraftStore
from agent_workbench.mcp_config import ROOT
from agent_workbench.run_store import StoreError
from agent_workbench.run_store.store import _uuid

from .api import body, error, invoke
from .store import WebMemoryStore

router = APIRouter()


@router.post("/api/repairs/{task_id}/candidates/{candidate_id}/memory-draft")
async def save(task_id: str, candidate_id: str, request: Request) -> Response:
    raw = await body(request)
    if isinstance(raw, Response):
        return raw
    try:
        _uuid(task_id)
        _uuid(candidate_id)
        payload = RepairDraftInput.model_validate_json(raw)
    except (ValueError, StoreError):
        return error("invalid_request")

    def action(memory: WebMemoryStore) -> dict[str, Any]:
        store = RepairDraftStore(memory.directory)
        try:
            return store.save_verified_repair_draft(task_id, candidate_id, payload, image_root=ROOT)
        finally:
            store.close()

    return await invoke(request, action)


@router.get("/api/repairs/{task_id}/candidates/{candidate_id}/memory-draft")
async def proof(task_id: str, candidate_id: str, request: Request) -> Response:
    try:
        _uuid(task_id)
        _uuid(candidate_id)
        if len(request.query_params.multi_items()) != 1 or "project_id" not in request.query_params:
            raise ValueError
    except (ValueError, StoreError):
        return error("invalid_request")

    def action(memory: WebMemoryStore) -> dict[str, Any]:
        store = RepairDraftStore(memory.directory)
        try:
            return store.proof(
                task_id, candidate_id, request.query_params["project_id"], image_root=ROOT
            )
        finally:
            store.close()

    return await invoke(request, action, paginated=True)


@router.get("/api/memory/projects/{project_id}/memories/{memory_id}/repair-source")
async def memory_source(project_id: str, memory_id: str, request: Request) -> Response:
    def action(memory: WebMemoryStore) -> dict[str, Any]:
        store = RepairDraftStore(memory.directory)
        try:
            return store.memory_source(project_id, memory_id, image_root=ROOT)
        finally:
            store.close()

    return await invoke(request, action)
