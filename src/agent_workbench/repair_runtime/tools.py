"""P0-06 tool bridge: durable claim before any execution, never resume pending work.

A bridge belongs to one newly started repair task and an externally held whole-life fence.
The caller must stop after requires_reconcile, release its fence, then explicitly reconcile.
Rebuilding a bridge does not restore T05 authorization or resume a model/execution loop.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from pathlib import Path
from typing import TypedDict, cast
from uuid import UUID, uuid5

import anyio
from pydantic import JsonValue

from agent_workbench.candidate_execution.compare import (
    ComparisonPlan,
    SlotResult,
    compare,
)
from agent_workbench.candidate_execution.compare import (
    verify_versions as verify_comparison,
)
from agent_workbench.candidate_execution.docker import verified_image
from agent_workbench.candidate_execution.oracle import ORACLE_SHA256, SUITE_SHA256, run_oracle
from agent_workbench.candidates.contracts import CandidateError, CandidatePatch
from agent_workbench.candidates.diagnostics import candidate_failure_code
from agent_workbench.candidates.snapshots import SnapshotHandle, SnapshotStore
from agent_workbench.controlled_tools.contracts import (
    ReadEvidence,
    Reproduce,
    ToolDecision,
    ToolRequest,
    target_key,
)
from agent_workbench.controlled_tools.resources import TaskResourceRegistry
from agent_workbench.controlled_tools.responses import ErrorCode, ResourceError
from agent_workbench.controlled_tools.runtime import ToolRuntime, _owned
from agent_workbench.mcp_config import canonical, digest
from agent_workbench.orchestration.contracts import EvidenceRef
from agent_workbench.orchestration.policy import PolicyError, authorize
from agent_workbench.repair_cases.bindings import validate_baseline_binding
from agent_workbench.repair_contracts.codec import decode_role
from agent_workbench.repair_contracts.common import RepairRole, RoleConclusion
from agent_workbench.repair_contracts.tools import (
    ApplyCandidate,
    CompareCandidate,
    RepairToolRequest,
)
from agent_workbench.repair_store.fencing import RepairFence
from agent_workbench.repair_store.models import Mutation, RepairClaim, Tool
from agent_workbench.repair_store.reproduction_models import (
    ReproductionPlan,
    ReproductionRecord,
    reproduction_id,
    result_outcome,
)
from agent_workbench.repair_store.reproductions import ReproductionStore, verify_versions
from agent_workbench.run_store.models import StoreError

from .feedback import comparison_feedback, reproduction_feedback
from .responses import RepairToolResponse, RepairToolResult

TOOLS: dict[str, Tool] = {
    "read_evidence": "T01",
    "read_contract": "T02",
    "read_source": "T03",
    "check_contract": "T04",
    "request_reproduction": "T05",
    "apply_candidate": "T06",
    "compare_candidate": "T07",
}
PENDING = {"model_pending", "tool_pending", "candidate_preparing", "comparing"}


class _CAS(TypedDict):
    expected_revision: int
    expected_sha256: str
    fence: RepairFence


def _cancelled() -> bool:
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


class RepairToolBridge:
    def __init__(
        self,
        *,
        store: ReproductionStore,
        registry: TaskResourceRegistry,
        baseline: SnapshotHandle,
        snapshots: SnapshotStore,
        image_root: Path,
        fence: RepairFence,
        remaining_seconds: Callable[[], float],
    ) -> None:
        self.store, self.registry = store, registry
        self.baseline, self.snapshots = baseline, snapshots
        self.image_root, self.fence = image_root, fence
        self.remaining_seconds = remaining_seconds
        self.task_id = registry.task_spec().task_id
        fence.require(store.directory, self.task_id)
        self._cursor = store.get(self.task_id)
        self._binding(self._cursor)
        self._observed: str | None = None
        self._seen: dict[str, str] = {}
        self._cache: dict[str, RepairToolResponse] = {}
        self._busy = False
        self._stopped = False

    def _binding(self, cursor: Mutation) -> None:
        task = self.registry.task_spec()
        profile = validate_baseline_binding(self.baseline.ref)
        if (
            cursor.task.task_id != task.task_id
            or cursor.task.budget != task.budget
            or cursor.task.baseline != self.baseline.ref
            or self.registry.source_sha256() != self.baseline.ref.file_sha256
            or self.registry.case_name != profile.case_id
            or self.registry.manifest_sha256 != profile.manifest_sha256
        ):
            raise ResourceError("PERMISSION_DENIED")
        self.snapshots.verify(self.baseline)

    def _cas(self) -> _CAS:
        return {
            "expected_revision": self._cursor.task.revision,
            "expected_sha256": self._cursor.task_sha256,
            "fence": self.fence,
        }

    def _allowance(self) -> float:
        remaining = self.remaining_seconds()
        if not math.isfinite(remaining) or remaining < 21:
            raise ResourceError("BUDGET_EXCEEDED")
        # Twenty seconds retained for attach/creation acknowledgements and cleanup.
        return min(25.0, remaining - 20.0)

    def _response(
        self,
        request: RepairToolRequest,
        *,
        payload: dict[str, JsonValue] | None = None,
        refs: tuple[EvidenceRef, ...] = (),
        error: ErrorCode | None = None,
        cancelled: bool = False,
        replayed: bool = False,
        reconcile: bool = False,
    ) -> RepairToolResult:
        response = RepairToolResponse(
            request_id=request.request_id,
            tool=request.tool,
            status="ok"
            if error is None
            else "outcome_unknown"
            if error == "RESULT_UNKNOWN"
            else "failed"
            if error == "TOOL_FAILURE"
            else "rejected",
            error_code=error,
            payload=payload or {},
            evidence_refs=refs,
        )
        if reconcile:
            self._stopped = True
        return RepairToolResult(response, self._cursor, cancelled, replayed, reconcile)

    def _registered(self, payload: dict[str, JsonValue]) -> tuple[EvidenceRef, ...]:
        return (self.registry.register({"summary": payload, "findings": [], "events": []}),)

    def _authorize(self, role: RepairRole, request: RepairToolRequest) -> None:
        if isinstance(request, (ApplyCandidate, CompareCandidate)):
            for ref in request.evidence_refs:
                self.registry.evidence(ref, "summary")
            self.registry.ensure_capacity()
        else:
            authorize(
                self.registry.task_spec(),
                "diagnosis" if role == "single" else role,
                ToolDecision(action="request_tool", request=request),
                self.registry.authorization(),
            )
            if not isinstance(request, ReadEvidence):
                self.registry.ensure_capacity()

    async def execute(
        self,
        role: RepairRole,
        request: RepairToolRequest,
        *,
        cursor: Mutation,
    ) -> RepairToolResult:
        if self._busy or self._stopped:
            raise ResourceError("PERMISSION_DENIED")
        self._busy = True
        try:
            # A caller cannot bypass role schemas by passing a mutated Pydantic instance.
            value = decode_role(
                canonical({"action": "request_tool", "request": request.model_dump(mode="json")}),
                role,
            )
            if isinstance(value, RoleConclusion):
                raise ResourceError("INVALID_INPUT")
            request = value.request
            self.fence.require(self.store.directory, self.task_id)
            self._binding(cursor)
            self._cursor = cursor
            self._authorize(role, request)
            fingerprint = digest(
                canonical({"role": role, "request": request.model_dump(mode="json")})
            )
            previous = self._seen.get(request.request_id)
            if previous is not None and previous != fingerprint:
                raise ResourceError("REQUEST_CONFLICT")
            self._seen[request.request_id] = fingerprint
            if isinstance(request, Reproduce):
                return await self._reproduce(role, request)
            if isinstance(request, ApplyCandidate):
                return self._apply(role, request)
            if isinstance(request, CompareCandidate):
                return await self._compare(role, request)
            return await self._read(role, request)
        except ResourceError as error:
            return self._response(request, error=error.code)
        except PolicyError:
            return self._response(request, error="PERMISSION_DENIED")
        except CandidateError:
            return self._response(request, error="ARTIFACT_MISMATCH")
        except StoreError as error:
            if self._cursor.task.phase in PENDING:
                return self._response(request, error="RESULT_UNKNOWN", reconcile=True)
            code: ErrorCode = (
                "BUDGET_EXCEEDED"
                if error.code in ("budget_exceeded", "deadline_exceeded", "candidate_limit")
                else "REQUEST_CONFLICT"
                if "conflict" in error.code
                else "STALE_VERSION"
                if error.code in ("version_mismatch", "stale_baseline")
                else "PERMISSION_DENIED"
                if error.code == "permission_denied"
                else "TOOL_FAILURE"
            )
            return self._response(request, error=code)
        except (OSError, RuntimeError, ValueError):
            pending = self._cursor.task.phase in PENDING
            return self._response(
                request, error="RESULT_UNKNOWN" if pending else "TOOL_FAILURE", reconcile=pending
            )
        finally:
            self._busy = False

    async def _read(self, role: RepairRole, request: ToolRequest) -> RepairToolResult:
        allowance = self._allowance()
        admitted = self.store.reserve_tool(
            self.task_id,
            request.request_id,
            role,
            TOOLS[request.tool],
            target_key(request),
            **self._cas(),
        )
        self._cursor = admitted
        if admitted.claim is None:
            cached = self._cache.get(request.request_id)
            if cached is not None and admitted.request and admitted.request.status == "completed":
                return RepairToolResult(cached, admitted, replayed=True)
            return self._response(
                request,
                error="RESULT_UNKNOWN",
                replayed=True,
                reconcile=admitted.task.phase in PENDING,
            )
        try:
            execution = await ToolRuntime(self.registry).execute(
                "diagnosis" if role == "single" else role, request, timeout_seconds=allowance
            )
        except asyncio.CancelledError:
            self._cursor = self.store.finish_tool(
                admitted.claim, error_code="INTERRUPTED", **self._cas()
            )
            return self._response(request, error="RESULT_UNKNOWN", cancelled=True)
        self._cursor = self.store.finish_tool(
            admitted.claim,
            error_code="TOOL_FAILED" if execution.response.error_code else None,
            **self._cas(),
        )
        response = RepairToolResponse.model_validate_json(execution.response.model_dump_json())
        self._cache[request.request_id] = response
        return RepairToolResult(response, self._cursor, execution.cancelled)

    def _reproduction_plan(self, request: Reproduce) -> ReproductionPlan:
        try:
            return self.store.get_reproduction(self.task_id, request.request_id).plan
        except StoreError as error:
            if error.code != "reproduction_not_found":
                raise
        image = verified_image(self.image_root)
        return ReproductionPlan(
            task_id=self.task_id,
            request_id=request.request_id,
            attempt_id=reproduction_id(self.task_id, request.request_id),
            baseline=self.baseline.ref,
            suite_sha256=SUITE_SHA256,
            oracle_sha256=ORACLE_SHA256,
            image_id=image.image_id,
            image_source_sha256=image.source_sha256,
        )

    def _reproduction_result(
        self,
        request: Reproduce,
        record: ReproductionRecord,
        *,
        replayed: bool,
        cancelled: bool = False,
    ) -> RepairToolResult:
        verified = verify_versions(record.plan, self.baseline, self.image_root)
        if not verified:
            return self._response(request, error="ARTIFACT_MISMATCH", replayed=replayed)
        payload = reproduction_feedback(record)
        observed = payload["status"] == "observed_failure"
        # A duplicate read on a rebuilt bridge must not create execution authorization.
        if observed and not replayed:
            self._observed = request.request_id
        error: ErrorCode | None = (
            None
            if observed
            else "RESULT_UNKNOWN"
            if record.status != "finished" or payload["status"] == "outcome_unknown"
            else "TOOL_FAILURE"
        )
        return self._response(
            request,
            payload=payload,
            refs=self._registered(payload),
            error=error,
            cancelled=cancelled,
            replayed=replayed,
            reconcile=record.status == "reserved",
        )

    async def _reproduce(self, role: RepairRole, request: Reproduce) -> RepairToolResult:
        allowance = self._allowance()
        plan = self._reproduction_plan(request)
        admitted = self.store.reserve_reproduction(
            plan, role=role, baseline=self.baseline, image_root=self.image_root, **self._cas()
        )
        self._cursor = admitted
        assert admitted.reproduction is not None
        if admitted.claim is None:
            return self._reproduction_result(request, admitted.reproduction, replayed=True)
        try:
            report, cancelled = await _owned(
                run_oracle(
                    self.baseline,
                    scenario="wrong_type",
                    attempt_id=plan.attempt_id,
                    image_root=self.image_root,
                    stage_timeout=min(3.0, allowance),
                    total_timeout=allowance,
                ),
                cancel_operation=True,
            )
            with anyio.CancelScope(shield=True):
                finished = self.store.finish_reproduction(
                    admitted.claim,
                    report,
                    baseline=self.baseline,
                    image_root=self.image_root,
                    **self._cas(),
                )
                self._cursor = finished
                assert finished.reproduction is not None
                return self._reproduction_result(
                    request, finished.reproduction, replayed=False, cancelled=cancelled
                )
        except (Exception, asyncio.CancelledError):
            return self._response(
                request,
                error="RESULT_UNKNOWN",
                reconcile=True,
                cancelled=_cancelled(),
            )

        return self._response(request, error="RESULT_UNKNOWN", reconcile=True, cancelled=True)

    def _require_observed(self) -> None:
        if self._observed is None:
            raise ResourceError("PERMISSION_DENIED")
        record = self.store.get_reproduction(self.task_id, self._observed)
        if (
            record.report is None
            or result_outcome(
                record.report, record.versions_verified_after, baseline=record.plan.baseline
            )
            != "observed_failure"
            or not verify_versions(record.plan, self.baseline, self.image_root)
        ):
            raise ResourceError("ARTIFACT_MISMATCH")

    def _apply(self, role: RepairRole, request: ApplyCandidate) -> RepairToolResult:
        self._allowance()
        self._require_observed()
        patch = CandidatePatch(
            expected_baseline_hash=request.expected_baseline_hash,
            expected_file_hash=request.expected_file_hash,
            replacement=request.replacement,
        )
        admitted = self.store.reserve_candidate(
            self.task_id, request.request_id, request.candidate_id, role, patch, **self._cas()
        )
        self._cursor = admitted
        if admitted.claim is None:
            return self._candidate_result(request, replayed=True)
        try:
            snapshot = self.snapshots.create_candidate(self.baseline, request.candidate_id, patch)
        except CandidateError as error:
            unknown = error.code == "SNAPSHOT_COMMIT_UNKNOWN"
            self._cursor = self.store.finish_candidate(
                admitted.claim,
                error_code=candidate_failure_code(error.code),
                **self._cas(),
            )
            return self._response(request, error="RESULT_UNKNOWN" if unknown else "TOOL_FAILURE")
        self._cursor = self.store.finish_candidate(
            admitted.claim, snapshot=snapshot.ref, **self._cas()
        )
        return self._candidate_result(request, replayed=False)

    def _handle(self, candidate_id: str) -> SnapshotHandle:
        record = next(
            (
                item
                for item in self.store.candidates(self.task_id)
                if item.candidate_id == candidate_id
            ),
            None,
        )
        if record is None or record.status != "committed" or record.snapshot is None:
            raise ResourceError("PERMISSION_DENIED")
        handle = SnapshotHandle(record.snapshot, self.baseline.root.parent / candidate_id)
        self.snapshots.verify(handle)
        return handle

    def _candidate_result(self, request: ApplyCandidate, *, replayed: bool) -> RepairToolResult:
        candidate = self._cursor.candidate
        if candidate is None or candidate.status != "committed":
            return self._response(
                request,
                error="RESULT_UNKNOWN"
                if candidate is None or candidate.status in ("preparing", "outcome_unknown")
                else "TOOL_FAILURE",
                replayed=replayed,
                reconcile=self._cursor.task.phase in PENDING,
            )
        handle = self._handle(request.candidate_id)
        payload: dict[str, JsonValue] = {
            "candidate_id": handle.ref.snapshot_id,
            "candidate_tree_hash": handle.ref.tree_sha256,
            "file_sha256": handle.ref.file_sha256,
            "baseline_tree_hash": self.baseline.ref.tree_sha256,
            "status": "committed",
            "accepted": False,
        }
        return self._response(
            request, payload=payload, refs=self._registered(payload), replayed=replayed
        )

    def _comparison_plan(
        self, request: CompareCandidate, candidate: SnapshotHandle
    ) -> ComparisonPlan:
        try:
            record = self.store.get_comparison(self.task_id, request.request_id)
            if record.plan.candidate != candidate.ref:
                raise ResourceError("REQUEST_CONFLICT")
            return record.plan
        except StoreError as error:
            if error.code != "comparison_not_found":
                raise
        image = verified_image(self.image_root)
        namespace = UUID(self.task_id)
        key = "p006:T07:v1:" + request.request_id
        return ComparisonPlan(
            task_id=self.task_id,
            request_id=request.request_id,
            comparison_id=str(uuid5(namespace, key)),
            baseline=self.baseline.ref,
            candidate=candidate.ref,
            attempts=cast(
                tuple[str, str, str, str],
                tuple(str(uuid5(namespace, key + ":" + str(i))) for i in range(4)),
            ),
            suite_sha256=SUITE_SHA256,
            oracle_sha256=ORACLE_SHA256,
            image_id=image.image_id,
            image_source_sha256=image.source_sha256,
        )

    async def _compare(self, role: RepairRole, request: CompareCandidate) -> RepairToolResult:
        self._allowance()
        candidate = self._handle(request.candidate_id)
        if request.expected_candidate_tree_hash != candidate.ref.tree_sha256:
            raise ResourceError("STALE_VERSION")
        plan = self._comparison_plan(request, candidate)
        admitted = self.store.reserve_comparison(
            plan,
            role=role,
            baseline=self.baseline,
            candidate=candidate,
            image_root=self.image_root,
            **self._cas(),
        )
        self._cursor = admitted
        cancelled = False
        if admitted.claim is not None:
            claim: RepairClaim = admitted.claim

            async def save(item: SlotResult) -> None:
                self._cursor = self.store.save_slot(claim, item, **self._cas())

            try:
                _, cancelled = await _owned(
                    compare(
                        plan,
                        self.baseline,
                        candidate,
                        image_root=self.image_root,
                        save_result=save,
                        remaining_seconds=lambda: self.remaining_seconds() - 5,
                    ),
                    cancel_operation=True,
                )
                with anyio.CancelScope(shield=True):
                    self._cursor = self.store.finalize_comparison(
                        claim,
                        baseline=self.baseline,
                        candidate=candidate,
                        image_root=self.image_root,
                        **self._cas(),
                    )
            except (Exception, asyncio.CancelledError):
                return self._response(
                    request,
                    error="RESULT_UNKNOWN",
                    reconcile=True,
                    cancelled=_cancelled(),
                )
        record = self.store.get_comparison(self.task_id, request.request_id)
        if not verify_comparison(plan, self.baseline, candidate, self.image_root):
            return self._response(request, error="ARTIFACT_MISMATCH")
        payload = comparison_feedback(record)
        outcome = record.report.outcome if record.report else None
        error: ErrorCode | None = (
            None
            if outcome in ("candidate_verified", "rejected")
            else "RESULT_UNKNOWN"
            if outcome in (None, "outcome_unknown")
            else "TOOL_FAILURE"
        )
        return self._response(
            request,
            payload=payload,
            refs=self._registered(payload),
            error=error,
            cancelled=cancelled,
            replayed=admitted.claim is None,
            reconcile=record.status == "reserved",
        )
