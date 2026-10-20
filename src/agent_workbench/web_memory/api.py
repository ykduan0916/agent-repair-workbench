"""M4-R loopback management and source-bound external event HTTP boundary."""

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from agent_workbench.checker import parse_json_bytes
from agent_workbench.experience_memory.common import encode, key
from agent_workbench.experience_memory.models import EventInput
from agent_workbench.run_store import StoreError
from agent_workbench.run_store.location import state_directory

from .models import (
    ChangeMemory,
    ConnectionChange,
    ConnectionInput,
    CreateMemory,
    CredentialChange,
    GrantInput,
    PolicyInput,
    ProjectInput,
)
from .store import WebMemoryStore

router = APIRouter()


def error(code: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status)


async def body(request: Request, *, external: bool = False) -> bytes | Response:
    if not external and (
        request.headers.get("origin") not in request.app.state.local_origins
        or request.headers.get("x-awb-local") != "1"
    ):
        return error("forbidden_origin", 403)
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        return error("unsupported_media_type", 415)
    raw = bytearray()
    try:
        async with asyncio.timeout(5):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > 32768:
                    return error("input_limit_exceeded", 413)
                raw.extend(chunk)
        value = parse_json_bytes(bytes(raw))
        if not isinstance(value, dict):
            return error("invalid_request")
        return encode(value)
    except TimeoutError:
        return error("request_timeout", 408)
    except Exception:
        return error("invalid_request")


async def invoke(
    request: Request, action: Callable[[WebMemoryStore], Any], *, paginated: bool = False
) -> Response:
    if request.query_params and not paginated:
        return error("invalid_request")
    gate: asyncio.Semaphore = request.app.state.memory_reads
    if gate.locked():
        return error("busy", 429)
    await gate.acquire()

    def run() -> Any:
        store = WebMemoryStore(state_directory())
        try:
            return action(store)
        finally:
            store.close()

    async def owned() -> Response:
        try:
            result = await asyncio.to_thread(run)
            if isinstance(result, BaseModel):
                result = result.model_dump(mode="json")
            return JSONResponse(result)
        except StoreError as exc:
            code = exc.code
            status = (
                401
                if code == "unauthorized"
                else 404
                if code == "not_found"
                else 503
                if code in ("schema_incompatible", "store_unavailable", "store_busy")
                else 409
            )
            return error(code, status)
        except (ValueError, ValidationError):
            return error("invalid_request")
        except Exception:
            return error("store_unavailable", 503)
        finally:
            gate.release()

    task = asyncio.create_task(owned())
    request.app.state.memory_workers.add(task)
    task.add_done_callback(request.app.state.memory_workers.discard)
    return await asyncio.shield(task)


async def mutation[T: BaseModel](
    request: Request, model: type[T], action: Callable[[WebMemoryStore, T], Any]
) -> Response:
    raw = await body(request)
    if isinstance(raw, Response):
        return raw
    try:
        parsed = model.model_validate_json(raw)
    except ValueError:
        return error("invalid_request")
    return await invoke(request, lambda store: action(store, parsed))


def pagination(request: Request, allowed: set[str]) -> dict[str, int]:
    items = list(request.query_params.multi_items())
    if len(items) != len(set(name for name, _ in items)):
        raise ValueError("duplicate_page")
    result = {}
    for name, value in items:
        if name not in allowed or not value.isascii() or not value.isdecimal() or len(value) > 6:
            raise ValueError("invalid_page")
        page = int(value)
        if page > 100000:
            raise ValueError("invalid_page")
        result[name] = page
    return result


@router.get("/api/memory/projects")
async def projects(request: Request) -> Response:
    try:
        pages = pagination(request, {"page"})
    except ValueError:
        return error("invalid_request")
    return await invoke(request, lambda s: s.projects(page=pages.get("page", 0)), paginated=True)


@router.post("/api/memory/projects")
async def create_project(request: Request) -> Response:
    def action(s: WebMemoryStore, p: ProjectInput) -> dict[str, str]:
        s.register_project(p.project_id)
        return {"project_id": p.project_id}

    return await mutation(request, ProjectInput, action)


@router.get("/api/memory/projects/{project_id}/overview")
async def overview(project_id: str, request: Request) -> Response:
    try:
        parsed = pagination(
            request,
            {
                name + "_page"
                for name in ("connections", "events", "memories", "grants", "policies", "jobs")
            },
        )
    except ValueError:
        return error("invalid_request")
    pages = {name.removesuffix("_page"): value for name, value in parsed.items()}
    return await invoke(request, lambda s: s.overview(project_id, pages=pages), paginated=True)


@router.post("/api/memory/projects/{project_id}/connections")
async def create_connection(project_id: str, request: Request) -> Response:
    def action(s: WebMemoryStore, p: ConnectionInput) -> dict[str, str]:
        s.register_connection(project_id, p.connection_id, p.origin)
        return {"connection_id": p.connection_id}

    return await mutation(request, ConnectionInput, action)


@router.patch("/api/memory/projects/{project_id}/connections/{connection_id}")
async def change_connection(project_id: str, connection_id: str, request: Request) -> Response:
    return await mutation(
        request,
        ConnectionChange,
        lambda s, p: {
            "version": s.set_connection(
                project_id, connection_id, p.expected_version, enabled=p.enabled
            )
        },
    )


@router.post("/api/memory/projects/{project_id}/connections/{connection_id}/credential")
async def credential(project_id: str, connection_id: str, request: Request) -> Response:
    return await mutation(
        request,
        CredentialChange,
        lambda s, p: s.credential(
            project_id, connection_id, p.expected_version, rotate=p.action == "rotate"
        ),
    )


@router.post("/api/memory/projects/{project_id}/connections/{connection_id}/events")
async def local_event(project_id: str, connection_id: str, request: Request) -> Response:
    return await mutation(
        request, EventInput, lambda s, p: s.ingest(connection_id, p, project_id=project_id)
    )


@router.post("/api/memory/projects/{project_id}/connections/{connection_id}/process")
async def process(project_id: str, connection_id: str, request: Request) -> Response:
    raw = await body(request)
    if isinstance(raw, Response):
        return raw
    if raw != b"{}":
        return error("invalid_request")
    return await invoke(
        request, lambda s: {"processed": s.process_pending_events(project_id, connection_id)}
    )


@router.post("/api/memory/projects/{project_id}/memories")
async def create_memory(project_id: str, request: Request) -> Response:
    return await mutation(
        request, CreateMemory, lambda s, p: s.create_memory(project_id, p.request_id, p.content)
    )


@router.get("/api/memory/projects/{project_id}/memories/{memory_id}")
async def get_memory(project_id: str, memory_id: str, request: Request) -> Response:
    return await invoke(request, lambda s: s.memory(project_id, memory_id))


@router.patch("/api/memory/projects/{project_id}/memories/{memory_id}")
async def change_memory(project_id: str, memory_id: str, request: Request) -> Response:
    return await mutation(
        request,
        ChangeMemory,
        lambda s, p: s.change_memory(
            project_id,
            memory_id,
            p.request_id,
            p.expected_version,
            state=p.state,
            content=p.content,
        ),
    )


@router.post("/api/memory/projects/{project_id}/grants")
async def grant(project_id: str, request: Request) -> Response:
    return await mutation(
        request,
        GrantInput,
        lambda s, p: {
            "version": s.set_grant(project_id, p.grant, expected_version=p.expected_version)
        },
    )


@router.post("/api/memory/projects/{project_id}/memories/{memory_id}/policy")
async def policy(project_id: str, memory_id: str, request: Request) -> Response:
    return await mutation(
        request,
        PolicyInput,
        lambda s, p: {
            "version": s.set_policy(
                project_id,
                memory_id,
                p.policy,
                expected_generation=p.expected_generation,
                expected_version=p.expected_version,
            )
        },
    )


def bearer(request: Request) -> str:
    value = request.headers.get("authorization", "")
    return value[7:] if value.startswith("Bearer ") else ""


@router.post("/api/integrations/{connection_id}/events")
async def external_event(connection_id: str, request: Request) -> Response:
    raw = await body(request, external=True)
    if isinstance(raw, Response):
        return raw
    try:
        key(connection_id)
        parsed = EventInput.model_validate_json(raw)
    except (ValueError, StoreError):
        return error("invalid_request")
    return await invoke(request, lambda s: s.ingest(connection_id, parsed, token=bearer(request)))


@router.get("/api/integrations/{connection_id}/events/{event_id}")
async def event_status(connection_id: str, event_id: str, request: Request) -> Response:
    return await invoke(
        request, lambda s: s.event_receipt(connection_id, event_id, bearer(request))
    )


@router.get("/api/memory/projects/{project_id}/memories/{memory_id}/context")
async def memory_context(project_id: str, memory_id: str, request: Request) -> Response:
    return await invoke(request, lambda s: s.memory_context(project_id, memory_id))


@router.get("/api/memory/projects/{project_id}/connections/{connection_id}/grants")
async def source_grants(project_id: str, connection_id: str, request: Request) -> Response:
    return await invoke(request, lambda s: s.source_grants(project_id, connection_id))
