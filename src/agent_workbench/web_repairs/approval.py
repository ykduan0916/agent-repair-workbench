"""Bounded B4 approval endpoints: record a decision or export one exact patch."""

import asyncio
import threading
from collections.abc import Callable
from typing import TypeVar
from uuid import UUID

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from agent_workbench.candidates.contracts import CandidateError
from agent_workbench.mcp_config import ROOT, digest
from agent_workbench.repair_store.decision_models import DecisionReceipt, DecisionRequest
from agent_workbench.repair_store.decisions import DecisionStore
from agent_workbench.run_store.location import state_directory
from agent_workbench.run_store.models import StoreError
from agent_workbench.web_runs.api import body as read_body

router = APIRouter(prefix="/api/repairs")
T = TypeVar("T")

_STATUS = {
    "repair_not_found": 404,
    "candidate_not_found": 404,
    "comparison_not_found": 404,
    "download_not_authorized": 403,
    "version_mismatch": 409,
    "decision_stale": 409,
    "decision_conflict": 409,
    "request_conflict": 409,
    "acceptance_requires_verified": 409,
    "comparison_not_finished": 409,
    "candidate_not_ready": 409,
    "invalid_decision": 400,
    "store_corrupt": 503,
    "approval_busy": 429,
    "repair_limit": 413,
    "decision_limit": 409,
}

_WORKERS = threading.BoundedSemaphore(4)


def _valid_id(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except (ValueError, AttributeError):
        return False


async def _bounded(call: Callable[[], T]) -> T:  # noqa: UP047
    def run() -> T:
        if not _WORKERS.acquire(blocking=False):
            raise StoreError("approval_busy")
        try:
            return call()
        finally:
            _WORKERS.release()

    return await asyncio.to_thread(run)


def _json_error(error: StoreError) -> Response:
    return JSONResponse({"error": error.code}, status_code=_STATUS.get(error.code, 503))


async def _read(task_id: str, candidate_id: str) -> Response:
    def run() -> DecisionReceipt:
        store = DecisionStore(state_directory())
        try:
            return store.decision(task_id, candidate_id)
        finally:
            store.close()

    try:
        return JSONResponse((await _bounded(run)).model_dump(mode="json"))
    except StoreError as error:
        return _json_error(error)
    except Exception:
        return JSONResponse({"error": "store_unavailable"}, status_code=503)


@router.get("/{task_id}/candidates/{candidate_id}/decision")
async def decision(task_id: str, candidate_id: str, request: Request) -> Response:
    if request.query_params or not _valid_id(task_id) or not _valid_id(candidate_id):
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    return await _read(task_id, candidate_id)


@router.post("/{task_id}/candidates/{candidate_id}/decision")
async def decide(task_id: str, candidate_id: str, request: Request) -> Response:
    if not _valid_id(task_id) or not _valid_id(candidate_id):
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    value = await read_body(request)
    if isinstance(value, Response):
        return value
    try:
        payload = DecisionRequest.model_validate(value)
    except ValueError:
        return JSONResponse({"error": "invalid_decision"}, status_code=400)

    def run() -> DecisionReceipt:
        store = DecisionStore(state_directory())
        try:
            return store.record(
                task_id,
                candidate_id,
                request_id=payload.request_id,
                decision=payload.decision,
                expected_revision=payload.expected_revision,
                expected_candidate_sha256=payload.expected_candidate_sha256,
                expected_comparison_sha256=payload.expected_comparison_sha256,
                image_root=ROOT,
            )
        finally:
            store.close()

    try:
        return JSONResponse((await _bounded(run)).model_dump(mode="json"))
    except StoreError as error:
        return _json_error(error)
    except (CandidateError, ValueError):
        return JSONResponse({"error": "invalid_decision"}, status_code=400)
    except Exception:
        return JSONResponse({"error": "store_unavailable"}, status_code=503)


@router.get("/{task_id}/candidates/{candidate_id}/download")
async def download(task_id: str, candidate_id: str, request: Request) -> Response:
    if request.query_params or not _valid_id(task_id) or not _valid_id(candidate_id):
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    def run() -> tuple[str, bytes]:
        store = DecisionStore(state_directory())
        try:
            return store.download(task_id, candidate_id, ROOT)
        finally:
            store.close()

    try:
        name, data = await _bounded(run)
        if len(data) > 1024 * 1024:
            return JSONResponse({"error": "repair_limit"}, status_code=413)
        return Response(
            content=data,
            media_type="text/x-diff; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{name}"',
                "Cache-Control": "no-store",
                "X-Task-ID": task_id,
                "X-Candidate-ID": candidate_id,
                "X-Patch-SHA256": digest(data),
            },
        )
    except StoreError as error:
        return _json_error(error)
    except (CandidateError, ValueError):
        return JSONResponse({"error": "decision_stale"}, status_code=409)
    except Exception:
        return JSONResponse({"error": "store_unavailable"}, status_code=503)
