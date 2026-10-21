"""M3-R retrieval ownership and transaction-scoped outgoing-context authority."""

import json
from typing import Any

from sqlalchemy import Connection

from agent_workbench.run_store import StoreError

from .agent_models import ContextSelection, MemoryBinding, TaskKind
from .common import connection, encode, key, one, sha
from .engine_models import Query, Retrieval
from .engine_store import EngineStore
from .models import DraftInput, EventInput


class AgentMemoryStore(EngineStore):
    def begin_task(
        self, kind: TaskKind, task_id: str, binding: MemoryBinding, query: Query
    ) -> None:
        key(task_id)
        if kind not in ("analysis", "repair"):
            raise StoreError("invalid_task_kind")
        if query.connection_id != binding.connection_id:
            raise StoreError("memory_binding_invalid")
        fingerprint = sha(encode([binding.model_dump(), query.model_dump()]))
        with self._transaction(write=True) as c:
            connection(c, binding.project_id, binding.connection_id)
            old = c.exec_driver_sql(
                "SELECT binding_digest FROM memory_task_bindings WHERE task_kind=? AND task_id=?",
                (kind, task_id),
            ).first()
            if old:
                raise StoreError("memory_retrieval_not_replayable")
            event = EventInput(
                event_id=query.event_id,
                event_type="user_correction",
                source_run_id=task_id,
                occurred_at=self._now(),
                outcome="unknown",
            )
            raw = encode(event.model_dump(mode="json"))
            # Host query lineage only: do not enqueue the external-event draft processor.
            c.exec_driver_sql(
                "INSERT INTO memory_events VALUES(?,?,?,?,?)",
                (binding.connection_id, query.event_id, sha(raw), raw, self._now()),
            )
            c.exec_driver_sql(
                "INSERT INTO memory_task_bindings VALUES(?,?,?,?,?,?,?,?,?,?,NULL,'running',"
                "'retrieving',?,?)",
                (
                    kind,
                    task_id,
                    binding.project_id,
                    binding.connection_id,
                    fingerprint,
                    query.tool_id,
                    query.source_version,
                    int(binding.allow_historical),
                    query.event_id,
                    sha(query.text),
                    encode([]),
                    self._now(),
                ),
            )

    def attach_job(self, kind: TaskKind, task_id: str, job_id: str) -> None:
        with self._transaction(write=True) as c:
            row = self._binding(c, kind, task_id)
            operation = one(
                c,
                "SELECT j.project_id,o.operation,o.snapshot FROM memory_jobs j "
                "JOIN memory_operations o ON o.job_id=j.job_id WHERE j.job_id=?",
                job_id,
            )
            snapshot = json.loads(operation["snapshot"])
            if (
                operation["project_id"] != row["project_id"]
                or operation["operation"] != "search"
                or snapshot["query"]["event_id"] != row["event_id"]
                or snapshot["query_digest"] != row["query_digest"]
            ):
                raise StoreError("memory_job_mismatch")
            updated = c.exec_driver_sql(
                "UPDATE memory_task_bindings SET job_id=? WHERE task_kind=? AND task_id=? "
                "AND state='running' AND job_id IS NULL",
                (job_id, kind, task_id),
            )
            if updated.rowcount != 1:
                raise StoreError("memory_retrieval_not_replayable")

    @staticmethod
    def _binding(c: Connection, kind: TaskKind, task_id: str) -> Any:
        return one(
            c, "SELECT * FROM memory_task_bindings WHERE task_kind=? AND task_id=?", kind, task_id
        )

    def finish_task(self, kind: TaskKind, task_id: str, result: Retrieval) -> bool:
        with self._transaction(write=True) as c:
            row = self._binding(c, kind, task_id)
            if row["state"] != "running" or row["job_id"] != result.job_id:
                raise StoreError("memory_job_mismatch")
            unknown = False
            if row["job_id"]:
                unknown = bool(
                    c.exec_driver_sql(
                        "SELECT 1 FROM memory_calls WHERE job_id=? AND "
                        "(state!='received' OR actual_tokens IS NULL) AND "
                        "recovered_by IS NULL LIMIT 1",
                        (row["job_id"],),
                    ).first()
                )
            refs = [r.model_dump(exclude={"body"}) for r in result.references]
            if len(refs) > 8:
                raise StoreError("context_limit")
            c.exec_driver_sql(
                "UPDATE memory_task_bindings SET state=?,reason=?,refs=? "
                "WHERE task_kind=? AND task_id=?",
                ("unknown" if unknown else "done", result.reason, encode(refs), kind, task_id),
            )
            return not unknown

    def mark_unknown(self, kind: TaskKind, task_id: str) -> None:
        with self._transaction(write=True) as c:
            c.exec_driver_sql(
                "UPDATE memory_task_bindings SET state='unknown',reason='retrieval_interrupted' "
                "WHERE task_kind=? AND task_id=? AND state='running'",
                (kind, task_id),
            )

    def _selected(
        self, c: Connection, row: Any, ref: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not any(
            r["memory_id"] == ref["memory_id"] and r["digest"] == ref["digest"]
            for r in json.loads(row["refs"])
        ):
            raise StoreError("memory_context_changed")
        current = self._ref(c, row["project_id"], ref["memory_id"], "agent_context")
        for field in (
            "memory_id",
            "revision",
            "generation",
            "digest",
            "connection_id",
            "event_id",
            "kind",
            "source_version",
        ):
            if current[field] != ref[field]:
                raise StoreError("memory_context_changed")
        if current["tool_id"] != row["tool_id"]:
            raise StoreError("memory_context_changed")
        exact = current["source_version"] == row["source_version"]
        if not exact and not (row["allow_historical"] and current["policy"]["allow_historical"]):
            raise StoreError("memory_context_changed")
        current["classification"] = "exact" if exact else "historical_hint"
        if current["classification"] != ref["classification"]:
            raise StoreError("memory_context_changed")
        revision = one(
            c,
            "SELECT payload FROM memory_revisions WHERE memory_id=? AND revision=?",
            current["memory_id"],
            current["revision"],
        )
        body = DraftInput.model_validate_json(revision["payload"])
        return current, {**ref, "body": body.body, "current_verification": False}

    def _retrieval_current(self, c: Connection, row: Any) -> None:
        if row["job_id"] is None:
            return
        operation = one(c, "SELECT snapshot FROM memory_operations WHERE job_id=?", row["job_id"])
        snapshot = json.loads(operation["snapshot"])
        source = connection(c, row["project_id"], row["connection_id"])
        grant = self._grant(c, row["project_id"], row["connection_id"], "query", "retrieve")
        if (
            source["version"] != snapshot["connection_version"]
            or grant != snapshot["query_grant"]
            or self._scope(c, row["project_id"], snapshot["query"]) != snapshot["refs"]
        ):
            raise StoreError("memory_context_changed")

    def prepare_context(self, kind: TaskKind, task_id: str, role: str) -> ContextSelection:
        with self._transaction() as c:
            row = self._binding(c, kind, task_id)
            if row["state"] != "done":
                raise StoreError("memory_retrieval_incomplete")
            if role == "verification":
                return ContextSelection(kind, task_id, (), ())
            if role not in ("diagnosis", "repair"):
                raise StoreError("memory_role_denied")
            connection(c, row["project_id"], row["connection_id"])
            self._retrieval_current(c, row)
            selected = [self._selected(c, row, ref) for ref in json.loads(row["refs"])]
            if sum(len(ref["body"].encode()) for _, ref in selected) > 8192:
                raise StoreError("context_limit")
            return ContextSelection(
                kind, task_id, tuple(s for s, _ in selected), tuple(r for _, r in selected)
            )

    def admit_context(
        self,
        c: Connection,
        selection: ContextSelection,
        call_id: str,
        role: str,
        request_sha256: str,
    ) -> None:
        """Called ONLY inside the model ledger's same SQLite write transaction."""
        row = self._binding(c, selection.task_kind, selection.task_id)
        if row["state"] != "done" or role not in ("diagnosis", "repair", "verification"):
            raise StoreError("memory_context_changed")
        if role == "verification" and (selection.references or selection.snapshots):
            raise StoreError("memory_role_denied")
        if len(selection.snapshots) != len(selection.references):
            raise StoreError("memory_context_changed")
        if role != "verification":
            connection(c, row["project_id"], row["connection_id"])
        if selection.references:
            self._retrieval_current(c, row)
        for snapshot, ref in zip(selection.snapshots, selection.references, strict=True):
            current, body = self._selected(c, row, ref)
            if current != snapshot or body != ref:
                raise StoreError("memory_context_changed")
        c.exec_driver_sql(
            "INSERT INTO memory_model_uses VALUES(?,?,?,?,?,?,?)",
            (
                selection.task_kind,
                selection.task_id,
                key(call_id),
                role,
                request_sha256,
                encode(selection.snapshots),
                self._now(),
            ),
        )

    def task_usage(self, kind: TaskKind, task_id: str) -> dict[str, Any]:
        with self._transaction() as c:
            row = self._binding(c, kind, task_id)
            uses = (
                c.exec_driver_sql(
                    "SELECT call_id,role,request_sha256,refs,admitted_at FROM "
                    "memory_model_uses WHERE task_kind=? AND task_id=? "
                    "ORDER BY admitted_at,call_id",
                    (kind, task_id),
                )
                .mappings()
                .all()
            )
            report = {
                "state": row["state"],
                "reason": row["reason"],
                "job_id": row["job_id"],
                "project_id": row["project_id"],
                "connection_id": row["connection_id"],
                "tool_id": row["tool_id"],
                "source_version": row["source_version"],
                "references": json.loads(row["refs"]),
                "uses": [{**dict(u), "refs": json.loads(u["refs"])} for u in uses],
                "token_limit": 100000,
                "monetary_cost": "unknown",
            }
        if row["job_id"]:
            report["accounting"] = self.accounting(row["project_id"], row["job_id"])
        return report
