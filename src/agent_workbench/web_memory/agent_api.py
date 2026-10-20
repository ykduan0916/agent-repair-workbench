"""Body-free M3-R projections; task history never contains experience text."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, Response

from agent_workbench.analysis_store.store import AnalysisStore
from agent_workbench.experience_memory.agent_models import TaskKind
from agent_workbench.experience_memory.agent_store import AgentMemoryStore
from agent_workbench.repair_store.store import RepairStore
from agent_workbench.run_store import StoreError

from .api import error, invoke
from .store import WebMemoryStore

router = APIRouter()


def usage(store: WebMemoryStore, kind: TaskKind, task_id: str) -> dict[str, Any]:
    table = "web_analysis_requests" if kind == "analysis" else "web_repair_requests"
    with store._transaction() as c:
        row = c.exec_driver_sql(
            f"SELECT memory_binding FROM {table} WHERE request_id=?", (task_id,)
        ).first()
        if row is None:
            parent_table = "analysis_summaries" if kind == "analysis" else "repair_tasks"
            if c.exec_driver_sql(
                f"SELECT 1 FROM {parent_table} WHERE task_id=?", (task_id,)
            ).first():
                return {"state": "disabled", "references": [], "uses": []}
            raise StoreError("not_found")
        if row[0] is None:
            return {"state": "disabled", "references": [], "uses": []}
        parent = (
            AnalysisStore(store.directory) if kind == "analysis" else RepairStore(store.directory)
        )
        try:
            finished = (
                parent._get(c, task_id).summary.state.phase == "finished"
                if isinstance(parent, AnalysisStore)
                else parent._read(c, task_id)[0].phase == "finished"
            )
        finally:
            parent.close()
        present = c.exec_driver_sql(
            "SELECT 1 FROM memory_task_bindings WHERE task_kind=? AND task_id=?", (kind, task_id)
        ).first()
        if not present:
            return {
                "state": "unavailable" if finished else "pending",
                "reason": "retrieval_not_started" if finished else "waiting",
                "references": [],
                "uses": [],
            }
    memory = AgentMemoryStore(store.directory)
    try:
        result = memory.task_usage(kind, task_id)
        if finished and result["state"] == "running":
            result = {**result, "state": "unknown", "reason": "retrieval_interrupted"}
        with memory._transaction() as c:
            references = []
            for reference in result["references"]:
                head = c.exec_driver_sql(
                    "SELECT state,version FROM memory_heads WHERE project_id=? AND memory_id=?",
                    (result["project_id"], reference["memory_id"]),
                ).first()
                current = (
                    "unavailable"
                    if head is None
                    else "changed"
                    if head[1] != reference["generation"] and head[0] == "confirmed"
                    else head[0]
                )
                references.append({**reference, "current_state": current})
            result = {**result, "references": references}
        return result
    finally:
        memory.close()


async def read(request: Request, kind: TaskKind, task_id: str) -> Response:
    try:
        if str(UUID(task_id)) != task_id:
            raise ValueError
    except ValueError:
        return error("invalid_request")
    return await invoke(request, lambda store: usage(store, kind, task_id))


@router.get("/api/analysis-runs/{task_id}/memory")
async def analysis(task_id: str, request: Request) -> Response:
    return await read(request, "analysis", task_id)


@router.get("/api/repair-runs/{task_id}/memory")
async def repair(task_id: str, request: Request) -> Response:
    return await read(request, "repair", task_id)
