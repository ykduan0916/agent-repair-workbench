"""M1-R project-scoped authority. Trusted host only; never imports Cognee."""

from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import Connection

from agent_workbench.run_store import StoreError

from .common import Row, connection, encode, enqueue, head, key, one, project, sha, validate
from .jobs import MemoryJobs
from .models import DraftInput, EventInput, Lease, MemoryView, Origin, Receipt, State


class MemoryStore(MemoryJobs):
    def register_project(self, project_id: str) -> None:
        key(project_id)
        with self._transaction(write=True) as c:
            c.exec_driver_sql(
                "INSERT OR IGNORE INTO memory_projects VALUES(?,?)", (project_id, self._now())
            )

    def register_connection(self, project_id: str, connection_id: str, origin: Origin) -> None:
        key(connection_id)
        if origin not in ("external_report", "local_user", "synthetic"):
            raise StoreError("invalid_origin")
        with self._transaction(write=True) as c:
            project(c, project_id)
            old = (
                c.exec_driver_sql(
                    "SELECT * FROM memory_connections WHERE connection_id=?", (connection_id,)
                )
                .mappings()
                .first()
            )
            if old:
                if old["project_id"] != project_id or old["origin"] != origin:
                    raise StoreError("connection_conflict")
                return
            c.exec_driver_sql(
                "INSERT INTO memory_connections VALUES(?,?,?,1,1)",
                (connection_id, project_id, origin),
            )

    def set_connection(
        self, project_id: str, connection_id: str, expected_version: int, *, enabled: bool
    ) -> int:
        if type(expected_version) is not int or type(enabled) is not bool:
            raise StoreError("invalid_payload")
        with self._transaction(write=True) as c:
            source = connection(c, project_id, connection_id, active=False)
            if source["version"] != expected_version:
                raise StoreError("version_conflict")
            version = expected_version + 1
            c.exec_driver_sql(
                "UPDATE memory_connections SET enabled=?,version=? WHERE connection_id=?",
                (int(enabled), version, connection_id),
            )
            return version

    def accept_event(self, project_id: str, connection_id: str, event: EventInput) -> str:
        event = validate(EventInput, event)
        payload = encode(event.model_dump(mode="json"))
        if len(payload) > 16384:
            raise StoreError("payload_limit")
        with self._transaction(write=True) as c:
            connection(c, project_id, connection_id)
            old = c.exec_driver_sql(
                "SELECT fingerprint FROM memory_events WHERE connection_id=? AND event_id=?",
                (connection_id, event.event_id),
            ).first()
            if old:
                if old[0] != sha(payload):
                    raise StoreError("idempotency_conflict")
                return event.event_id
            count = c.exec_driver_sql(
                "SELECT count(*) FROM memory_events WHERE connection_id=?", (connection_id,)
            ).scalar_one()
            if count >= 10000:
                raise StoreError("event_limit")
            c.exec_driver_sql(
                "INSERT INTO memory_events VALUES(?,?,?,?,?)",
                (connection_id, event.event_id, sha(payload), payload, self._now()),
            )
            enqueue(c, project_id, "event", connection_id, event.event_id, self._now())
            return event.event_id

    @staticmethod
    def _source(c: Connection, project_id: str, content: DraftInput) -> None:
        connection(c, project_id, content.connection_id)
        one(
            c,
            "SELECT event_id FROM memory_events WHERE connection_id=? AND event_id=?",
            content.connection_id,
            content.event_id,
        )

    @staticmethod
    def _prior(c: Connection, project_id: str, request_id: str, fingerprint: str) -> Receipt | None:
        key(request_id)
        project(c, project_id)
        row = (
            c.exec_driver_sql(
                "SELECT * FROM memory_receipts WHERE project_id=? AND request_id=?",
                (project_id, request_id),
            )
            .mappings()
            .first()
        )
        if row:
            if row["fingerprint"] != fingerprint:
                raise StoreError("idempotency_conflict")
            try:
                return Receipt.model_validate_json(row["payload"])
            except ValidationError:
                raise StoreError("store_corrupt") from None
        return None

    @staticmethod
    def _receipt(item: Row) -> Receipt:
        return Receipt(
            resource_id=item["memory_id"],
            version=item["version"],
            revision=item["revision"],
            state=item["state"],
        )

    @staticmethod
    def _save_receipt(
        c: Connection, project_id: str, request_id: str, fingerprint: str, receipt: Receipt
    ) -> None:
        c.exec_driver_sql(
            "INSERT INTO memory_receipts VALUES(?,?,?,?)",
            (project_id, request_id, fingerprint, encode(receipt.model_dump(mode="json"))),
        )

    @staticmethod
    def _revision(c: Connection, memory_id: str, revision: int, content: DraftInput) -> None:
        payload = encode(content.model_dump(mode="json"))
        c.exec_driver_sql(
            "INSERT INTO memory_revisions VALUES(?,?,?,?,?,?)",
            (memory_id, revision, content.connection_id, content.event_id, sha(payload), payload),
        )

    def create_memory(self, project_id: str, request_id: str, content: DraftInput) -> Receipt:
        content = validate(DraftInput, content)
        fingerprint = sha(encode(["create", content.model_dump(mode="json")]))
        with self._transaction(write=True) as c:
            prior = self._prior(c, project_id, request_id, fingerprint)
            if prior:
                return prior
            self._source(c, project_id, content)
            if (
                c.exec_driver_sql(
                    "SELECT count(*) FROM memory_heads WHERE project_id=?", (project_id,)
                ).scalar_one()
                >= 10000
            ):
                raise StoreError("memory_limit")
            memory_id = str(uuid4())
            c.exec_driver_sql(
                "INSERT INTO memory_heads VALUES(?,?,?,1,1,'draft',?)",
                (memory_id, project_id, content.kind, self._now()),
            )
            self._revision(c, memory_id, 1, content)
            enqueue(
                c,
                project_id,
                "project",
                content.connection_id,
                content.event_id,
                self._now(),
                memory_id,
                1,
            )
            receipt = Receipt(resource_id=memory_id, version=1, revision=1, state="draft")
            self._save_receipt(c, project_id, request_id, fingerprint, receipt)
            return receipt

    @staticmethod
    def _invalidate_jobs(c: Connection, memory_id: str) -> None:
        c.exec_driver_sql(
            "UPDATE memory_calls SET state='unknown' WHERE state='reserved' AND "
            "job_id IN (SELECT job_id FROM memory_jobs WHERE memory_id=? AND state IN"
            " ('pending','leased'))",
            (memory_id,),
        )
        c.exec_driver_sql(
            "UPDATE memory_jobs SET state=CASE WHEN EXISTS (SELECT 1 FROM "
            "memory_calls WHERE memory_calls.job_id=memory_jobs.job_id AND "
            "state='unknown') THEN 'unknown' ELSE 'cancelled' END WHERE memory_id=? "
            "AND state IN ('pending','leased')",
            (memory_id,),
        )

    def change_memory(
        self,
        project_id: str,
        memory_id: str,
        request_id: str,
        expected_version: int,
        *,
        state: State | None = None,
        content: DraftInput | None = None,
    ) -> Receipt:
        key(memory_id)
        if type(expected_version) is not int or not 1 <= expected_version < 1000000:
            raise StoreError("invalid_version")
        if (state is None) == (content is None) or state not in (
            None,
            "confirmed",
            "revoked",
            "deleted",
        ):
            raise StoreError("invalid_transition")
        if content is not None:
            content = validate(DraftInput, content)
        fingerprint = sha(
            encode(
                [
                    "change",
                    memory_id,
                    expected_version,
                    state,
                    content.model_dump(mode="json") if content else None,
                ]
            )
        )
        with self._transaction(write=True) as c:
            prior = self._prior(c, project_id, request_id, fingerprint)
            if prior:
                return prior
            item = head(c, project_id, memory_id)
            if item["version"] != expected_version:
                raise StoreError("version_conflict")
            if item["state"] == "deleted":
                raise StoreError("memory_deleted")
            rev = one(
                c,
                "SELECT * FROM memory_revisions WHERE memory_id=? AND revision=?",
                memory_id,
                item["revision"],
            )
            revision, version = item["revision"], expected_version + 1
            target: State
            if content:
                if content.kind != item["kind"] or revision >= 64:
                    raise StoreError("revision_limit_or_kind")
                self._source(c, project_id, content)
                revision += 1
                self._revision(c, memory_id, revision, content)
                connection_id, event_id = content.connection_id, content.event_id
                target = "draft"
            else:
                connection_id, event_id = rev["connection_id"], rev["event_id"]
                assert state is not None
                target = state
                if state == "confirmed":
                    connection(c, project_id, connection_id)
            self._invalidate_jobs(c, memory_id)
            c.exec_driver_sql(
                "UPDATE memory_heads SET state=?,version=?,revision=?,updated_at=? WHERE "
                "memory_id=?",
                (target, version, revision, self._now(), memory_id),
            )
            if target == "deleted":
                c.exec_driver_sql(
                    "UPDATE memory_revisions SET payload=NULL WHERE memory_id=?", (memory_id,)
                )
            enqueue(
                c,
                project_id,
                "cleanup" if target in ("revoked", "deleted") else "project",
                connection_id,
                event_id,
                self._now(),
                memory_id,
                version,
            )
            receipt = self._receipt(head(c, project_id, memory_id))
            self._save_receipt(c, project_id, request_id, fingerprint, receipt)
            return receipt

    def memory(self, project_id: str, memory_id: str) -> MemoryView:
        with self._transaction() as c:
            item = head(c, project_id, memory_id)
            rev = one(
                c,
                "SELECT * FROM memory_revisions WHERE memory_id=? AND revision=?",
                memory_id,
                item["revision"],
            )
            content = None
            if rev["payload"] is not None:
                if sha(rev["payload"]) != rev["digest"]:
                    raise StoreError("store_corrupt")
                try:
                    content = DraftInput.model_validate_json(rev["payload"])
                except ValidationError:
                    raise StoreError("store_corrupt") from None
            elif item["state"] != "deleted":
                raise StoreError("store_corrupt")
            return MemoryView(
                receipt=self._receipt(item),
                kind=item["kind"],
                digest=rev["digest"],
                content=content,
            )

    def list_memories(self, project_id: str, *, limit: int = 100) -> list[Receipt]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise StoreError("invalid_limit")
        with self._transaction() as c:
            project(c, project_id)
            return [
                self._receipt(row)
                for row in c.exec_driver_sql(
                    "SELECT * FROM memory_heads WHERE project_id=? ORDER BY updated_at "
                    "DESC,memory_id LIMIT ?",
                    (project_id, limit),
                ).mappings()
            ]

    def register_projection(
        self, project_id: str, lease: Lease, dataset_id: str, data_id: str, expected_digest: str
    ) -> None:
        key(dataset_id)
        key(data_id)
        with self._transaction(write=True) as c:
            job = self._owner(c, project_id, lease)
            if job["kind"] != "project" or job["memory_id"] is None:
                raise StoreError("invalid_projection_job")
            item = head(c, project_id, job["memory_id"])
            rev = one(
                c,
                "SELECT * FROM memory_revisions WHERE memory_id=? AND revision=?",
                item["memory_id"],
                item["revision"],
            )
            if (
                expected_digest != rev["digest"]
                or rev["payload"] is None
                or sha(rev["payload"]) != expected_digest
            ):
                raise StoreError("projection_mismatch")
            values = (
                project_id,
                item["memory_id"],
                item["revision"],
                item["version"],
                expected_digest,
                dataset_id,
                data_id,
            )
            prior = c.exec_driver_sql(
                "SELECT * FROM memory_projections WHERE dataset_id=? AND data_id=?",
                (dataset_id, data_id),
            ).first()
            if prior:
                if tuple(prior) != values:
                    raise StoreError("projection_conflict")
                return
            c.exec_driver_sql("INSERT INTO memory_projections VALUES(?,?,?,?,?,?,?)", values)

    def resolve_projection(self, project_id: str, dataset_id: str, data_id: str) -> Receipt | None:
        key(dataset_id)
        key(data_id)
        with self._transaction() as c:
            project(c, project_id)
            row = (
                c.exec_driver_sql(
                    "SELECT p.*,h.state,h.version,h.revision AS "
                    "current_revision,r.connection_id,r.payload FROM memory_projections p "
                    "JOIN memory_heads h ON h.memory_id=p.memory_id JOIN memory_revisions r "
                    "ON r.memory_id=p.memory_id AND r.revision=p.revision WHERE "
                    "p.project_id=? AND p.dataset_id=? AND p.data_id=?",
                    (project_id, dataset_id, data_id),
                )
                .mappings()
                .first()
            )
            if (
                row is None
                or row["state"] != "confirmed"
                or row["version"] != row["generation"]
                or row["current_revision"] != row["revision"]
                or row["payload"] is None
                or sha(row["payload"]) != row["digest"]
            ):
                return None
            if not connection(c, project_id, row["connection_id"], active=False)["enabled"]:
                return None
            return Receipt(
                resource_id=row["memory_id"],
                version=row["version"],
                revision=row["revision"],
                state="confirmed",
            )
