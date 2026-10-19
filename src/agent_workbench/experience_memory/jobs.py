"""M1-R outbox leases and request reservations; no execution or network side effects."""

import secrets
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import Connection

from agent_workbench.run_store import RunStore, StoreError

from .common import Row, connection, head, key, one, project, sha, validate
from .models import CallReceipt, EventInput, Lease


class MemoryJobs(RunStore):
    @staticmethod
    def _current(c: Connection, job: Row) -> bool:
        if job["kind"] == "cleanup":
            item = head(c, job["project_id"], job["memory_id"])
            return bool(
                item["version"] == job["generation"] and item["state"] in ("revoked", "deleted")
            )
        source = connection(c, job["project_id"], job["connection_id"], active=False)
        if not source["enabled"]:
            return False
        if job["memory_id"] is None:
            return True
        item = head(c, job["project_id"], job["memory_id"])
        return bool(item["version"] == job["generation"] and item["state"] == "confirmed")

    def claim_job(self, project_id: str) -> Lease | None:
        with self._transaction(write=True) as c:
            project(c, project_id)
            now = self._now()
            # A dead owner may have sent a request. Never reclaim that work blindly.
            c.exec_driver_sql(
                "UPDATE memory_calls SET state='unknown' WHERE state='reserved' AND job_id IN "
                "(SELECT job_id FROM memory_jobs WHERE project_id=? AND state='leased' "
                "AND lease_until<=?)",
                (project_id, now),
            )
            c.exec_driver_sql(
                "UPDATE memory_jobs SET state='unknown' WHERE project_id=? "
                "AND state='leased' AND lease_until<=? AND EXISTS "
                "(SELECT 1 FROM memory_calls WHERE memory_calls.job_id=memory_jobs.job_id"
                " AND state='unknown')",
                (project_id, now),
            )
            rows = (
                c.exec_driver_sql(
                    "SELECT * FROM memory_jobs WHERE project_id=? AND (state='pending' OR "
                    "(state='leased' AND lease_until<=?)) ORDER BY created_at,job_id LIMIT 1000",
                    (project_id, now),
                )
                .mappings()
                .all()
            )
            for job in rows:
                if job["attempt"] >= 3:
                    c.exec_driver_sql(
                        "UPDATE memory_jobs SET state='failed',lease_hash=NULL WHERE job_id=?",
                        (job["job_id"],),
                    )
                    continue
                if not self._current(c, job):
                    # Paused sources stay queued; obsolete memory generations retire.
                    source = connection(c, project_id, job["connection_id"], active=False)
                    if source["enabled"] or job["kind"] == "cleanup":
                        c.exec_driver_sql(
                            "UPDATE memory_jobs SET state='cancelled',lease_hash=NULL "
                            "WHERE job_id=?",
                            (job["job_id"],),
                        )
                    continue
                token = secrets.token_hex(32)
                c.exec_driver_sql(
                    "UPDATE memory_jobs SET "
                    "state='leased',attempt=attempt+1,lease_hash=?,lease_until=? WHERE "
                    "job_id=?",
                    (sha(token), now + 30, job["job_id"]),
                )
                return Lease(
                    job_id=job["job_id"],
                    token=token,
                    attempt=job["attempt"] + 1,
                    kind=job["kind"],
                    memory_id=job["memory_id"],
                    generation=job["generation"],
                    expires_at=now + 30,
                )
            return None

    def _owner(self, c: Connection, project_id: str, lease: Lease, *, current: bool = True) -> Row:
        lease = validate(Lease, lease)
        job = one(
            c,
            "SELECT * FROM memory_jobs WHERE project_id=? AND job_id=?",
            key(project_id),
            lease.job_id,
        )
        if (
            job["state"] != "leased"
            or job["lease_hash"] != sha(lease.token)
            or job["lease_until"] <= self._now()
        ):
            raise StoreError("lease_conflict")
        if current and not self._current(c, job):
            raise StoreError("job_obsolete")
        return job

    def event_for_job(self, project_id: str, lease: Lease) -> EventInput:
        """Read the authorized immutable event for a currently owned job."""
        with self._transaction() as c:
            job = self._owner(c, project_id, lease)
            row = one(
                c,
                "SELECT payload,fingerprint FROM memory_events "
                "WHERE connection_id=? AND event_id=?",
                job["connection_id"],
                job["event_id"],
            )
            if sha(row["payload"]) != row["fingerprint"]:
                raise StoreError("store_corrupt")
            try:
                return EventInput.model_validate_json(row["payload"])
            except ValidationError:
                raise StoreError("store_corrupt") from None

    def renew_job(self, project_id: str, lease: Lease) -> None:
        with self._transaction(write=True) as c:
            self._owner(c, project_id, lease)
            c.exec_driver_sql(
                "UPDATE memory_jobs SET lease_until=? WHERE job_id=?",
                (self._now() + 30, lease.job_id),
            )

    def finish_job(self, project_id: str, lease: Lease, *, succeeded: bool) -> None:
        if type(succeeded) is not bool:
            raise StoreError("invalid_payload")
        with self._transaction(write=True) as c:
            self._owner(c, project_id, lease)
            if c.exec_driver_sql(
                "SELECT 1 FROM memory_calls WHERE job_id=? AND state!='received' LIMIT 1",
                (lease.job_id,),
            ).first():
                raise StoreError("usage_unsettled")
            c.exec_driver_sql(
                "UPDATE memory_jobs SET state=? WHERE job_id=?",
                ("done" if succeeded else "failed", lease.job_id),
            )

    def cancel_job(self, project_id: str, job_id: str) -> None:
        with self._transaction(write=True) as c:
            job = one(
                c,
                "SELECT * FROM memory_jobs WHERE project_id=? AND job_id=?",
                key(project_id),
                key(job_id),
            )
            if job["state"] not in ("pending", "leased"):
                return
            c.exec_driver_sql(
                "UPDATE memory_calls SET state='unknown' WHERE job_id=? AND state='reserved'",
                (job_id,),
            )
            unknown = c.exec_driver_sql(
                "SELECT 1 FROM memory_calls WHERE job_id=? AND state='unknown' LIMIT 1", (job_id,)
            ).first()
            c.exec_driver_sql(
                "UPDATE memory_jobs SET state=? WHERE job_id=?",
                ("unknown" if unknown else "cancelled", job_id),
            )

    def reserve_call(
        self,
        project_id: str,
        lease: Lease,
        call_id: str,
        kind: Literal["llm", "embedding"],
        tokens: int,
    ) -> CallReceipt:
        key(call_id)
        if kind not in ("llm", "embedding") or type(tokens) is not int or not 1 <= tokens <= 65536:
            raise StoreError("invalid_budget")
        with self._transaction(write=True) as c:
            job = self._owner(c, project_id, lease)
            if job["kind"] == "cleanup":
                raise StoreError("cleanup_model_forbidden")
            connection(c, project_id, job["connection_id"])
            prior = (
                c.exec_driver_sql(
                    "SELECT * FROM memory_calls WHERE job_id=? AND call_id=?",
                    (lease.job_id, call_id),
                )
                .mappings()
                .first()
            )
            if prior:
                if prior["kind"] != kind or prior["reserved_tokens"] != tokens:
                    raise StoreError("idempotency_conflict")
                return self._call(prior)
            if c.exec_driver_sql(
                "SELECT 1 FROM memory_calls WHERE job_id=? AND state!='received' LIMIT 1",
                (lease.job_id,),
            ).first():
                raise StoreError("usage_unsettled")
            count = c.exec_driver_sql(
                "SELECT count(*) FROM memory_calls WHERE job_id=? AND kind=?", (lease.job_id, kind)
            ).scalar_one()
            if count >= (6 if kind == "llm" else 40) or job["tokens"] + tokens > 100000:
                raise StoreError("budget_exceeded")
            c.exec_driver_sql(
                "INSERT INTO memory_calls(job_id,call_id,kind,reserved_tokens,state) "
                "VALUES(?,?,?,?,'reserved')",
                (lease.job_id, call_id, kind, tokens),
            )
            c.exec_driver_sql(
                "UPDATE memory_jobs SET tokens=tokens+? WHERE job_id=?", (tokens, lease.job_id)
            )
            return CallReceipt(
                admitted=True,
                call_id=call_id,
                state="reserved",
                reserved_tokens=tokens,
                actual_tokens=None,
            )

    @staticmethod
    def _call(row: Row) -> CallReceipt:
        return CallReceipt(
            call_id=row["call_id"],
            state=row["state"],
            reserved_tokens=row["reserved_tokens"],
            actual_tokens=row["actual_tokens"],
        )

    def settle_call(
        self, project_id: str, lease: Lease, call_id: str, actual_tokens: int | None
    ) -> CallReceipt:
        key(call_id)
        if actual_tokens is not None and (
            type(actual_tokens) is not int or actual_tokens < 0 or actual_tokens > 10**9
        ):
            raise StoreError("invalid_usage")
        with self._transaction(write=True) as c:
            # Pausing a source does not discard an already received bill.
            lease = validate(Lease, lease)
            job = one(
                c,
                "SELECT * FROM memory_jobs WHERE project_id=? AND job_id=?",
                key(project_id),
                lease.job_id,
            )
            if job["lease_hash"] != sha(lease.token):
                raise StoreError("lease_conflict")
            row = one(
                c, "SELECT * FROM memory_calls WHERE job_id=? AND call_id=?", lease.job_id, call_id
            )
            if row["state"] == "received" or row["actual_tokens"] is not None:
                if row["actual_tokens"] != actual_tokens:
                    raise StoreError("idempotency_conflict")
                return self._call(row)
            state: Literal["received", "unknown"] = (
                "unknown"
                if (
                    actual_tokens is None
                    or actual_tokens > row["reserved_tokens"]
                    or row["state"] == "unknown"
                    or job["state"] != "leased"
                    or job["lease_until"] <= self._now()
                )
                else "received"
            )
            if state == "unknown":
                c.exec_driver_sql(
                    "UPDATE memory_jobs SET state='unknown' WHERE job_id=?", (lease.job_id,)
                )
            c.exec_driver_sql(
                "UPDATE memory_calls SET state=?,actual_tokens=? WHERE job_id=? AND call_id=?",
                (state, actual_tokens, lease.job_id, call_id),
            )
            return CallReceipt(
                call_id=call_id,
                state=state,
                reserved_tokens=row["reserved_tokens"],
                actual_tokens=actual_tokens,
            )

    def job_status(self, project_id: str, job_id: str) -> str:
        with self._transaction() as c:
            return str(
                one(
                    c,
                    "SELECT state FROM memory_jobs WHERE project_id=? AND job_id=?",
                    key(project_id),
                    key(job_id),
                )["state"]
            )
