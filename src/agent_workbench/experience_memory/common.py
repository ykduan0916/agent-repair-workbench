"""M1-R transaction helpers; all errors are safe codes."""

import hashlib
import json
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy import Connection, RowMapping

from agent_workbench.run_store import StoreError

from .models import Key

Row = RowMapping


def key(value: str) -> str:
    try:
        return TypeAdapter(Key).validate_python(value, strict=True)
    except ValidationError:
        raise StoreError("invalid_id") from None


def validate[M: BaseModel](model: type[M], value: M) -> M:
    try:
        return model.model_validate_json(value.model_dump_json())
    except (ValidationError, ValueError, TypeError, AttributeError):
        raise StoreError("invalid_payload") from None


def encode(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def sha(value: bytes | str) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def one(c: Connection, sql: str, *args: Any) -> Row:
    row = c.exec_driver_sql(sql, args).mappings().first()
    if row is None:
        raise StoreError("not_found")
    return row


def project(c: Connection, project_id: str) -> None:
    one(c, "SELECT project_id FROM memory_projects WHERE project_id=?", key(project_id))


def connection(c: Connection, project_id: str, connection_id: str, *, active: bool = True) -> Row:
    row = one(
        c,
        "SELECT * FROM memory_connections WHERE project_id=? AND connection_id=?",
        key(project_id),
        key(connection_id),
    )
    if active and not row["enabled"]:
        raise StoreError("connection_paused")
    return row


def head(c: Connection, project_id: str, memory_id: str) -> Row:
    return one(
        c,
        "SELECT * FROM memory_heads WHERE project_id=? AND memory_id=?",
        key(project_id),
        key(memory_id),
    )


def enqueue(
    c: Connection,
    project_id: str,
    kind: str,
    connection_id: str,
    event_id: str,
    now: float,
    memory_id: str | None = None,
    generation: int = 0,
) -> str:
    count = c.exec_driver_sql(
        "SELECT count(*) FROM memory_jobs WHERE project_id=? AND state IN ('pending','leased')",
        (project_id,),
    ).scalar_one()
    if count >= 1000:
        raise StoreError("queue_limit")
    job_id = str(uuid4())
    c.exec_driver_sql(
        "INSERT INTO memory_jobs(job_id,project_id,kind,memory_id,connection_id,event_id,"
        "generation,state,created_at) VALUES(?,?,?,?,?,?,?,'pending',?)",
        (job_id, project_id, kind, memory_id, connection_id, event_id, generation, now),
    )
    return job_id
