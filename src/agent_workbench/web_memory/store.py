"""M4-R credentials, atomic ingestion and deterministic event processing."""

import hmac
import json
import re
import secrets
from typing import Any
from uuid import uuid4

from sqlalchemy import Connection

from agent_workbench.experience_memory.common import (
    connection,
    encode,
    enqueue,
    key,
    one,
    project,
    sha,
    validate,
)
from agent_workbench.experience_memory.engine_store import EngineStore
from agent_workbench.experience_memory.models import DraftInput, EventInput
from agent_workbench.run_store import StoreError


class WebMemoryStore(EngineStore):
    def projects(self, *, page: int = 0) -> dict[str, Any]:
        self._page(page)
        with self._transaction() as c:
            items = [
                dict(r)
                for r in c.exec_driver_sql(
                    "SELECT * FROM memory_projects ORDER BY created_at DESC,project_id "
                    "LIMIT 101 OFFSET ?",
                    (page * 100,),
                ).mappings()
            ]
        return {"projects": items[:100], "pagination": self._pagination(page, len(items))}

    @staticmethod
    def _page(value: int) -> None:
        if type(value) is not int or not 0 <= value <= 100000:
            raise StoreError("invalid_page")

    @staticmethod
    def _pagination(page: int, length: int) -> dict[str, Any]:
        return {
            "page": page,
            "has_more": length > 100,
            "next_page": page + 1 if length > 100 else None,
        }

    def credential(
        self, project_id: str, connection_id: str, expected_version: int, *, rotate: bool
    ) -> dict[str, Any]:
        token = secrets.token_urlsafe(32) if rotate else None
        with self._transaction(write=True) as c:
            source = connection(c, project_id, connection_id, active=False)
            if source["origin"] != "external_report":
                raise StoreError("external_connection_required")
            row = c.exec_driver_sql(
                "SELECT version FROM memory_credentials WHERE connection_id=?", (connection_id,)
            ).first()
            version = row[0] if row else 0
            if type(expected_version) is not int or version != expected_version:
                raise StoreError("version_conflict")
            c.exec_driver_sql(
                "INSERT INTO memory_credentials VALUES(?,?,?) ON CONFLICT(connection_id) DO "
                "UPDATE SET version=excluded.version,token_hash=excluded.token_hash",
                (connection_id, version + 1, sha(token) if token else None),
            )
            # Source generation changes invalidate any in-flight authorization snapshot.
            c.exec_driver_sql(
                "UPDATE memory_connections SET version=version+1 WHERE connection_id=?",
                (connection_id,),
            )
        result: dict[str, Any] = {"credential_version": version + 1, "credential_active": rotate}
        if token:
            result["token"] = token
        return result

    @staticmethod
    def _authenticate(c: Connection, connection_id: str, token: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
            raise StoreError("unauthorized")
        row = (
            c.exec_driver_sql(
                "SELECT c.project_id,c.enabled,c.origin,k.token_hash FROM memory_connections "
                "c JOIN memory_credentials k USING(connection_id) WHERE c.connection_id=?",
                (key(connection_id),),
            )
            .mappings()
            .first()
        )
        if (
            row is None
            or row["token_hash"] is None
            or not hmac.compare_digest(row["token_hash"], sha(token))
            or row["origin"] != "external_report"
        ):
            raise StoreError("unauthorized")
        if not row["enabled"]:
            raise StoreError("connection_paused")
        return str(row["project_id"])

    def ingest(
        self,
        connection_id: str,
        event: EventInput,
        *,
        token: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        event = validate(EventInput, event)
        payload = encode(event.model_dump(mode="json"))
        with self._transaction(write=True) as c:
            if token is not None:
                project_id = self._authenticate(c, connection_id, token)
            if project_id is None:
                raise StoreError("unauthorized")
            connection(c, project_id, connection_id)
            old = c.exec_driver_sql(
                "SELECT fingerprint FROM memory_events WHERE connection_id=? AND event_id=?",
                (connection_id, event.event_id),
            ).first()
            if old and old[0] != sha(payload):
                raise StoreError("idempotency_conflict")
            if not old:
                count = c.exec_driver_sql(
                    "SELECT count(*) FROM memory_events WHERE connection_id=?", (connection_id,)
                ).scalar_one()
                if count >= 10000:
                    raise StoreError("event_limit")
                c.exec_driver_sql(
                    "INSERT INTO memory_events VALUES(?,?,?,?,?)",
                    (connection_id, event.event_id, sha(payload), payload, self._now()),
                )
                job_id = enqueue(c, project_id, "event", connection_id, event.event_id, self._now())
                c.exec_driver_sql(
                    "INSERT INTO memory_event_processing VALUES(?,?,?,NULL,'pending')",
                    (connection_id, event.event_id, job_id),
                )
            return self._event_receipt(c, connection_id, event.event_id)

    def _event_receipt(self, c: Connection, connection_id: str, event_id: str) -> dict[str, Any]:
        row = one(
            c,
            "SELECT e.event_id,p.state,p.memory_id,s.project_id FROM memory_events e "
            "JOIN memory_connections s USING(connection_id) LEFT JOIN "
            "memory_event_processing p USING(connection_id,event_id) WHERE e.connection_id=? "
            "AND e.event_id=?",
            connection_id,
            key(event_id),
        )
        return {
            "event_id": row["event_id"],
            "accepted": True,
            "processing": self._processing(c, row),
            "memory_id": row["memory_id"],
        }

    @staticmethod
    def _processing(c: Connection, row: Any) -> str:
        if (
            row["state"] == "pending"
            and c.exec_driver_sql(
                "SELECT count(*) FROM memory_heads WHERE project_id=?", (row["project_id"],)
            ).scalar_one()
            >= 10000
        ):
            return "capacity_limited"
        return str(row["state"] or "unmanaged")

    def event_receipt(self, connection_id: str, event_id: str, token: str) -> dict[str, Any]:
        with self._transaction() as c:
            self._authenticate(c, connection_id, token)
            return self._event_receipt(c, connection_id, event_id)

    def process_pending_events(
        self, project_id: str | None = None, connection_id: str | None = None
    ) -> int:
        """One bounded transaction: no model or network; crash rolls back draft and receipt."""
        with self._transaction(write=True) as c:
            if project_id is not None:
                project(c, project_id)
            if connection_id is not None:
                if project_id is None:
                    raise StoreError("invalid_id")
                connection(c, project_id, connection_id)
            rows = list(
                c.exec_driver_sql(
                    "SELECT p.*,s.project_id,e.payload FROM memory_event_processing p JOIN "
                    "memory_connections s USING(connection_id) JOIN memory_events e "
                    "USING(connection_id,event_id) JOIN memory_jobs j ON j.job_id=p.job_id "
                    "WHERE p.state='pending' AND s.enabled=1 AND j.state='pending' AND (? IS "
                    "NULL OR s.project_id=?) AND (? IS NULL OR s.connection_id=?) "
                    "AND (SELECT count(*) FROM memory_heads h "
                    "WHERE h.project_id=s.project_id) < 10000 "
                    "ORDER BY "
                    "e.received_at,p.event_id LIMIT 20",
                    (project_id, project_id, connection_id, connection_id),
                ).mappings()
            )
            processed = 0
            for row in rows:
                count = c.exec_driver_sql(
                    "SELECT count(*) FROM memory_heads WHERE project_id=?", (row["project_id"],)
                ).scalar_one()
                if count >= 10000:
                    continue
                event = EventInput.model_validate_json(row["payload"])
                memory_id = str(uuid4())
                content = DraftInput(
                    kind="episode",
                    connection_id=row["connection_id"],
                    event_id=row["event_id"],
                    tool_id="unspecified",
                    source_version="unknown",
                    title=f"External event: {event.event_type}",
                    body=(
                        f"External source reported {event.event_type}, outcome={event.outcome}. "
                        "This is an observation, not an internally verified result. "
                        "Tool and source version require user review."
                    ),
                )
                c.exec_driver_sql(
                    "INSERT INTO memory_heads VALUES(?,?,?,1,1,'draft',?)",
                    (memory_id, row["project_id"], "episode", self._now()),
                )
                self._revision(c, memory_id, 1, content)
                c.exec_driver_sql(
                    "UPDATE memory_jobs SET state='done' WHERE job_id=?", (row["job_id"],)
                )
                c.exec_driver_sql(
                    "UPDATE memory_event_processing SET state='done',memory_id=? WHERE "
                    "connection_id=? AND event_id=?",
                    (memory_id, row["connection_id"], row["event_id"]),
                )
                processed += 1
            return processed

    def overview(self, project_id: str, *, pages: dict[str, int] | None = None) -> dict[str, Any]:
        sections = ("connections", "events", "memories", "grants", "policies", "jobs")
        pages = pages or {}
        if set(pages) - set(sections):
            raise StoreError("invalid_page")
        for value in pages.values():
            self._page(value)
        offsets = {name: pages.get(name, 0) * 100 for name in sections}
        with self._transaction() as c:
            project(c, project_id)
            connections = [
                dict(r)
                for r in c.exec_driver_sql(
                    "SELECT c.*,COALESCE(k.version,0) AS credential_version,(k.token_hash IS "
                    "NOT NULL) AS credential_active FROM memory_connections c LEFT JOIN "
                    "memory_credentials k USING(connection_id) WHERE c.project_id=? ORDER BY "
                    "c.connection_id LIMIT 101 OFFSET ?",
                    (project_id, offsets["connections"]),
                ).mappings()
            ]
            for source in connections:
                source["enabled"] = bool(source["enabled"])
                source["credential_active"] = bool(source["credential_active"])
            events = []
            for row in c.exec_driver_sql(
                "SELECT e.*,p.state,p.memory_id,c.project_id FROM memory_events e "
                "JOIN memory_connections "
                "c USING(connection_id) LEFT JOIN memory_event_processing p "
                "USING(connection_id,event_id) WHERE c.project_id=? ORDER BY received_at "
                "DESC,e.event_id,e.connection_id LIMIT 101 OFFSET ?",
                (project_id, offsets["events"]),
            ).mappings():
                event = EventInput.model_validate_json(row["payload"])
                events.append(
                    {
                        "connection_id": row["connection_id"],
                        "event_id": event.event_id,
                        "event_type": event.event_type,
                        "outcome": event.outcome,
                        "received_at": row["received_at"],
                        "occurred_at": event.occurred_at,
                        "processing": self._processing(c, row),
                        "memory_id": row["memory_id"],
                    }
                )
            grants = [
                dict(r)
                for r in c.exec_driver_sql(
                    "SELECT connection_id,content_type,purpose,version,enabled,expires_at "
                    "FROM memory_grants WHERE project_id=? ORDER BY "
                    "connection_id,content_type,purpose LIMIT 101 OFFSET ?",
                    (project_id, offsets["grants"]),
                ).mappings()
            ]
            for grant in grants:
                grant["enabled"] = bool(grant["enabled"])
            policies = [
                dict(
                    memory_id=r["memory_id"],
                    version=r["version"],
                    generation=r["generation"],
                    **json.loads(r["payload"]),
                )
                for r in c.exec_driver_sql(
                    "SELECT p.* FROM memory_policies p JOIN memory_heads h USING(memory_id) "
                    "WHERE h.project_id=? ORDER BY memory_id LIMIT 101 OFFSET ?",
                    (project_id, offsets["policies"]),
                ).mappings()
            ]
            jobs = [
                dict(r)
                for r in c.exec_driver_sql(
                    "SELECT job_id,kind,state,memory_id,created_at FROM memory_jobs WHERE "
                    "project_id=? ORDER BY created_at DESC,job_id LIMIT 101 OFFSET ?",
                    (project_id, offsets["jobs"]),
                ).mappings()
            ]
            memory_ids = [
                r[0]
                for r in c.exec_driver_sql(
                    "SELECT memory_id FROM memory_heads WHERE project_id=? "
                    "ORDER BY updated_at DESC,memory_id LIMIT 101 OFFSET ?",
                    (project_id, offsets["memories"]),
                )
            ]
        memories = [
            self.memory(project_id, memory_id).model_dump(mode="json")
            for memory_id in memory_ids[:100]
        ]
        result = dict(
            connections=connections,
            events=events,
            grants=grants,
            policies=policies,
            jobs=jobs,
            memories=memories,
        )
        pagination = {
            name: self._pagination(
                pages.get(name, 0), len(memory_ids) if name == "memories" else len(items)
            )
            for name, items in result.items()
        }
        return {**{name: items[:100] for name, items in result.items()}, "pagination": pagination}

    def source_grants(self, project_id: str, connection_id: str) -> dict[str, Any]:
        with self._transaction() as c:
            connection(c, project_id, connection_id, active=False)
            grants = [
                dict(r)
                for r in c.exec_driver_sql(
                    "SELECT connection_id,content_type,purpose,version,enabled,expires_at "
                    "FROM memory_grants WHERE project_id=? AND connection_id=? "
                    "ORDER BY content_type,purpose LIMIT 12",
                    (project_id, connection_id),
                ).mappings()
            ]
            for grant in grants:
                grant["enabled"] = bool(grant["enabled"])
            return {"grants": grants}

    def memory_context(self, project_id: str, memory_id: str) -> dict[str, Any]:
        memory = self.memory(project_id, memory_id)
        with self._transaction() as c:
            row = (
                c.exec_driver_sql("SELECT * FROM memory_policies WHERE memory_id=?", (memory_id,))
                .mappings()
                .first()
            )
            policy = (
                dict(
                    memory_id=row["memory_id"],
                    version=row["version"],
                    generation=row["generation"],
                    **json.loads(row["payload"]),
                )
                if row
                else None
            )
        grants = (
            self.source_grants(project_id, memory.content.connection_id)["grants"]
            if memory.content
            else []
        )
        return {"policy": policy, "grants": grants}
