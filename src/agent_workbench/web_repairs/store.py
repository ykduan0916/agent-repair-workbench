"""Atomic receipts, single-use claims, and evidence-bound release transactions."""

import secrets

from sqlalchemy import Connection, RowMapping, select, update

from agent_workbench.analysis_store.store import _hash
from agent_workbench.candidate_execution.models import OracleReport
from agent_workbench.experience_memory.agent_models import MemoryBinding, agent_budget
from agent_workbench.mcp_config import canonical, digest
from agent_workbench.orchestration.contracts import Usage
from agent_workbench.repair_cases.profiles import get_profile
from agent_workbench.repair_recovery.inventory import RecoveryInventory
from agent_workbench.repair_store.comparisons import _records as comparison_records
from agent_workbench.repair_store.fencing import RepairFence
from agent_workbench.repair_store.models import Mutation, RepairEvent, RepairTask
from agent_workbench.repair_store.reproductions import _records as reproduction_records
from agent_workbench.repair_store.store import _bytes, _load, _new, _validated
from agent_workbench.run_store.models import StoreError
from agent_workbench.run_store.schema import repair_events, repair_tasks
from agent_workbench.run_store.schema import web_repair_requests as receipts
from agent_workbench.run_store.store import _uuid

from .fencing import RepairWebFence
from .models import (
    WEB_BUDGET,
    RepairAdmission,
    RepairAdmissionClaim,
    RepairMode,
    RepairReceipt,
    RepairRequest,
    RepairStart,
)


class WebRepairStore(RecoveryInventory):
    def public_events(self, task_id: str) -> dict[str, object]:
        """B3 projection: audit in the same transaction, never serialize private requests."""
        with self._base._transaction() as connection:
            receipt = self._receipt(connection, task_id)
            saved = self._saved(connection, receipt)
            rows = (
                connection.execute(
                    select(repair_events)
                    .where(repair_events.c.task_id == task_id)
                    .order_by(repair_events.c.revision)
                    .limit(256)
                )
                .mappings()
                .all()
            )
            events = []
            for row in rows:
                event = _load(RepairEvent, row)
                request = event.request
                events.append(
                    {
                        "revision": event.revision,
                        "at": event.at.isoformat(),
                        "kind": event.kind,
                        "role": request.role if request else None,
                        "operation": request.operation if request else None,
                        "tool": request.tool if request else None,
                        "status": request.status if request else None,
                        "error_code": request.error_code if request else None,
                    }
                )
            return {
                "schema_version": 1,
                "task_id": task_id,
                "revision": saved.task.revision,
                "events": events,
            }

    def active_id(self) -> str | None:
        with self._base._transaction() as connection:
            task_id = connection.execute(
                select(receipts.c.request_id).where(receipts.c.active == 1)
            ).scalar_one_or_none()
            if task_id is not None:
                self._saved(connection, self._receipt(connection, task_id))
            return task_id

    def _receipt(self, connection: Connection, task_id: str) -> RowMapping:
        row = (
            connection.execute(select(receipts).where(receipts.c.request_id == task_id))
            .mappings()
            .first()
        )
        if row is None:
            raise StoreError("web_repair_not_found")
        try:
            request = RepairRequest(
                request_id=row["request_id"],
                case_id=row["case_id"],
                mode=row["mode"],
                memory=MemoryBinding.model_validate_json(row["memory_binding"])
                if row["memory_binding"]
                else None,
            )
            if request.fingerprint() != row["payload_sha256"]:
                raise ValueError
            for key in ("policy_sha256", "initial_sha256", "claim_sha256"):
                _hash(row[key])
            if any(
                type(row[k]) is not int or row[k] not in (0, 1)
                for k in ("active", "consumed", "cancel_requested")
            ):
                raise ValueError
        except (ValueError, TypeError, StoreError):
            raise StoreError("store_corrupt") from None
        return row

    def _saved(self, connection: Connection, row: RowMapping) -> Mutation:
        try:
            task, _, _ = self._read(connection, row["request_id"])
        except StoreError as error:
            if error.code == "repair_not_found":
                raise StoreError("request_gone") from None
            raise
        created_row = (
            connection.execute(
                select(repair_events).where(
                    repair_events.c.task_id == task.task_id, repair_events.c.revision == 0
                )
            )
            .mappings()
            .one()
        )
        created = _load(RepairEvent, created_row)
        # Rebuild the immutable initial snapshot from audited task fields; this
        # binds the receipt to the original created event even after progress.
        initial = _new(
            task,
            revision=0,
            phase="ready",
            outcome=None,
            error_code=None,
            finished_at=None,
            usage=Usage(),
            candidate_count=0,
        )
        if (
            task.mode != row["mode"]
            or task.source_analysis_id is not None
            or task.budget
            != agent_budget(
                MemoryBinding.model_validate_json(row["memory_binding"])
                if row["memory_binding"]
                else None,
                WEB_BUDGET,
            )
            or task.model_config_sha256 is None
            or created.task_sha256 != row["initial_sha256"]
            or digest(_bytes(initial)) != row["initial_sha256"]
            or (not row["consumed"] and task != initial)
            or (not row["active"] and task.phase != "finished")
        ):
            raise StoreError("store_corrupt")
        return Mutation(task, digest(_bytes(task)))

    def _existing(self, connection: Connection, request: RepairRequest) -> RepairAdmission | None:
        if (
            connection.execute(
                select(receipts.c.request_id).where(receipts.c.request_id == request.request_id)
            ).first()
            is None
        ):
            return None
        row = self._receipt(connection, request.request_id)
        if request.fingerprint() != row["payload_sha256"]:
            raise StoreError("request_conflict")
        return RepairAdmission(self._saved(connection, row), bool(row["cancel_requested"]))

    def existing(self, request: RepairRequest) -> RepairAdmission | None:
        request = _validated(RepairRequest, request)
        with self._base._transaction() as connection:
            return self._existing(connection, request)

    def receipt(self, task_id: str) -> RepairReceipt:
        with self._base._transaction() as connection:
            row = self._receipt(connection, _uuid(task_id))
            return RepairReceipt(
                RepairRequest(
                    request_id=task_id,
                    case_id=row["case_id"],
                    mode=row["mode"],
                    memory=MemoryBinding.model_validate_json(row["memory_binding"])
                    if row["memory_binding"]
                    else None,
                ),
                self._saved(connection, row),
                bool(row["cancel_requested"]),
                bool(row["active"]),
                bool(row["consumed"]),
            )

    def admit(
        self,
        request: RepairRequest,
        initial: RepairTask,
        *,
        policy_sha256: str,
        owner: RepairWebFence,
        task_fence: RepairFence,
    ) -> RepairAdmission:
        request = _validated(RepairRequest, request)
        nonce = owner.require(self.directory)
        task_fence.require(self.directory, request.request_id)
        with (
            owner.guard(self.directory, nonce),
            task_fence.guard(self.directory, request.request_id),
        ):
            with self._base._transaction(write=True) as connection:
                existing = self._existing(connection, request)
                if existing is not None:
                    return existing
                _hash(policy_sha256)
                initial = _validated(RepairTask, initial)
                profile = get_profile(request.case_id)
                if (
                    initial.task_id != request.request_id
                    or initial.mode != request.mode
                    or initial.source_analysis_id is not None
                    or initial.budget != agent_budget(request.memory, WEB_BUDGET)
                    or initial.model_config_sha256 is None
                    or initial.baseline.manifest_sha256 != profile.manifest_sha256
                    or initial.baseline.file_sha256 != profile.source_sha256
                ):
                    raise StoreError("invalid_repair_input")
                if connection.execute(
                    select(repair_tasks.c.task_id).where(
                        repair_tasks.c.task_id == request.request_id
                    )
                ).first():
                    raise StoreError("request_conflict")
                if connection.execute(
                    select(receipts.c.request_id).where(receipts.c.active == 1)
                ).first():
                    raise StoreError("recovery_required")
                # Durable start time is host admission time, never a browser value.
                initial = _new(initial, started_at=self._now())
                saved = self._create_task(connection, initial, fence=task_fence)
                token = secrets.token_hex(32)
                connection.execute(
                    receipts.insert().values(
                        request_id=request.request_id,
                        payload_sha256=request.fingerprint(),
                        case_id=request.case_id,
                        mode=request.mode,
                        memory_binding=request.memory.model_dump_json().encode()
                        if request.memory
                        else None,
                        policy_sha256=policy_sha256,
                        initial_sha256=saved.task_sha256,
                        claim_sha256=digest(token.encode("ascii")),
                        consumed=0,
                        active=1,
                        cancel_requested=0,
                    )
                )
                claim = RepairAdmissionClaim(
                    request.request_id, saved.task_sha256, token, owner, nonce
                )
                result = RepairAdmission(saved, False, claim)
            # Exiting the context commits first. Unknown/failed commit never returns claim.
        return result

    def consume(
        self,
        claim: RepairAdmissionClaim,
        expected_initial: Mutation,
        *,
        case_id: str,
        mode: RepairMode,
        policy_sha256: str,
        task_fence: RepairFence,
    ) -> RepairStart:
        if not isinstance(claim, RepairAdmissionClaim):
            raise StoreError("admission_invalid")
        claim.owner.require(self.directory, claim.nonce)
        task_fence.require(self.directory, _uuid(claim.task_id))
        _hash(policy_sha256)
        _hash(claim.token)
        if (
            expected_initial.task.task_id != claim.task_id
            or expected_initial.task_sha256 != claim.initial_sha256
            or digest(_bytes(expected_initial.task)) != claim.initial_sha256
        ):
            raise StoreError("admission_invalid")
        with (
            claim.owner.guard(self.directory, claim.nonce),
            task_fence.guard(self.directory, claim.task_id),
        ):
            with self._base._transaction(write=True) as connection:
                row = self._receipt(connection, claim.task_id)
                current = self._saved(connection, row)
                if (
                    row["consumed"]
                    or not row["active"]
                    or row["case_id"] != case_id
                    or row["mode"] != mode
                    or row["policy_sha256"] != policy_sha256
                    or row["initial_sha256"] != claim.initial_sha256
                    or current.task_sha256 != claim.initial_sha256
                    or current.task.revision != 0
                    or current.task.phase != "ready"
                    or not secrets.compare_digest(
                        row["claim_sha256"], digest(claim.token.encode("ascii"))
                    )
                ):
                    raise StoreError("admission_invalid")
                now = self._now()
                if now < current.task.started_at:
                    raise StoreError("invalid_clock")
                if (
                    connection.execute(
                        update(receipts)
                        .where(
                            receipts.c.request_id == claim.task_id,
                            receipts.c.consumed == 0,
                            receipts.c.active == 1,
                        )
                        .values(consumed=1)
                    ).rowcount
                    != 1
                ):
                    raise StoreError("admission_invalid")
                cancelled = bool(row["cancel_requested"])
                expired = (
                    now - current.task.started_at
                ).total_seconds() >= WEB_BUDGET.timeout_seconds
                if cancelled or expired:
                    current = self._finish_task(
                        connection,
                        claim.task_id,
                        "cancelled" if cancelled else "budget_exceeded",
                        expected_revision=0,
                        expected_sha256=claim.initial_sha256,
                        fence=task_fence,
                        error_code="CANCELLED" if cancelled else "DEADLINE_EXCEEDED",
                    )
                result = RepairStart(current, cancelled, not (cancelled or expired))
        return result

    def request_cancel(self, task_id: str) -> RepairReceipt:
        task_id = _uuid(task_id)
        with self._base._transaction(write=True) as connection:
            row = self._receipt(connection, task_id)
            saved = self._saved(connection, row)
            cancelled = bool(row["cancel_requested"])
            if saved.task.phase != "finished" and row["active"]:
                connection.execute(
                    update(receipts)
                    .where(receipts.c.request_id == task_id)
                    .values(cancel_requested=1)
                )
                cancelled = True
            result = RepairReceipt(
                RepairRequest(
                    request_id=task_id,
                    case_id=row["case_id"],
                    mode=row["mode"],
                    memory=MemoryBinding.model_validate_json(row["memory_binding"])
                    if row["memory_binding"]
                    else None,
                ),
                saved,
                cancelled,
                bool(row["active"]),
                bool(row["consumed"]),
            )
        return result

    def retire_unstarted(self, task_id: str, *, owner: RepairWebFence, fence: RepairFence) -> None:
        """Explicit recovery consumes an abandoned capability without executing it."""
        with owner.guard(self.directory), fence.guard(self.directory, task_id):
            with self._base._transaction(write=True) as connection:
                row = self._receipt(connection, task_id)
                saved = self._saved(connection, row)
                if row["consumed"] or not row["active"]:
                    return
                connection.execute(
                    update(receipts).where(receipts.c.request_id == task_id).values(consumed=1)
                )
                self._finish_task(
                    connection,
                    task_id,
                    "outcome_unknown",
                    expected_revision=saved.task.revision,
                    expected_sha256=saved.task_sha256,
                    fence=fence,
                    error_code="INTERRUPTED",
                )

    def _release_state(
        self,
        connection: Connection,
        task_id: str,
    ) -> tuple[str, bool, dict[str, OracleReport | None]]:
        row = self._receipt(connection, task_id)
        self._saved(connection, row)
        task, requests, candidates = self._read(connection, task_id)
        repro = reproduction_records(connection, task_id)
        comparisons = comparison_records(connection, task_id)
        events = [
            _load(RepairEvent, e)
            for e in connection.execute(
                select(repair_events)
                .where(repair_events.c.task_id == task_id)
                .order_by(repair_events.c.revision)
            ).mappings()
        ]
        # Include the complete audited evidence, not only the task revision.
        stamp = digest(
            canonical(
                {
                    "receipt": {
                        **dict(row),
                        "memory_binding": digest(row["memory_binding"])
                        if row["memory_binding"] is not None
                        else None,
                    },
                    "task": task.model_dump(mode="json"),
                    "requests": {k: v.model_dump(mode="json") for k, v in requests.items()},
                    "candidates": {k: v.model_dump(mode="json") for k, v in candidates.items()},
                    "reproductions": {k: v.model_dump(mode="json") for k, v in repro.items()},
                    "comparisons": {k: v.model_dump(mode="json") for k, v in comparisons.items()},
                    "events": [e.model_dump(mode="json") for e in events],
                }
            )
        )
        settled = (
            task.phase == "finished"
            and bool(row["consumed"])
            and not any(r.status == "pending" for r in requests.values())
            and not any(c.status in ("preparing", "outcome_unknown") for c in candidates.values())
        )
        reports: dict[str, OracleReport | None] = {}
        reconciled = {e.request.request_id for e in events if e.kind == "reconciled" and e.request}
        for request_id, record in repro.items():
            reports[record.plan.attempt_id] = record.report
            if record.report is None and request_id not in reconciled:
                settled = False
        for record2 in comparisons.values():
            actual = {r.report.attempt_id: r.report for r in record2.results}
            for attempt in record2.plan.attempts:
                reports[attempt] = actual.get(attempt)
            if record2.report is None and record2.plan.request_id not in reconciled:
                settled = False
        return stamp, settled, reports

    def release_state(self, task_id: str) -> tuple[str, bool, dict[str, OracleReport | None]]:
        with self._base._transaction() as connection:
            return self._release_state(connection, task_id)

    def _release_cas(
        self,
        task_id: str,
        expected_stamp: str,
        *,
        owner: RepairWebFence,
        fence: RepairFence,
    ) -> None:
        """Private commit step; only recovery's positively checked resource path calls it."""
        with owner.guard(self.directory), fence.guard(self.directory, task_id):
            with self._base._transaction(write=True) as connection:
                stamp, settled, _ = self._release_state(connection, task_id)
                if stamp != expected_stamp or not settled:
                    raise StoreError("recovery_required")
                connection.execute(
                    update(receipts)
                    .where(receipts.c.request_id == task_id, receipts.c.active == 1)
                    .values(active=0)
                )
