"""Atomic browser admission, one-use startup claims and conservative slot release."""

import secrets
from dataclasses import replace

from sqlalchemy import select, update
from sqlalchemy.engine import Connection, RowMapping

from agent_workbench.analysis_store.fencing import AnalysisFence
from agent_workbench.analysis_store.models import AnalysisEvent, SaveResult
from agent_workbench.analysis_store.store import AnalysisStore, _hash
from agent_workbench.experience_memory.agent_models import MemoryBinding, agent_budget
from agent_workbench.mcp_config import digest
from agent_workbench.orchestration.contracts import AnalysisSummary
from agent_workbench.orchestration.policy import transition
from agent_workbench.run_store.models import StoreError
from agent_workbench.run_store.schema import analysis_summaries, events, web_analysis_requests
from agent_workbench.run_store.store import TERMINAL, _uuid
from agent_workbench.web_analyses.fencing import AnalysisWebFence
from agent_workbench.web_analyses.models import (
    WEB_BUDGET,
    AnalysisAdmission,
    AnalysisClaim,
    AnalysisRequest,
    AnalysisStart,
    CaseName,
)


class WebAnalysisStore(AnalysisStore):
    def _receipt(self, connection: Connection, task_id: str) -> RowMapping:
        row = (
            connection.execute(
                select(web_analysis_requests).where(web_analysis_requests.c.request_id == task_id)
            )
            .mappings()
            .first()
        )
        if row is None:
            raise StoreError("web_analysis_not_found")
        try:
            request = AnalysisRequest(
                request_id=row["request_id"],
                case_name=row["case_name"],
                intent=row["intent"],
                memory=MemoryBinding.model_validate_json(row["memory_binding"])
                if row["memory_binding"]
                else None,
            )
            if request.fingerprint() != row["payload_sha256"]:
                raise ValueError
            for key in ("policy_sha256", "initial_sha256", "claim_sha256"):
                _hash(row[key])
            if any(
                type(row[key]) is not int or row[key] not in (0, 1)
                for key in ("active", "consumed", "cancel_requested")
            ):
                raise ValueError
        except (ValueError, TypeError, StoreError):
            raise StoreError("analysis_corrupt") from None
        return row

    def _existing(
        self, connection: Connection, request: AnalysisRequest
    ) -> AnalysisAdmission | None:
        found = connection.execute(
            select(web_analysis_requests.c.request_id).where(
                web_analysis_requests.c.request_id == request.request_id
            )
        ).first()
        if found is None:
            return None
        row = self._receipt(connection, request.request_id)
        if row["payload_sha256"] != request.fingerprint():
            raise StoreError("request_conflict")
        try:
            saved = self._get(connection, request.request_id)
        except StoreError as error:
            if error.code == "analysis_not_found":
                raise StoreError("request_gone") from None
            raise
        if saved.summary.state.intent != request.intent:
            raise StoreError("analysis_corrupt")
        return AnalysisAdmission(saved, bool(row["cancel_requested"]))

    def existing(self, request: AnalysisRequest) -> AnalysisAdmission | None:
        with self._transaction() as connection:
            return self._existing(connection, request)

    def active_id(self) -> str | None:
        with self._transaction() as connection:
            active = connection.execute(
                select(web_analysis_requests.c.request_id).where(
                    web_analysis_requests.c.active == 1
                )
            ).scalar_one_or_none()
            if active is not None:
                self._receipt(connection, active)
            return active

    def _release_safe(self, connection: Connection) -> bool:
        active = connection.execute(
            select(web_analysis_requests.c.request_id).where(web_analysis_requests.c.active == 1)
        ).scalar_one_or_none()
        if active is None:
            return True
        self._receipt(connection, active)
        saved = self._get(connection, active)
        if saved.summary.state.phase != "finished":
            return False
        for record_id in saved.summary.execution_record_ids:
            run = self._view(connection, record_id)
            if run.status not in TERMINAL or run.executor_state not in ("absent", "stopped"):
                return False
            latest = connection.execute(
                select(events.c.kind)
                .where(events.c.run_id == record_id)
                .order_by(events.c.version.desc())
                .limit(1)
            ).scalar()
            if latest != "finished" or run.executor_state != "absent":
                return False
            report = self._report(connection, run)
            if report.execution.cleanup not in (
                "removed",
                "not_created",
            ) or report.execution.error in (
                "cleanup_unconfirmed",
                "create_unconfirmed",
                "attach_cleanup_unconfirmed",
            ):
                return False
        connection.execute(
            update(web_analysis_requests)
            .where(web_analysis_requests.c.request_id == active)
            .values(active=0)
        )
        return True

    def release_safe(self, owner: AnalysisWebFence) -> bool:
        owner.require(self.directory)
        with self._transaction(write=True) as connection:
            return self._release_safe(connection)

    def admit(
        self,
        request: AnalysisRequest,
        summary: AnalysisSummary,
        *,
        policy_sha256: str,
        owner: AnalysisWebFence,
        fence: AnalysisFence,
    ) -> AnalysisAdmission:
        owner.require(self.directory)
        fence.require(self.directory, request.request_id)
        _hash(policy_sha256)
        if (
            summary.task_id != request.request_id
            or summary.state.intent != request.intent
            or summary.source_kind != "synthetic"
            or summary.budget != agent_budget(request.memory, WEB_BUDGET)
        ):
            raise StoreError("invalid_analysis")
        with self._transaction(write=True) as connection:
            existing = self._existing(connection, request)
            if existing is not None:
                return existing
            if (
                connection.execute(
                    select(analysis_summaries.c.task_id).where(
                        analysis_summaries.c.task_id == request.request_id
                    )
                ).first()
                is not None
            ):
                raise StoreError("request_conflict")
            if not self._release_safe(connection):
                raise StoreError("recovery_required")
            saved = self._create_analysis(
                connection, summary, AnalysisEvent(kind="created"), fence=fence
            )
            token = secrets.token_hex(32)
            connection.execute(
                web_analysis_requests.insert().values(
                    request_id=request.request_id,
                    payload_sha256=request.fingerprint(),
                    case_name=request.case_name,
                    intent=request.intent,
                    memory_binding=request.memory.model_dump_json().encode()
                    if request.memory
                    else None,
                    policy_sha256=policy_sha256,
                    initial_sha256=saved.sha256,
                    claim_sha256=digest(token.encode("ascii")),
                    consumed=0,
                    active=1,
                    cancel_requested=0,
                )
            )
            claim = AnalysisClaim(request.request_id, saved.sha256, token, owner)
            return AnalysisAdmission(saved, False, claim)

    def start_created(
        self,
        claim: AnalysisClaim,
        saved: SaveResult,
        *,
        fence: AnalysisFence,
        case_name: CaseName,
    ) -> AnalysisStart:
        """Consume admission and advance queued->checking in the same transaction."""
        claim.owner.require(self.directory)
        fence.require(self.directory, claim.task_id)
        if saved.summary.task_id != claim.task_id or saved.sha256 != claim.initial_sha256:
            raise StoreError("admission_invalid")
        if not isinstance(claim.token, str) or len(claim.token) != 64:
            raise StoreError("admission_invalid")
        try:
            token_hash = digest(claim.token.encode("ascii"))
        except UnicodeError:
            raise StoreError("admission_invalid") from None
        with self._transaction(write=True) as connection:
            row = self._receipt(connection, claim.task_id)
            current = self._get(connection, claim.task_id)
            if (
                row["case_name"] != case_name
                or row["consumed"]
                or not row["active"]
                or not secrets.compare_digest(row["claim_sha256"], token_hash)
                or row["initial_sha256"] != claim.initial_sha256
                or current.sha256 != saved.sha256
                or current.summary.summary_revision != 0
                or current.summary.state.phase != "queued"
            ):
                raise StoreError("admission_invalid")
            state = transition(current.summary.state, "checking", expected_revision=0)
            updated = current.summary.model_copy(update={"state": state, "summary_revision": 1})
            started = self._save(
                connection, updated, AnalysisEvent(kind="phase_changed"), 0, current.sha256
            )
            connection.execute(
                update(web_analysis_requests)
                .where(web_analysis_requests.c.request_id == claim.task_id)
                .values(consumed=1)
            )
            return AnalysisStart(replace(started, reused=False), bool(row["cancel_requested"]))

    def request_cancel(self, task_id: str) -> bool:
        _uuid(task_id)
        with self._transaction(write=True) as connection:
            row = self._receipt(connection, task_id)
            saved = self._get(connection, task_id)
            if not row["active"] or saved.summary.state.phase == "finished":
                return False
            connection.execute(
                update(web_analysis_requests)
                .where(web_analysis_requests.c.request_id == task_id)
                .values(cancel_requested=1)
            )
            return True

    def cancellation_requested(self, task_id: str) -> bool:
        _uuid(task_id)
        with self._transaction() as connection:
            return bool(self._receipt(connection, task_id)["cancel_requested"])
