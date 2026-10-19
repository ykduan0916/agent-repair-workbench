"""B4 CAS-protected human review and exact diff export."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from sqlalchemy import Connection, select, update

from agent_workbench.candidate_execution.compare import verify_versions
from agent_workbench.candidates.contracts import SnapshotRef
from agent_workbench.candidates.snapshots import SnapshotHandle, SnapshotStore
from agent_workbench.mcp_config import canonical, digest
from agent_workbench.repair_store.comparisons import ComparisonStore
from agent_workbench.repair_store.comparisons import _records as _comparison_records
from agent_workbench.repair_store.models import CandidateRecord
from agent_workbench.run_store.models import StoreError
from agent_workbench.run_store.schema import (
    repair_candidate_decision_events,
    repair_candidate_decisions,
)
from agent_workbench.run_store.store import _uuid

from .comparison_models import ComparisonRecord
from .decision_models import CandidateDecision, Decision, DecisionEvent, DecisionReceipt
from .fencing import RepairFence
from .models import RepairTask
from .store import _load


def _raw(value: CandidateDecision) -> bytes:
    raw = canonical(value.model_dump(mode="json"))
    if len(raw) > 32768:
        raise StoreError("repair_limit")
    return raw


def _handles(
    root: Path, task: RepairTask, candidate: CandidateRecord
) -> tuple[SnapshotHandle, SnapshotHandle]:
    if candidate.snapshot is None:
        raise StoreError("candidate_not_ready")
    snapshots = SnapshotStore(root / "repair-snapshots")
    baseline = SnapshotRef.model_validate_json(task.baseline.model_dump_json())
    changed = SnapshotRef.model_validate_json(candidate.snapshot.model_dump_json())
    return SnapshotHandle(baseline, snapshots.root / baseline.snapshot_id), SnapshotHandle(
        changed, snapshots.root / changed.snapshot_id
    )


class DecisionStore(ComparisonStore):
    def _read_event(
        self, connection: Connection, task_id: str, request_id: str
    ) -> CandidateDecision | None:
        row = (
            connection.execute(
                select(repair_candidate_decision_events).where(
                    repair_candidate_decision_events.c.task_id == task_id,
                    repair_candidate_decision_events.c.request_id == request_id,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        value = _load(CandidateDecision, row)
        if (
            value.task_id != task_id
            or value.candidate_id != row["candidate_id"]
            or value.request_id != request_id
            or digest(_raw(value)) != row["sha256"]
        ):
            raise StoreError("store_corrupt")
        return value

    @staticmethod
    def _global_revision(connection: Connection, task_id: str) -> int:
        values = (
            connection.execute(
                select(repair_candidate_decision_events.c.revision)
                .where(repair_candidate_decision_events.c.task_id == task_id)
                .order_by(repair_candidate_decision_events.c.revision)
            )
            .scalars()
            .all()
        )
        expected = list(range(1, len(values) + 1))
        if values != expected:
            raise StoreError("store_corrupt")
        return len(values)

    def _receipt(
        self,
        connection: Connection,
        task_id: str,
        candidate_id: str,
        decision: CandidateDecision | None,
        candidate: CandidateRecord,
        comparison: ComparisonRecord | None,
    ) -> DecisionReceipt:
        candidate_sha = candidate.snapshot.tree_sha256 if candidate.snapshot else None
        comparison_sha = (
            digest(canonical(comparison.model_dump(mode="json"))) if comparison else None
        )
        current_valid = bool(
            decision
            and candidate_sha == decision.candidate_sha256
            and comparison_sha == decision.comparison_sha256
            and comparison is not None
            and comparison.status == "finished"
            and comparison.report is not None
            and comparison.report.outcome == "candidate_verified"
        )
        evidence_ready = bool(
            candidate.status == "committed"
            and candidate.snapshot is not None
            and comparison is not None
            and comparison.status == "finished"
            and comparison.report is not None
            and comparison.report.outcome == "candidate_verified"
        )
        accepted_other = False
        for item in self._heads(connection, task_id):
            if item.candidate_id == candidate_id or item.decision != "accepted":
                continue
            other_candidate = self._read_candidate(connection, task_id, item.candidate_id)
            other_comparison = self._read_comparison(connection, task_id, item.candidate_id)
            if self._decision_current(item, other_candidate, other_comparison):
                accepted_other = True
                break
        return DecisionReceipt(
            task_id=task_id,
            candidate_id=candidate_id,
            decision=decision,
            candidate_sha256=candidate_sha,
            comparison_sha256=comparison_sha,
            revision=self._global_revision(connection, task_id),
            current_valid=current_valid,
            can_accept=evidence_ready
            and not accepted_other
            and (decision is None or decision.decision != "accepted"),
            can_reject=evidence_ready and (decision is None or decision.decision != "rejected"),
            can_download=current_valid and decision is not None and decision.decision == "accepted",
        )

    def _heads(self, connection: Connection, task_id: str) -> tuple[CandidateDecision, ...]:
        rows = connection.execute(
            select(repair_candidate_decisions).where(
                repair_candidate_decisions.c.task_id == task_id
            )
        ).mappings()
        result: list[CandidateDecision] = []
        for row in rows:
            value = _load(CandidateDecision, row)
            if value.task_id != task_id or value.candidate_id != row["candidate_id"]:
                raise StoreError("store_corrupt")
            result.append(value)
        return tuple(result)

    def _read_candidate(
        self, connection: Connection, task_id: str, candidate_id: str
    ) -> CandidateRecord:
        _, _, candidates = self._read(connection, task_id)
        candidate = candidates.get(candidate_id)
        if candidate is None:
            raise StoreError("candidate_not_found")
        return candidate

    def _read_comparison(
        self, connection: Connection, task_id: str, candidate_id: str
    ) -> ComparisonRecord | None:
        return next(
            (
                item
                for item in _comparison_records(connection, task_id).values()
                if item.plan.candidate.snapshot_id == candidate_id
            ),
            None,
        )

    @staticmethod
    def _decision_current(
        decision: CandidateDecision,
        candidate: CandidateRecord,
        comparison: ComparisonRecord | None,
    ) -> bool:
        return bool(
            candidate.snapshot
            and candidate.snapshot.tree_sha256 == decision.candidate_sha256
            and comparison
            and digest(canonical(comparison.model_dump(mode="json"))) == decision.comparison_sha256
            and comparison.status == "finished"
            and comparison.report is not None
            and comparison.report.outcome == "candidate_verified"
        )

    def _read_decision(
        self, connection: Connection, task_id: str, candidate_id: str
    ) -> CandidateDecision | None:
        rows = (
            connection.execute(
                select(repair_candidate_decisions)
                .where(
                    repair_candidate_decisions.c.task_id == task_id,
                    repair_candidate_decisions.c.candidate_id == candidate_id,
                )
                .limit(2)
            )
            .mappings()
            .all()
        )
        if not rows:
            return None
        if len(rows) != 1:
            raise StoreError("store_corrupt")
        row = rows[0]
        value = _load(CandidateDecision, row)
        if (
            value.task_id != task_id
            or value.candidate_id != candidate_id
            or value.decision_id != row["decision_id"]
        ):
            raise StoreError("store_corrupt")
        if value.revision != row["revision"]:
            raise StoreError("store_corrupt")
        return value

    def decision(self, task_id: str, candidate_id: str) -> DecisionReceipt:
        _uuid(task_id)
        _uuid(candidate_id)
        with self._base._transaction() as connection:
            task, _, candidates = self._read(connection, task_id)
            comparisons = _comparison_records(connection, task_id)
            candidate = candidates.get(candidate_id)
            if candidate is None:
                raise StoreError("candidate_not_found")
            comparison = next(
                (
                    item
                    for item in comparisons.values()
                    if item.plan.candidate.snapshot_id == candidate_id
                ),
                None,
            )
            return self._receipt(
                connection,
                task_id,
                candidate_id,
                self._read_decision(connection, task_id, candidate_id),
                candidate,
                comparison,
            )

    def _verified(
        self,
        task: RepairTask,
        candidate: CandidateRecord,
        comparison: ComparisonRecord,
        image_root: Path,
    ) -> tuple[SnapshotHandle, SnapshotHandle]:
        baseline, changed = _handles(self.directory, task, candidate)
        if not verify_versions(comparison.plan, baseline, changed, image_root):
            raise StoreError("version_mismatch")
        if comparison.status != "finished" or comparison.report is None:
            raise StoreError("comparison_not_finished")
        return baseline, changed

    def record(
        self,
        task_id: str,
        candidate_id: str,
        *,
        request_id: str,
        decision: str,
        expected_revision: int,
        expected_candidate_sha256: str,
        expected_comparison_sha256: str,
        image_root: Path,
    ) -> DecisionReceipt:
        _uuid(task_id)
        _uuid(candidate_id)
        _uuid(request_id)
        if (
            decision not in ("accepted", "rejected")
            or type(expected_revision) is not int
            or not 0 <= expected_revision < 256
        ):
            raise StoreError("invalid_decision")
        with RepairFence(self.directory, task_id) as fence:
            with self._base._transaction(write=True) as connection:
                task, _, candidates = self._read(connection, task_id)
                comparisons = _comparison_records(connection, task_id)
                fence.require(self.directory, task_id)
                candidate = candidates.get(candidate_id)
                if candidate is None:
                    raise StoreError("candidate_not_found")
                comparison = next(
                    (
                        item
                        for item in comparisons.values()
                        if item.plan.candidate.snapshot_id == candidate_id
                    ),
                    None,
                )
                historical = self._read_event(connection, task_id, request_id)
                if historical is not None:
                    if (
                        historical.candidate_id != candidate_id
                        or historical.decision != decision
                        or historical.candidate_sha256 != expected_candidate_sha256
                        or historical.comparison_sha256 != expected_comparison_sha256
                    ):
                        raise StoreError("request_conflict")
                    return self._receipt(
                        connection, task_id, candidate_id, historical, candidate, comparison
                    )
                if candidate.status != "committed" or candidate.snapshot is None:
                    raise StoreError("candidate_not_ready")
                if comparison is None:
                    raise StoreError("comparison_not_found")
                candidate_sha = candidate.snapshot.tree_sha256
                comparison_sha = digest(canonical(comparison.model_dump(mode="json")))
                if (
                    candidate_sha != expected_candidate_sha256
                    or comparison_sha != expected_comparison_sha256
                ):
                    raise StoreError("version_mismatch")
                self._verified(task, candidate, comparison, image_root)
                if decision == "accepted" and (
                    comparison.report is None or comparison.report.outcome != "candidate_verified"
                ):
                    raise StoreError("acceptance_requires_verified")
                if decision == "accepted" and any(
                    item.candidate_id != candidate_id
                    and item.decision == "accepted"
                    and self._decision_current(
                        item,
                        self._read_candidate(connection, task_id, item.candidate_id),
                        self._read_comparison(connection, task_id, item.candidate_id),
                    )
                    for item in self._heads(connection, task_id)
                ):
                    raise StoreError("decision_conflict")
                current = self._read_decision(connection, task_id, candidate_id)
                if current and current.request_id == request_id:
                    if (
                        current.decision != decision
                        or current.candidate_sha256 != candidate_sha
                        or current.comparison_sha256 != comparison_sha
                    ):
                        raise StoreError("request_conflict")
                    return self._receipt(
                        connection, task_id, candidate_id, current, candidate, comparison
                    )
                current_revision = current.revision if current else 0
                global_revision = self._global_revision(connection, task_id)
                if current_revision >= 255 or global_revision >= 255:
                    raise StoreError("decision_limit")
                if global_revision != expected_revision:
                    raise StoreError("decision_conflict")
                now = datetime.now(UTC)
                choice = cast(Decision, decision)
                event = DecisionEvent(
                    candidate_id=candidate_id,
                    decision=choice,
                    revision=current_revision + 1,
                    request_id=request_id,
                    created_at=now,
                )
                value = CandidateDecision(
                    decision_id=current.decision_id if current else str(uuid4()),
                    task_id=task_id,
                    candidate_id=candidate_id,
                    request_id=request_id,
                    decision=choice,
                    revision=event.revision,
                    candidate_sha256=candidate_sha,
                    comparison_id=comparison.plan.comparison_id,
                    comparison_sha256=comparison_sha,
                    created_at=now,
                    history=tuple(current.history[-7:] if current else ()) + (event,),
                )
                raw = _raw(value)
                global_event_revision = global_revision + 1
                if current:
                    result = connection.execute(
                        update(repair_candidate_decisions)
                        .where(
                            repair_candidate_decisions.c.task_id == task_id,
                            repair_candidate_decisions.c.candidate_id == candidate_id,
                            repair_candidate_decisions.c.revision == current_revision,
                            repair_candidate_decisions.c.sha256 == digest(_raw(current)),
                        )
                        .values(
                            revision=value.revision,
                            request_id=request_id,
                            payload=raw,
                            sha256=digest(raw),
                        )
                    )
                    if result.rowcount != 1:
                        raise StoreError("decision_conflict")
                else:
                    connection.execute(
                        repair_candidate_decisions.insert().values(
                            decision_id=value.decision_id,
                            task_id=task_id,
                            candidate_id=candidate_id,
                            request_id=request_id,
                            revision=value.revision,
                            payload=raw,
                            sha256=digest(raw),
                        )
                    )
                connection.execute(
                    repair_candidate_decision_events.insert().values(
                        event_id=str(uuid4()),
                        task_id=task_id,
                        candidate_id=candidate_id,
                        request_id=request_id,
                        revision=global_event_revision,
                        payload=raw,
                        sha256=digest(raw),
                    )
                )
                return self._receipt(
                    connection, task_id, candidate_id, value, candidate, comparison
                )

    def download(self, task_id: str, candidate_id: str, image_root: Path) -> tuple[str, bytes]:
        _uuid(task_id)
        _uuid(candidate_id)
        with self._base._transaction() as connection:
            task, _, candidates = self._read(connection, task_id)
            comparisons = _comparison_records(connection, task_id)
            decision = self._read_decision(connection, task_id, candidate_id)
            candidate = candidates.get(candidate_id)
            comparison = next(
                (
                    item
                    for item in comparisons.values()
                    if item.plan.candidate.snapshot_id == candidate_id
                ),
                None,
            )
            if (
                not decision
                or decision.decision != "accepted"
                or not candidate
                or not comparison
                or not candidate.snapshot
            ):
                raise StoreError("download_not_authorized")
            if (
                decision.candidate_sha256 != candidate.snapshot.tree_sha256
                or decision.comparison_sha256
                != digest(canonical(comparison.model_dump(mode="json")))
            ):
                raise StoreError("decision_stale")
            if (
                comparison.status != "finished"
                or comparison.report is None
                or comparison.report.outcome != "candidate_verified"
            ):
                raise StoreError("decision_stale")
            baseline, changed = self._verified(task, candidate, comparison, image_root)
            diff = SnapshotStore(baseline.root.parent).diff(baseline, changed)
            name = f"agent-repair-{candidate.sequence}-{candidate.candidate_id[:8]}.patch"
            return name, diff.unified_diff.encode("utf-8")
