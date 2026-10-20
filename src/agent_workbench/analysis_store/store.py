"""CAS safe summaries, once-only sends and execution links in SQLite transactions."""

import builtins
import math
import re
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import Connection

from agent_workbench.analysis_store.fencing import AnalysisFence
from agent_workbench.analysis_store.models import (
    AnalysisEvent,
    EventView,
    ExecutionReservation,
    SaveResult,
)
from agent_workbench.analysis_store.validation import validate_ledger, validate_update
from agent_workbench.mcp_config import canonical, digest
from agent_workbench.orchestration.contracts import AnalysisSummary
from agent_workbench.orchestration.policy import transition
from agent_workbench.run_store.models import Claim, RunView, StoreError
from agent_workbench.run_store.schema import (
    analysis_events,
    analysis_execution_links,
    analysis_summaries,
)
from agent_workbench.run_store.store import RunStore, _uuid
from agent_workbench.runner.models import SandboxConfig, SandboxReport


def _hash(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise StoreError("invalid_hash")


def _revision(value: int) -> None:
    if type(value) is not int or not 0 <= value < 256:
        raise StoreError("invalid_revision")


def validated(summary: AnalysisSummary, event: AnalysisEvent) -> tuple[bytes, bytes]:
    try:
        # JSON mode deliberately revalidates nested constructed/copied models and datetime/tuples.
        raw = canonical(summary.model_dump(mode="json"))
        event_raw = canonical(event.model_dump(mode="json"))
        if len(raw) > 262144 or len(event_raw) > 16384:
            raise ValueError
        AnalysisSummary.model_validate_json(raw)
        AnalysisEvent.model_validate_json(event_raw)
        _revision(summary.summary_revision)
        if not set(event.evidence_refs) <= set(summary.evidence_refs):
            raise ValueError
    except (ValueError, TypeError, AttributeError, RecursionError):
        raise StoreError("invalid_analysis") from None
    return raw, event_raw


class AnalysisStore:
    def __init__(self, directory: Path, *, clock: Callable[[], float] = time.time) -> None:
        self._base = RunStore(directory, clock=clock)
        self.directory = self._base.directory

    def initialize(self) -> None:
        self._base.initialize()

    def close(self) -> None:
        self._base.close()

    def _now(self) -> float:
        return self._base._now()

    def _present(self) -> bool:
        return self._base._present()

    def _transaction(self, *, write: bool = False) -> AbstractContextManager[Connection]:
        return self._base._transaction(write=write)

    _view = staticmethod(RunStore._view)
    _report = staticmethod(RunStore._report)

    def _create(
        self,
        connection: Connection,
        run_id: str,
        profile: str,
        scenario: str,
        config_sha256: str,
        case_sha256: str,
    ) -> RunView:
        return self._base._create(connection, run_id, profile, scenario, config_sha256, case_sha256)

    def _claim(self, connection: Connection, run_id: str, version: int) -> Claim:
        return self._base._claim(connection, run_id, version)

    def _events(self, connection: Connection, task_id: str) -> builtins.list[EventView]:
        rows = connection.execute(
            select(analysis_events)
            .where(analysis_events.c.task_id == task_id)
            .order_by(analysis_events.c.revision)
            .limit(257)
        ).mappings()
        result = []
        try:
            for index, row in enumerate(rows):
                raw = row["event_json"]
                if (
                    index >= 256
                    or row["revision"] != index
                    or not isinstance(raw, bytes)
                    or len(raw) > 16384
                    or digest(raw) != row["sha256"]
                ):
                    raise ValueError
                if not math.isfinite(row["created_at"]):
                    raise ValueError
                event = AnalysisEvent.model_validate_json(raw)
                if canonical(event.model_dump(mode="json")) != raw:
                    raise ValueError
                result.append(
                    EventView(
                        revision=index,
                        created_at=row["created_at"],
                        sha256=row["sha256"],
                        event=event,
                    )
                )
        except (ValueError, TypeError):
            raise StoreError("analysis_corrupt") from None
        return result

    def _get(self, connection: Connection, task_id: str) -> SaveResult:
        row = (
            connection.execute(
                select(analysis_summaries).where(analysis_summaries.c.task_id == task_id)
            )
            .mappings()
            .first()
        )
        if row is None:
            raise StoreError("analysis_not_found")
        try:
            raw = row["summary_json"]
            if not isinstance(raw, bytes) or len(raw) > 262144 or digest(raw) != row["sha256"]:
                raise ValueError
            if not math.isfinite(row["updated_at"]):
                raise ValueError
            summary = AnalysisSummary.model_validate_json(raw)
            if (
                summary.task_id != task_id
                or summary.summary_revision != row["revision"]
                or canonical(summary.model_dump(mode="json")) != raw
            ):
                raise ValueError
            events = self._events(connection, task_id)
            if len(events) != row["revision"] + 1:
                raise ValueError
            links = list(
                connection.execute(
                    select(analysis_execution_links.c.record_id).where(
                        analysis_execution_links.c.task_id == task_id
                    )
                ).scalars()
            )
            if set(links) != set(summary.execution_record_ids):
                raise ValueError
            validate_ledger(summary, [item.event for item in events])
            for run_id in links:
                self._view(connection, run_id)
                link = (
                    connection.execute(
                        select(analysis_execution_links).where(
                            analysis_execution_links.c.record_id == run_id
                        )
                    )
                    .mappings()
                    .one()
                )
                related = [
                    item.event
                    for item in events
                    if item.event.call_id == link["request_id"]
                    and item.event.kind == "tool_started"
                ]
                if (
                    len(related) != 1
                    or related[0].tool != "request_reproduction"
                    or related[0].request_sha256 != link["payload_sha256"]
                ):
                    raise ValueError
        except (ValueError, TypeError, StoreError):
            raise StoreError("analysis_corrupt") from None
        return SaveResult(summary, row["sha256"], True)

    @staticmethod
    def _append_event(
        connection: Connection, summary: AnalysisSummary, raw: bytes, now: float
    ) -> None:
        connection.execute(
            analysis_events.insert().values(
                task_id=summary.task_id,
                revision=summary.summary_revision,
                event_json=raw,
                sha256=digest(raw),
                created_at=now,
            )
        )

    def create(
        self, summary: AnalysisSummary, event: AnalysisEvent, *, fence: AnalysisFence
    ) -> SaveResult:
        with self._transaction(write=True) as connection:
            return self._create_analysis(connection, summary, event, fence=fence)

    def _create_analysis(
        self,
        connection: Connection,
        summary: AnalysisSummary,
        event: AnalysisEvent,
        *,
        fence: AnalysisFence,
    ) -> SaveResult:
        raw, event_raw = validated(summary, event)
        fence.require(self.directory, summary.task_id)
        if (
            summary.summary_revision != 0
            or summary.state.phase != "queued"
            or event.kind != "created"
            or summary.execution_record_ids
            or summary.proposal_count
            or summary.usage.model_calls
            or summary.usage.tool_calls
        ):
            raise StoreError("invalid_analysis")
        exists = connection.execute(
            select(analysis_summaries.c.task_id).where(
                analysis_summaries.c.task_id == summary.task_id
            )
        ).first()
        if exists:
            old = self._get(connection, summary.task_id)
            if old.sha256 == digest(raw) and self._events(connection, summary.task_id)[
                0
            ].sha256 == digest(event_raw):
                return old
            raise StoreError("analysis_conflict")
        now = self._now()
        connection.execute(
            analysis_summaries.insert().values(
                task_id=summary.task_id,
                revision=0,
                summary_json=raw,
                sha256=digest(raw),
                updated_at=now,
            )
        )
        self._append_event(connection, summary, event_raw, now)
        return SaveResult(summary, digest(raw), False)

    def _save(
        self,
        connection: Connection,
        summary: AnalysisSummary,
        event: AnalysisEvent,
        expected_revision: int,
        expected_sha256: str,
        *,
        new_record: str | None = None,
    ) -> SaveResult:
        raw, event_raw = validated(summary, event)
        _revision(expected_revision)
        _hash(expected_sha256)
        old = self._get(connection, summary.task_id)
        if old.summary.summary_revision == summary.summary_revision:
            last = self._events(connection, summary.task_id)[-1]
            if old.sha256 == digest(raw) and last.sha256 == digest(event_raw):
                return old
            raise StoreError("analysis_conflict")
        if old.summary.state.phase == "finished":
            raise StoreError("analysis_terminal")
        if (
            old.summary.summary_revision != expected_revision
            or old.sha256 != expected_sha256
            or summary.summary_revision != expected_revision + 1
        ):
            raise StoreError("analysis_conflict")
        expected_records = old.summary.execution_record_ids + ((new_record,) if new_record else ())
        if summary.execution_record_ids != expected_records:
            raise StoreError("execution_link_conflict")
        validate_update(
            old.summary,
            summary,
            event,
            [item.event for item in self._events(connection, summary.task_id)],
        )
        now = self._now()
        changed = connection.execute(
            update(analysis_summaries)
            .where(
                analysis_summaries.c.task_id == summary.task_id,
                analysis_summaries.c.revision == expected_revision,
                analysis_summaries.c.sha256 == expected_sha256,
            )
            .values(
                revision=summary.summary_revision,
                summary_json=raw,
                sha256=digest(raw),
                updated_at=now,
            )
        )
        if changed.rowcount != 1:
            raise StoreError("analysis_conflict")
        self._append_event(connection, summary, event_raw, now)
        return SaveResult(summary, digest(raw), False)

    def save(
        self,
        summary: AnalysisSummary,
        event: AnalysisEvent,
        *,
        expected_revision: int,
        expected_sha256: str,
        fence: AnalysisFence,
        memory_admission: Callable[[Connection], None] | None = None,
    ) -> SaveResult:
        fence.require(self.directory, summary.task_id)
        with self._transaction(write=True) as connection:
            result = self._save(connection, summary, event, expected_revision, expected_sha256)
            if memory_admission is not None and not result.reused:
                if event.kind != "model_started":
                    raise StoreError("memory_admission_invalid")
                memory_admission(connection)
            return result

    def get(self, task_id: str) -> SaveResult:
        _uuid(task_id)
        with self._transaction() as connection:
            return self._get(connection, task_id)

    def list(self, limit: int = 20) -> builtins.list[SaveResult]:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise StoreError("invalid_limit")
        if not self._present():
            return []
        with self._transaction() as connection:
            ids = list(
                connection.execute(
                    select(analysis_summaries.c.task_id)
                    .order_by(analysis_summaries.c.updated_at.desc(), analysis_summaries.c.task_id)
                    .limit(limit)
                ).scalars()
            )
            return [self._get(connection, task_id) for task_id in ids]

    def events(self, task_id: str) -> builtins.list[EventView]:
        _uuid(task_id)
        with self._transaction() as connection:
            self._get(connection, task_id)
            return self._events(connection, task_id)

    def detail(self, task_id: str) -> tuple[SaveResult, builtins.list[EventView]]:
        _uuid(task_id)
        with self._transaction() as connection:
            return self._get(connection, task_id), self._events(connection, task_id)

    def reserve_execution(
        self,
        *,
        task_id: str,
        request_id: str,
        payload_sha256: str,
        config: SandboxConfig,
        case_sha256: str,
        summary: AnalysisSummary,
        expected_revision: int,
        expected_sha256: str,
        fence: AnalysisFence,
        event: AnalysisEvent,
    ) -> ExecutionReservation:
        _uuid(task_id)
        _uuid(request_id)
        _hash(payload_sha256)
        _hash(case_sha256)
        fence.require(self.directory, task_id)
        try:
            config = SandboxConfig.model_validate_json(config.model_dump_json())
        except ValueError:
            raise StoreError("invalid_configuration") from None
        config_hash = digest(canonical(config.model_dump()))
        if (
            summary.task_id != task_id
            or event.kind != "tool_started"
            or event.tool != "request_reproduction"
            or event.call_id != request_id
            or event.request_sha256 != payload_sha256
        ):
            raise StoreError("execution_link_conflict")
        with self._transaction(write=True) as connection:
            existing = self._execution(connection, task_id, request_id)
            if existing:
                row = connection.execute(
                    select(analysis_execution_links.c.payload_sha256).where(
                        analysis_execution_links.c.record_id == existing.run.run_id
                    )
                ).scalar_one()
                if (
                    row != payload_sha256
                    or existing.run.config_sha256 != config_hash
                    or existing.run.case_sha256 != case_sha256
                ):
                    raise StoreError("execution_link_conflict")
                return existing
            record_id = str(uuid4())
            new = summary.model_copy(
                update={"execution_record_ids": summary.execution_record_ids + (record_id,)}
            )
            saved = self._save(
                connection, new, event, expected_revision, expected_sha256, new_record=record_id
            )
            run = self._create(
                connection, record_id, config.profile, config.scenario, config_hash, case_sha256
            )
            claim = self._claim(connection, run.run_id, run.version)
            connection.execute(
                analysis_execution_links.insert().values(
                    record_id=record_id,
                    task_id=task_id,
                    request_id=request_id,
                    payload_sha256=payload_sha256,
                )
            )
            return ExecutionReservation(True, claim.run, claim, saved.summary, saved.sha256)

    def _execution(
        self, connection: Connection, task_id: str, request_id: str
    ) -> ExecutionReservation | None:
        saved = self._get(connection, task_id)
        row = connection.execute(
            select(analysis_execution_links.c.record_id).where(
                analysis_execution_links.c.task_id == task_id,
                analysis_execution_links.c.request_id == request_id,
            )
        ).first()
        if row is None:
            return None
        return ExecutionReservation(
            False, self._view(connection, row[0]), None, saved.summary, saved.sha256
        )

    def get_execution(self, task_id: str, request_id: str) -> ExecutionReservation | None:
        _uuid(task_id)
        _uuid(request_id)
        with self._transaction() as connection:
            return self._execution(connection, task_id, request_id)

    def read_report(self, task_id: str, record_id: str) -> SandboxReport:
        _uuid(task_id)
        _uuid(record_id)
        with self._transaction() as connection:
            saved = self._get(connection, task_id)
            if record_id not in saved.summary.execution_record_ids:
                raise StoreError("execution_link_conflict")
            return self._report(connection, self._view(connection, record_id))

    def reconcile(self, task_id: str, expected_revision: int, expected_sha256: str) -> SaveResult:
        _uuid(task_id)
        with AnalysisFence(self.directory, task_id) as fence:
            old = self.get(task_id)
            if old.summary.state.phase == "finished":
                return old
            state = transition(
                old.summary.state,
                "finished",
                expected_revision=old.summary.state.revision,
                outcome="outcome_unknown",
            )
            summary = old.summary.model_copy(
                update={
                    "state": state,
                    "summary_revision": old.summary.summary_revision + 1,
                    "finished_at": max(
                        datetime.fromtimestamp(self._now(), UTC), old.summary.started_at
                    ),
                }
            )
            return self.save(
                summary,
                AnalysisEvent(kind="reconciled", error_code="INTERRUPTED"),
                expected_revision=expected_revision,
                expected_sha256=expected_sha256,
                fence=fence,
            )
