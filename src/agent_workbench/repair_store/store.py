"""P0-06 atomic admission. Filesystem publication stays outside SQLite transactions."""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Connection, Table, literal_column, select, update

from agent_workbench.analysis_store.models import ErrorCode
from agent_workbench.analysis_store.store import AnalysisStore
from agent_workbench.candidates.contracts import CandidatePatch, SnapshotRef
from agent_workbench.mcp_config import canonical, digest
from agent_workbench.model_gateway.gateway import PreparedCall, Reply
from agent_workbench.model_gateway.rejections import rejection_code
from agent_workbench.orchestration.budget import reserve_usage, settle
from agent_workbench.orchestration.contracts import Contract, Usage
from agent_workbench.orchestration.policy import PolicyError
from agent_workbench.run_store.models import StoreError
from agent_workbench.run_store.schema import (
    repair_candidates,
    repair_events,
    repair_requests,
    repair_tasks,
)
from agent_workbench.run_store.store import RunStore, _uuid

from .fencing import RepairFence
from .models import (
    CandidateRecord,
    Mutation,
    Outcome,
    RepairClaim,
    RepairEvent,
    RepairTask,
    RequestRecord,
    Role,
    Tool,
)

MAX_REQUESTS = 32
PERMISSIONS = {
    "single": ("T01", "T02", "T03", "T04", "T05", "T06", "T07"),
    "diagnosis": ("T01", "T02", "T03", "T04", "T05"),
    "repair": ("T01", "T02", "T03", "T06"),
    "verification": ("T01", "T02", "T03", "T04", "T05", "T07"),
}


def _validated[T: Contract](model: type[T], value: T) -> T:
    try:
        return model.model_validate_json(value.model_dump_json())
    except (ValidationError, ValueError, TypeError, AttributeError):
        raise StoreError("invalid_repair_input") from None


def _bytes(value: Contract) -> bytes:
    raw = canonical(value.model_dump(mode="json"))
    if len(raw) > 32768:
        raise StoreError("repair_limit")
    return raw


def _load[T: Contract](model: type[T], row: Any) -> T:
    try:
        raw = row["payload"]
        if not isinstance(raw, bytes) or len(raw) > 32768 or digest(raw) != row["sha256"]:
            raise ValueError
        result = model.model_validate_json(raw)
        if _bytes(result) != raw:
            raise ValueError
        return result
    except (ValidationError, ValueError, TypeError, KeyError):
        raise StoreError("store_corrupt") from None


def _new[T: Contract](model: T, **changes: Any) -> T:
    return _validated(type(model), model.model_copy(update=changes))


def _permission(task: RepairTask, role: Role, tool: Tool | None = None) -> None:
    if role not in PERMISSIONS or ((task.mode == "single_role") != (role == "single")):
        raise StoreError("permission_denied")
    if tool is not None and tool not in PERMISSIONS[role]:
        raise StoreError("permission_denied")


class RepairStore:
    def __init__(self, directory: Path, *, clock: Callable[[], float] = time.time) -> None:
        self._base = RunStore(directory, clock=clock)
        self.directory = self._base.directory

    def initialize(self) -> None:
        self._base.initialize()

    def close(self) -> None:
        self._base.close()

    def _now(self) -> datetime:
        try:
            return datetime.fromtimestamp(self._base._now(), UTC)
        except (OverflowError, ValueError, OSError):
            raise StoreError("invalid_clock") from None

    @staticmethod
    def _put(
        connection: Connection,
        table: Table,
        obj: Contract,
        keys: dict[str, Any],
        *,
        replace: bool = False,
    ) -> None:
        raw = _bytes(obj)
        values = {**keys, "payload": raw, "sha256": digest(raw)}
        if replace:
            condition = [table.c[k] == v for k, v in keys.items()]
            if connection.execute(update(table).where(*condition).values(**values)).rowcount != 1:
                raise StoreError("stale_version")
        else:
            connection.execute(table.insert().values(**values))

    def _read(
        self, connection: Connection, task_id: str
    ) -> tuple[RepairTask, dict[str, RequestRecord], dict[str, CandidateRecord]]:
        row = (
            connection.execute(select(repair_tasks).where(repair_tasks.c.task_id == task_id))
            .mappings()
            .first()
        )
        if row is None:
            raise StoreError("repair_not_found")
        task = _load(RepairTask, row)
        if task.task_id != task_id or task.revision != row["revision"]:
            raise StoreError("store_corrupt")
        requests: dict[str, RequestRecord] = {}
        candidates: dict[str, CandidateRecord] = {}

        def records[M: RequestRecord | CandidateRecord](
            table: Table, model: type[M], identity: str, bound: int
        ) -> dict[str, M]:
            result: dict[str, M] = {}
            rows = (
                connection.execute(select(table).where(table.c.task_id == task_id).limit(bound + 1))
                .mappings()
                .all()
            )
            if len(rows) > bound:
                raise StoreError("store_corrupt")
            for item in rows:
                record = _load(model, item)
                key = getattr(record, identity)
                if record.task_id != task_id or key != item[identity]:
                    raise StoreError("store_corrupt")
                if isinstance(record, CandidateRecord) and (
                    record.sequence != item["sequence"] or record.request_id != item["request_id"]
                ):
                    raise StoreError("store_corrupt")
                result[key] = record
            return result

        requests = records(repair_requests, RequestRecord, "request_id", MAX_REQUESTS)
        candidates = records(repair_candidates, CandidateRecord, "candidate_id", 2)
        self._audit(connection, task, requests, candidates)
        from .comparisons import audit_comparisons

        audit_comparisons(connection, task, requests, candidates)
        from .reproductions import audit_reproductions

        audit_reproductions(connection, task, requests)
        return task, requests, candidates

    @staticmethod
    def _audit(
        connection: Connection,
        task: RepairTask,
        requests: dict[str, RequestRecord],
        candidates: dict[str, CandidateRecord],
    ) -> None:
        """Rebuild request states and accounting from immutable events; no trusted counters."""
        rows = (
            connection.execute(
                select(repair_events)
                .where(repair_events.c.task_id == task.task_id)
                .order_by(repair_events.c.revision)
                .limit(257)
            )
            .mappings()
            .all()
        )
        seen: dict[str, RequestRecord] = {}
        candidate_history: dict[str, CandidateRecord] = {}
        last_at = task.started_at
        if len(rows) != task.revision + 1:
            raise StoreError("store_corrupt")
        for revision, row in enumerate(rows):
            event = _load(RepairEvent, row)
            if (
                event.task_id != task.task_id
                or event.revision != revision
                or row["revision"] != revision
                or event.at < last_at
            ):
                raise StoreError("store_corrupt")
            last_at = event.at
            if (revision == 0) != (event.kind == "created"):
                raise StoreError("store_corrupt")
            req, candidate = event.request, event.candidate
            if req is not None:
                previous = seen.get(req.request_id)
                if req.task_id != task.task_id or (previous is None) != (event.kind == "reserved"):
                    raise StoreError("store_corrupt")
                if previous is None:
                    if req.status != "pending" or any(r.status == "pending" for r in seen.values()):
                        raise StoreError("store_corrupt")
                else:
                    if (
                        previous.status != "pending"
                        or req.status == "pending"
                        or event.kind not in ("settled", "reconciled")
                    ):
                        raise StoreError("store_corrupt")
                    stable = {"status", "known_input_tokens", "known_output_tokens", "error_code"}
                    if req.model_dump(exclude=stable) != previous.model_dump(exclude=stable):
                        raise StoreError("store_corrupt")
                seen[req.request_id] = req
            elif event.kind in ("reserved", "settled"):
                raise StoreError("store_corrupt")
            if candidate:
                if (
                    req is None
                    or req.operation != "candidate"
                    or candidate.request_id != req.request_id
                ):
                    raise StoreError("store_corrupt")
                previous_candidate = candidate_history.get(candidate.candidate_id)
                if previous_candidate is not None and candidate.model_dump(
                    exclude={"status", "snapshot", "error_code", "finished_at"}
                ) != previous_candidate.model_dump(
                    exclude={"status", "snapshot", "error_code", "finished_at"}
                ):
                    raise StoreError("store_corrupt")
                candidate_history[candidate.candidate_id] = candidate
        if (
            seen != requests
            or candidate_history != candidates
            or _load(RepairEvent, rows[-1]).task_sha256 != digest(_bytes(task))
        ):
            raise StoreError("store_corrupt")
        models = [r for r in requests.values() if r.operation == "model"]
        tools = [r for r in requests.values() if r.operation != "model"]
        reserved = sum(
            r.input_reserved + r.output_reserved
            for r in models
            if r.status in ("pending", "outcome_unknown")
        )
        try:
            usage = Usage(
                model_calls=len(models),
                tool_calls=len(tools),
                reserved_tokens=reserved,
                known_input_tokens=sum(r.known_input_tokens or 0 for r in models),
                known_output_tokens=sum(r.known_output_tokens or 0 for r in models),
                usage_complete=not reserved,
            )
            for req in requests.values():
                _permission(task, req.role, req.tool)
            if (
                usage != task.usage
                or len(candidates) != task.candidate_count
                or sorted(c.sequence for c in candidates.values())
                != list(range(1, len(candidates) + 1))
            ):
                raise ValueError
            if {r.candidate_id for r in requests.values() if r.operation == "candidate"} != set(
                candidates
            ):
                raise ValueError
            for candidate in candidates.values():
                request = requests[candidate.request_id]
                status = {
                    "preparing": "pending",
                    "committed": "completed",
                    "failed": "failed",
                    "outcome_unknown": "outcome_unknown",
                }[candidate.status]
                if (
                    candidate.task_id != task.task_id
                    or candidate.owner_role != request.role
                    or candidate.candidate_id != request.candidate_id
                    or status != request.status
                    or candidate.expected_baseline_hash != task.baseline.tree_sha256
                    or candidate.expected_file_hash != task.baseline.file_sha256
                ):
                    raise ValueError
                if candidate.snapshot and (
                    candidate.snapshot.baseline_id != task.baseline.snapshot_id
                    or candidate.snapshot.manifest_sha256 != task.baseline.manifest_sha256
                ):
                    raise ValueError
            pending = [r for r in requests.values() if r.status == "pending"]
            phase = (
                {
                    "model": "model_pending",
                    "tool": "tool_pending",
                    "candidate": "candidate_preparing",
                    "comparison": "comparing",
                    "reproduction": "tool_pending",
                }[pending[0].operation]
                if pending
                else "ready"
            )
            if (
                len(pending) > 1
                or (task.phase != "finished" and task.phase != phase)
                or (task.phase == "finished" and pending)
            ):
                raise ValueError
            if (
                any(r.status == "outcome_unknown" for r in requests.values())
                and task.outcome != "outcome_unknown"
            ):
                raise ValueError
        except (ValueError, KeyError, StoreError):
            raise StoreError("store_corrupt") from None

    def list_tasks(self, limit: int = 20) -> list[Mutation]:
        """Newest admitted SQLite rows first; audit each bounded task before exposing it."""
        if type(limit) is not int or not 1 <= limit <= 50:
            raise StoreError("invalid_limit")
        if not self._base._present():
            return []
        with self._base._transaction() as connection:
            ids = (
                connection.execute(
                    select(repair_tasks.c.task_id)
                    .order_by(literal_column("rowid").desc())
                    .limit(limit)
                )
                .scalars()
                .all()
            )
            values = []
            for task_id in ids:
                task, _, _ = self._read(connection, task_id)
                values.append(Mutation(task, digest(_bytes(task))))
            return values

    def get(self, task_id: str) -> Mutation:
        with self._base._transaction() as connection:
            task, _, _ = self._read(connection, _uuid(task_id))
            return Mutation(task, digest(_bytes(task)))

    def candidates(self, task_id: str) -> list[CandidateRecord]:
        with self._base._transaction() as connection:
            _, _, records = self._read(connection, _uuid(task_id))
            return sorted(records.values(), key=lambda c: c.sequence)

    def create_task(self, task: RepairTask, *, fence: RepairFence) -> Mutation:
        with self._base._transaction(write=True) as connection:
            return self._create_task(connection, task, fence=fence)

    def _create_task(
        self, connection: Connection, task: RepairTask, *, fence: RepairFence
    ) -> Mutation:
        task = _validated(RepairTask, task)
        fence.require(self.directory, task.task_id)
        if (
            task.revision
            or task.phase != "ready"
            or task.usage != Usage()
            or task.candidate_count
            or task.error_code
        ):
            raise StoreError("invalid_repair_input")
        if connection.execute(
            select(repair_tasks.c.task_id).where(repair_tasks.c.task_id == task.task_id)
        ).first():
            raise StoreError("repair_id_conflict")
        if task.source_analysis_id:
            AnalysisStore(self.directory)._get(connection, task.source_analysis_id)
        self._put(connection, repair_tasks, task, {"task_id": task.task_id, "revision": 0})
        event = RepairEvent(
            task_id=task.task_id,
            revision=0,
            kind="created",
            task_sha256=digest(_bytes(task)),
            at=task.started_at,
        )
        self._put(connection, repair_events, event, {"task_id": task.task_id, "revision": 0})
        return Mutation(task, digest(_bytes(task)))

    @staticmethod
    def _cas(task: RepairTask, revision: int, sha256: str) -> None:
        if type(revision) is not int or task.revision != revision or digest(_bytes(task)) != sha256:
            raise StoreError("stale_version")
        if task.phase == "finished":
            raise StoreError("repair_finished")
        if task.revision >= 255:
            raise StoreError("repair_limit")

    def _commit(
        self,
        connection: Connection,
        old: RepairTask,
        new: RepairTask,
        kind: str,
        req: RequestRecord | None = None,
        candidate: CandidateRecord | None = None,
        *,
        insert: bool = False,
    ) -> Mutation:
        new = _new(new, revision=old.revision + 1)
        raw = _bytes(new)
        changed = connection.execute(
            update(repair_tasks)
            .where(
                repair_tasks.c.task_id == old.task_id,
                repair_tasks.c.revision == old.revision,
                repair_tasks.c.sha256 == digest(_bytes(old)),
            )
            .values(revision=new.revision, payload=raw, sha256=digest(raw))
        )
        if changed.rowcount != 1:
            raise StoreError("stale_version")
        if req:
            self._put(
                connection,
                repair_requests,
                req,
                {"task_id": req.task_id, "request_id": req.request_id},
                replace=not insert,
            )
        if candidate:
            self._put(
                connection,
                repair_candidates,
                candidate,
                {
                    "task_id": candidate.task_id,
                    "candidate_id": candidate.candidate_id,
                    "request_id": candidate.request_id,
                    "sequence": candidate.sequence,
                },
                replace=not insert,
            )
        event = RepairEvent.model_validate(
            {
                "task_id": new.task_id,
                "revision": new.revision,
                "kind": kind,
                "task_sha256": digest(raw),
                "at": self._now(),
                "request": req,
                "candidate": candidate,
            }
        )
        self._put(
            connection, repair_events, event, {"task_id": new.task_id, "revision": new.revision}
        )
        self._read(connection, new.task_id)
        return Mutation(new, digest(raw), req, candidate)

    def _reserve(
        self,
        task_id: str,
        request_id: str,
        role: Role,
        operation: str,
        payload: dict[str, Any],
        *,
        expected_revision: int,
        expected_sha256: str,
        fence: RepairFence,
        prepared: PreparedCall | None = None,
        patch: CandidatePatch | None = None,
        candidate_id: str | None = None,
        tool: Tool | None = None,
    ) -> Mutation:
        _uuid(task_id)
        _uuid(request_id)
        fence.require(self.directory, task_id)
        payload_hash = digest(
            canonical({"operation": operation, "role": role, "tool": tool, **payload})
        )
        with self._base._transaction(write=True) as connection:
            task, requests, candidates = self._read(connection, task_id)
            _permission(task, role, tool)
            previous = requests.get(request_id)
            if previous:
                if previous.payload_sha256 != payload_hash:
                    raise StoreError("request_conflict")
                return Mutation(
                    task,
                    digest(_bytes(task)),
                    previous,
                    candidates.get(previous.candidate_id or ""),
                )
            self._cas(task, expected_revision, expected_sha256)
            if task.phase != "ready" or not task.usage.usage_complete:
                raise StoreError("request_pending")
            now = self._now()
            if (
                now < task.started_at
                or (now - task.started_at).total_seconds() >= task.budget.timeout_seconds
            ):
                raise StoreError("deadline_exceeded")
            if len(requests) >= MAX_REQUESTS:
                raise StoreError("repair_limit")
            token = secrets.token_hex(32)
            fields: dict[str, Any] = dict(
                task_id=task_id,
                request_id=request_id,
                role=role,
                operation=operation,
                tool=tool,
                payload_sha256=payload_hash,
                claim_sha256=digest(token.encode()),
            )
            candidate = None
            usage = task.usage
            if prepared is not None:
                if task.model_config_sha256 is None:
                    raise StoreError("model_not_configured")
                try:
                    usage = reserve_usage(task.budget, usage, prepared)
                except (PolicyError, ValidationError):
                    raise StoreError("budget_exceeded") from None
                fields.update(
                    input_reserved=prepared.input_reserved,
                    output_reserved=prepared.output_reserved,
                    reservation_sha256=prepared.reservation_sha256,
                )
                phase = "model_pending"
            else:
                if usage.tool_calls >= task.budget.tool_calls:
                    raise StoreError("budget_exceeded")
                usage = _new(usage, tool_calls=usage.tool_calls + 1)
                phase = "tool_pending"
            if patch is not None:
                if task.candidate_count >= task.budget.candidates:
                    raise StoreError("candidate_limit")
                if (
                    patch.expected_baseline_hash != task.baseline.tree_sha256
                    or patch.expected_file_hash != task.baseline.file_sha256
                ):
                    raise StoreError("stale_baseline")
                if (
                    candidate_id == task.baseline.snapshot_id
                    or connection.execute(
                        select(repair_candidates.c.candidate_id).where(
                            repair_candidates.c.candidate_id == candidate_id
                        )
                    ).first()
                ):
                    raise StoreError("candidate_id_conflict")
                candidate = CandidateRecord.model_validate(
                    dict(
                        task_id=task_id,
                        request_id=request_id,
                        candidate_id=candidate_id,
                        sequence=task.candidate_count + 1,
                        owner_role=role,
                        expected_baseline_hash=patch.expected_baseline_hash,
                        expected_file_hash=patch.expected_file_hash,
                        patch_sha256=digest(canonical(patch.model_dump(mode="json"))),
                        created_at=now,
                    )
                )
                fields["candidate_id"] = candidate_id
                phase = "candidate_preparing"
            req = RequestRecord.model_validate(fields)
            new = _new(
                task,
                usage=usage,
                phase=phase,
                candidate_count=task.candidate_count + bool(candidate),
            )
            result = self._commit(connection, task, new, "reserved", req, candidate, insert=True)
            return Mutation(
                result.task,
                result.task_sha256,
                req,
                candidate,
                RepairClaim(task_id, request_id, token),
            )

    def reserve_model(
        self,
        task_id: str,
        request_id: str,
        role: Role,
        prepared: PreparedCall,
        *,
        expected_revision: int,
        expected_sha256: str,
        fence: RepairFence,
    ) -> Mutation:
        # Validate numeric types before pure accounting (bool must not act as int).
        if (
            type(prepared.input_reserved) is not int
            or type(prepared.output_reserved) is not int
            or not 0 <= prepared.input_reserved <= 2_000_000
            or not 1 <= prepared.output_reserved <= 32768
        ):
            raise StoreError("invalid_repair_input")
        for sha in (prepared.request_sha256, prepared.reservation_sha256):
            if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
                raise StoreError("invalid_repair_input")
        return self._reserve(
            task_id,
            request_id,
            role,
            "model",
            {
                "request_sha256": prepared.request_sha256,
                "reservation_sha256": prepared.reservation_sha256,
                "input": prepared.input_reserved,
                "output": prepared.output_reserved,
            },
            expected_revision=expected_revision,
            expected_sha256=expected_sha256,
            fence=fence,
            prepared=prepared,
        )

    def reserve_tool(
        self,
        task_id: str,
        request_id: str,
        role: Role,
        tool: Tool,
        request_sha256: str,
        *,
        expected_revision: int,
        expected_sha256: str,
        fence: RepairFence,
    ) -> Mutation:
        if (
            tool not in ("T01", "T02", "T03", "T04", "T05")
            or len(request_sha256) != 64
            or any(c not in "0123456789abcdef" for c in request_sha256)
        ):
            raise StoreError("invalid_repair_input")
        return self._reserve(
            task_id,
            request_id,
            role,
            "tool",
            {"request_sha256": request_sha256},
            expected_revision=expected_revision,
            expected_sha256=expected_sha256,
            fence=fence,
            tool=tool,
        )

    def reserve_candidate(
        self,
        task_id: str,
        request_id: str,
        candidate_id: str,
        role: Role,
        patch: CandidatePatch,
        *,
        expected_revision: int,
        expected_sha256: str,
        fence: RepairFence,
    ) -> Mutation:
        patch = _validated(CandidatePatch, patch)
        _uuid(candidate_id)
        return self._reserve(
            task_id,
            request_id,
            role,
            "candidate",
            {
                "candidate_id": candidate_id,
                "patch_sha256": digest(canonical(patch.model_dump(mode="json"))),
            },
            expected_revision=expected_revision,
            expected_sha256=expected_sha256,
            fence=fence,
            tool="T06",
            patch=patch,
            candidate_id=candidate_id,
        )

    def _finish(
        self,
        claim: RepairClaim,
        *,
        expected_revision: int,
        expected_sha256: str,
        fence: RepairFence,
        operation: str,
        reply: Reply | None = None,
        snapshot: SnapshotRef | None = None,
        error_code: ErrorCode | None = None,
    ) -> Mutation:
        fence.require(self.directory, _uuid(claim.task_id))
        with self._base._transaction(write=True) as connection:
            task, requests, candidates = self._read(connection, claim.task_id)
            self._cas(task, expected_revision, expected_sha256)
            req = requests.get(claim.request_id)
            if (
                req is None
                or req.operation != operation
                or req.status != "pending"
                or not secrets.compare_digest(req.claim_sha256, digest(claim.token.encode()))
            ):
                raise StoreError("claim_conflict")
            usage = task.usage
            status = "failed" if error_code else "completed"
            unknown = error_code in ("TOOL_OUTCOME_UNKNOWN", "RESULT_UNKNOWN", "INTERRUPTED")
            if unknown:
                status = "outcome_unknown"
            changes: dict[str, Any] = {}
            if reply is not None:
                if reply.error:
                    error_code = rejection_code(reply.error)
                prepared = PreparedCall(
                    b"",
                    "0" * 64,
                    req.input_reserved,
                    req.output_reserved,
                    req.reservation_sha256 or "",
                )
                try:
                    usage = settle(usage, prepared, reply)
                    unknown = not reply.usage_known
                except (PolicyError, ValidationError):
                    unknown = True
                    error_code = "MODEL_USAGE_BOUND_VIOLATION"
                if unknown:
                    status = "outcome_unknown"
                    error_code = error_code or "MODEL_USAGE_UNKNOWN"
                else:
                    changes.update(
                        known_input_tokens=reply.input_tokens,
                        known_output_tokens=reply.output_tokens,
                    )
                if reply.error and not unknown:
                    status = "failed"
            req = _new(req, status=status, error_code=error_code, **changes)
            candidate = candidates.get(req.candidate_id or "")
            if candidate:
                if snapshot and (
                    snapshot.baseline_id != task.baseline.snapshot_id
                    or snapshot.manifest_sha256 != task.baseline.manifest_sha256
                ):
                    raise StoreError("snapshot_binding")
                candidate = _new(
                    candidate,
                    status="committed"
                    if snapshot
                    else ("outcome_unknown" if unknown else "failed"),
                    snapshot=snapshot,
                    error_code=error_code,
                    finished_at=self._now(),
                )
            new = _new(
                task,
                usage=usage,
                phase="finished" if unknown else "ready",
                outcome="outcome_unknown" if unknown else None,
                error_code=error_code if unknown else None,
                finished_at=self._now() if unknown else None,
            )
            return self._commit(connection, task, new, "settled", req, candidate)

    def settle_model(
        self,
        claim: RepairClaim,
        reply: Reply,
        *,
        expected_revision: int,
        expected_sha256: str,
        fence: RepairFence,
    ) -> Mutation:
        return self._finish(
            claim,
            expected_revision=expected_revision,
            expected_sha256=expected_sha256,
            fence=fence,
            operation="model",
            reply=reply,
        )

    def finish_tool(
        self,
        claim: RepairClaim,
        *,
        expected_revision: int,
        expected_sha256: str,
        fence: RepairFence,
        error_code: ErrorCode | None = None,
    ) -> Mutation:
        return self._finish(
            claim,
            expected_revision=expected_revision,
            expected_sha256=expected_sha256,
            fence=fence,
            operation="tool",
            error_code=error_code,
        )

    def finish_candidate(
        self,
        claim: RepairClaim,
        *,
        expected_revision: int,
        expected_sha256: str,
        fence: RepairFence,
        snapshot: SnapshotRef | None = None,
        error_code: ErrorCode | None = None,
    ) -> Mutation:
        if (snapshot is None) == (error_code is None):
            raise StoreError("invalid_repair_input")
        if snapshot:
            snapshot = _validated(SnapshotRef, snapshot)
        return self._finish(
            claim,
            expected_revision=expected_revision,
            expected_sha256=expected_sha256,
            fence=fence,
            operation="candidate",
            snapshot=snapshot,
            error_code=error_code,
        )

    def finish_task(
        self,
        task_id: str,
        outcome: Outcome,
        *,
        expected_revision: int,
        expected_sha256: str,
        fence: RepairFence,
        error_code: ErrorCode | None = None,
    ) -> Mutation:
        with self._base._transaction(write=True) as connection:
            return self._finish_task(
                connection,
                task_id,
                outcome,
                expected_revision=expected_revision,
                expected_sha256=expected_sha256,
                fence=fence,
                error_code=error_code,
            )

    def _finish_task(
        self,
        connection: Connection,
        task_id: str,
        outcome: Outcome,
        *,
        expected_revision: int,
        expected_sha256: str,
        fence: RepairFence,
        error_code: ErrorCode | None = None,
    ) -> Mutation:
        if outcome == "candidate_verified":
            raise StoreError("invalid_repair_input")
        fence.require(self.directory, _uuid(task_id))
        task, _, _ = self._read(connection, task_id)
        self._cas(task, expected_revision, expected_sha256)
        if task.phase != "ready":
            raise StoreError("request_pending")
        new = _new(
            task,
            phase="finished",
            outcome=outcome,
            error_code=error_code,
            finished_at=self._now(),
        )
        return self._commit(connection, task, new, "finished")

    def reconcile(self, task_id: str, *, expected_revision: int, expected_sha256: str) -> Mutation:
        with RepairFence(self.directory, _uuid(task_id)) as fence:
            return self.reconcile_held(
                task_id,
                expected_revision=expected_revision,
                expected_sha256=expected_sha256,
                fence=fence,
            )

    def reconcile_held(
        self, task_id: str, *, expected_revision: int, expected_sha256: str, fence: RepairFence
    ) -> Mutation:
        fence.require(self.directory, _uuid(task_id))
        with self._base._transaction(write=True) as connection:
            task, requests, candidates = self._read(connection, task_id)
            self._cas(task, expected_revision, expected_sha256)
            req = next((r for r in requests.values() if r.status == "pending"), None)
            if req and req.operation == "reproduction":
                raise StoreError("reproduction_recovery_required")
            if req and req.operation == "comparison":
                raise StoreError("comparison_recovery_required")
            candidate = candidates.get(req.candidate_id or "") if req else None
            if req:
                req = _new(req, status="outcome_unknown", error_code="INTERRUPTED")
            if candidate:
                candidate = _new(
                    candidate,
                    status="outcome_unknown",
                    error_code="INTERRUPTED",
                    finished_at=self._now(),
                )
            new = _new(
                task,
                phase="finished",
                outcome="outcome_unknown",
                error_code="INTERRUPTED",
                finished_at=self._now(),
            )
            return self._commit(connection, task, new, "reconciled", req, candidate)
