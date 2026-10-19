"""One-shot bounded repair graph. Durable ledger, never graph state, authorizes effects."""

import asyncio
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypedDict
from uuid import uuid4

import anyio
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from pydantic import JsonValue

from agent_workbench.analysis_store.models import ErrorCode
from agent_workbench.candidate_execution.docker import verified_image
from agent_workbench.candidate_execution.oracle import verify_oracle
from agent_workbench.candidates.snapshots import SnapshotHandle, SnapshotStore
from agent_workbench.controlled_tools.contracts import target_key
from agent_workbench.controlled_tools.resources import TaskResourceRegistry
from agent_workbench.controlled_tools.responses import ResourceError
from agent_workbench.controlled_tools.runtime import _owned
from agent_workbench.mcp_config import digest
from agent_workbench.model_gateway.gateway import GatewayError, PreparedCall, Reply
from agent_workbench.model_gateway.rejections import (
    CORRECTABLE,
    FORMAT_CORRECTIONS,
    rejection_code,
)
from agent_workbench.model_gateway.repair import OUTPUT_LIMIT, RepairAdapter
from agent_workbench.orchestration.policy import PolicyError
from agent_workbench.repair_cases.bindings import validate_baseline_binding
from agent_workbench.repair_contracts.codec import decode_role
from agent_workbench.repair_contracts.common import RepairRole, RoleConclusion
from agent_workbench.repair_store.fencing import RepairFence
from agent_workbench.repair_store.models import Mutation, Outcome, RepairTask
from agent_workbench.repair_store.reproductions import ReproductionStore
from agent_workbench.run_store.models import StoreError
from agent_workbench.web_repairs.models import RepairAdmissionClaim
from agent_workbench.web_repairs.policy import policy_sha256
from agent_workbench.web_repairs.store import WebRepairStore

from .context import context
from .tools import _CAS, PENDING, RepairToolBridge


class Route(TypedDict):
    route: Literal["continue", "end"]


@dataclass(frozen=True)
class RepairResult:
    cursor: Mutation
    requires_reconcile: bool = False
    cancelled: bool = False


class Halt(Exception):
    def __init__(self, outcome: Outcome, code: ErrorCode):
        self.outcome, self.code = outcome, code


class RepairController:
    def __init__(
        self,
        *,
        store: ReproductionStore,
        snapshots: SnapshotStore,
        baseline: SnapshotHandle,
        registry: TaskResourceRegistry,
        adapter: RepairAdapter,
        mode: Literal["single_role", "three_role"],
        image_root: Path,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.store, self.snapshots, self.baseline = store, snapshots, baseline
        self.registry, self.adapter, self.mode = registry, adapter, mode
        self.image_root, self.clock = image_root, clock
        self._web_store: WebRepairStore | None = None
        self._last_wall: float | None = None
        self._used = False
        self._materials: dict[str, dict[str, JsonValue]] = {}
        self._completed_reads: dict[str, dict[str, JsonValue]] = {}
        self._last_tool_feedback: dict[str, JsonValue] | None = None
        self._request_corrections = 0
        self._format_corrections = 0
        self._format_feedback: ErrorCode | None = None
        self._role: RepairRole = "single" if mode == "single_role" else "diagnosis"
        self._observed = False
        self._candidate = False
        self._rejected = False
        self._reconcile = False
        self._cancelled = False
        self.cursor: Mutation
        self.fence: RepairFence
        self.bridge: RepairToolBridge
        self.deadline: float

    def _cas(self) -> _CAS:
        return dict(
            expected_revision=self.cursor.task.revision,
            expected_sha256=self.cursor.task_sha256,
            fence=self.fence,
        )

    def remaining(self) -> float:
        self._check_cancelled()
        value = self.deadline - self.clock()
        if self._web_store is not None:
            now = self._web_store._now().timestamp()
            started = self.cursor.task.started_at.timestamp()
            if now < started or (self._last_wall is not None and now < self._last_wall):
                raise Halt("outcome_unknown", "PERSISTENCE_FAILED")
            self._last_wall = now
            value = min(value, started + self.cursor.task.budget.timeout_seconds - now)
        if not math.isfinite(value) or value <= 0:
            raise Halt("budget_exceeded", "DEADLINE_EXCEEDED")
        return value

    def _stop(self, outcome: Outcome, code: ErrorCode) -> None:
        if self.cursor.task.phase == "finished":
            return
        if self.cursor.task.phase in PENDING:
            self._reconcile = True
            return
        try:
            self.cursor = self.store.finish_task(
                self.cursor.task.task_id, outcome, error_code=code, **self._cas()
            )
        except (StoreError, OSError):
            # An unacknowledged commit cannot be reconstructed from a stale cursor.
            # Return its last acknowledged state, require explicit reconciliation.
            self._reconcile = True

    def _check_cancelled(self) -> None:
        task = asyncio.current_task()
        requested = (
            self._web_store is not None
            and self._web_store.receipt(self.cursor.task.task_id).cancel_requested
        )
        if requested or (task is not None and task.cancelling()):
            self._cancelled = True
            raise Halt("cancelled", "CANCELLED")

    async def _send(self, request: PreparedCall, timeout: float) -> Reply:
        async with asyncio.timeout(timeout):
            return await self.adapter.send(request, timeout)

    async def _model(self) -> Reply:
        self._check_cancelled()
        self.remaining()
        if self.adapter.config_sha256 != self.cursor.task.model_config_sha256:
            raise Halt("failed", "MODEL_CONFIGURATION_INVALID")
        messages = [
            {"role": "system", "content": self.adapter.prompt(self._role)},
            {
                "role": "user",
                "content": context(
                    self.registry,
                    self.cursor,
                    self._role,
                    self._materials,
                    self._completed_reads,
                    self._last_tool_feedback,
                ),
            },
        ]
        if self._format_feedback is not None:
            # Host-owned enums only: no rejected text, validation values or prior tool replay.
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Your last reply was rejected before any tool action from that reply. "
                        f"Reason: {self._format_feedback}. Return one emit_decision matching "
                        "the current role schema. Use the current execution_progress and "
                        "completed_reads; do not repeat completed work. This is the only "
                        "format correction allowed for this task."
                    ),
                }
            )
        prepared = self.adapter.prepare(
            self._role, messages, min(OUTPUT_LIMIT, self.cursor.task.budget.output_tokens)
        )
        if len(prepared.body) > 65536 or digest(prepared.body) != prepared.request_sha256:
            raise Halt("failed", "INVALID_MODEL_OUTPUT")
        self._check_cancelled()
        admitted = self.store.reserve_model(
            self.cursor.task.task_id, str(uuid4()), self._role, prepared, **self._cas()
        )
        self.cursor = admitted
        if admitted.claim is None:
            self._reconcile = True
            raise Halt("outcome_unknown", "RESULT_UNKNOWN")
        try:
            self._check_cancelled()
            reply, interrupted = await _owned(
                self._send(prepared, min(120.0, self.remaining())), cancel_operation=True
            )
        except Halt as halt:
            interrupted = halt.outcome == "cancelled"
            reply = Reply(error="MODEL_CANCELLED" if interrupted else "MODEL_TIMEOUT")
        except (asyncio.CancelledError, TimeoutError) as error:
            interrupted = isinstance(error, asyncio.CancelledError)
            reply = Reply(error="MODEL_CANCELLED" if interrupted else "MODEL_TIMEOUT")
        except Exception:
            interrupted = False
            reply = Reply(error="MODEL_TRANSPORT_ERROR")
        if not reply.error:
            code = self._decision_error(reply)
            if code:
                reply = replace(reply, decision=None, error=code)
        self.cursor = self.store.settle_model(admitted.claim, reply, **self._cas())
        if interrupted:
            self._cancelled = True
            raise Halt("cancelled", "CANCELLED")
        if self.cursor.task.phase == "finished":
            return reply
        return reply

    def _decision_error(self, reply: Reply) -> ErrorCode | None:
        if reply.decision is None:
            return "MODEL_DECISION_INVALID"
        try:
            decision = decode_role(reply.decision, self._role, diagnostic=True)
        except PolicyError as error:
            reason = error.args[0] if error.args and isinstance(error.args[0], str) else None
            return (
                "MODEL_DECISION_LIMIT"
                if reason == "MESSAGE_LIMIT_EXCEEDED"
                else rejection_code(reason)
            )
        if isinstance(decision, RoleConclusion):
            try:
                self._next_role(decision)
            except Halt as halt:
                return halt.code
        return None

    def _next_role(self, decision: RoleConclusion) -> RepairRole:
        for ref in decision.evidence_refs:
            try:
                self.registry.evidence(ref, "summary")
            except ResourceError:
                raise Halt("inconclusive", "MODEL_EVIDENCE_INVALID") from None
        if decision.disposition == "needs_evidence":
            raise Halt("inconclusive", "MODEL_EVIDENCE_REQUIRED")
        if self._role == "diagnosis" and self._observed:
            return "repair"
        elif self._role == "repair" and self._candidate:
            return "verification"
        elif self._role == "verification" and self._rejected:
            return "repair"
        else:
            raise Halt("inconclusive", "MODEL_HANDOFF_INVALID")

    async def _turn(self, _: Route) -> Route:
        reply = await self._model()
        self._check_cancelled()
        if self.cursor.task.phase == "finished":
            return {"route": "end"}
        if reply.error or reply.decision is None:
            code = rejection_code(reply.error)
            if (
                code in CORRECTABLE
                and reply.usage_known
                and self.cursor.task.phase == "ready"
                and self._format_corrections < FORMAT_CORRECTIONS
            ):
                self._format_corrections += 1
                self._format_feedback = code
                return {"route": "continue"}
            raise Halt("inconclusive", code)
        self._format_feedback = None
        assert reply.decision is not None
        decision = decode_role(reply.decision, self._role)
        if isinstance(decision, RoleConclusion):
            self._role = self._next_role(decision)
            return {"route": "continue"}
        # Preserve the tool request identity: bridge replay must remain read-only.
        request = decision.request
        before = self.cursor
        result = await self.bridge.execute(self._role, request, cursor=self.cursor)
        self.cursor = result.cursor
        self._cancelled = self._cancelled or result.cancelled
        if result.requires_reconcile:
            self._reconcile = True
            return {"route": "end"}
        if result.cancelled:
            raise Halt("cancelled", "CANCELLED")
        if self.cursor.task.phase == "finished":
            return {"route": "end"}
        if result.response.error_code:
            if (
                result.response.error_code
                in ("INVALID_INPUT", "PERMISSION_DENIED", "STALE_VERSION")
                and result.response.status == "rejected"
                and self.cursor.task.phase == "ready"
                and self.cursor.task_sha256 == before.task_sha256
                and not result.replayed
                and self._request_corrections < 2
            ):
                # Rejected before durable admission: ask the model to correct parameters.
                # Never retry accepted/unknown work or replay the previous tool request.
                self._request_corrections += 1
                self._last_tool_feedback = {
                    "tool": request.tool,
                    "error_code": result.response.error_code,
                    "accepted": False,
                    "corrections_remaining": 2 - self._request_corrections,
                }
                return {"route": "continue"}
            if result.response.error_code == "BUDGET_EXCEEDED":
                raise Halt("budget_exceeded", "BUDGET_EXCEEDED")
            raise Halt("inconclusive", "TOOL_FAILED")
        self._last_tool_feedback = None
        self._materials[request.tool] = result.response.payload
        if request.tool in ("read_evidence", "read_contract", "read_source", "check_contract"):
            self._completed_reads[target_key(request)] = {
                "request": request.model_dump(mode="json"),
                "result": result.response.payload,
                "evidence_refs": [
                    ref.model_dump(mode="json") for ref in result.response.evidence_refs
                ],
            }
        if request.tool == "request_reproduction":
            self._observed = result.response.payload.get("status") == "observed_failure"
            if not self._observed:
                raise Halt("inconclusive", "TOOL_FAILED")
            if self.mode == "three_role":
                self._role = "repair"
        elif request.tool == "apply_candidate":
            self._candidate, self._rejected = True, False
            if self.mode == "three_role":
                self._role = "verification"
        elif request.tool == "compare_candidate":
            self._candidate = False
            self._rejected = result.response.payload.get("status") == "rejected"
            if (
                not self._rejected
                or self.cursor.task.candidate_count >= self.cursor.task.budget.candidates
            ):
                raise Halt("inconclusive", "TOOL_FAILED")
            if self.mode == "three_role":
                self._role = "repair"
        return {"route": "continue"}

    def _validate_resources(self) -> None:
        spec = self.registry.task_spec()
        profile = validate_baseline_binding(self.baseline.ref)
        if (
            spec.intent != "propose_repair"
            or self.registry.case_name != profile.case_id
            or self.registry.manifest_sha256 != profile.manifest_sha256
        ):
            raise ResourceError("PERMISSION_DENIED")
        self.snapshots.verify(self.baseline)
        if self.baseline.ref.file_sha256 != self.registry.source_sha256():
            raise ResourceError("ARTIFACT_MISMATCH")

    async def run(self) -> RepairResult:
        if self._used:
            raise StoreError("repair_id_conflict")
        self._used = True
        self._validate_resources()
        spec = self.registry.task_spec()
        with RepairFence(self.store.directory, spec.task_id) as self.fence:
            task = RepairTask(
                task_id=spec.task_id,
                baseline=self.baseline.ref,
                mode=self.mode,
                started_at=datetime.now(UTC),
                budget=spec.budget,
                model_config_sha256=self.adapter.config_sha256,
            )
            self.cursor = self.store.create_task(task, fence=self.fence)
            self.deadline = self.clock() + task.budget.timeout_seconds
            return await self._loop()

    async def run_created(
        self,
        claim: RepairAdmissionClaim,
        saved: Mutation,
        fence: RepairFence,
    ) -> RepairResult:
        """B1b: accept only the original in-memory admission, never resume a task."""
        if self._used:
            raise StoreError("repair_id_conflict")
        self._used = True
        if not isinstance(self.store, WebRepairStore) or not isinstance(
            claim, RepairAdmissionClaim
        ):
            raise StoreError("admission_invalid")
        with (
            claim.owner.guard(self.store.directory, claim.nonce),
            fence.guard(self.store.directory, claim.task_id),
        ):
            self._validate_resources()
            spec = self.registry.task_spec()
            if (
                saved.task.task_id != spec.task_id
                or saved.task.mode != self.mode
                or saved.task.baseline != self.baseline.ref
                or saved.task.budget != spec.budget
                or saved.task.model_config_sha256 != self.adapter.config_sha256
            ):
                raise StoreError("version_mismatch")
            verified_image(self.image_root)
            if not verify_oracle():
                raise StoreError("version_mismatch")
            started = self.store.consume(
                claim,
                saved,
                case_id=self.registry.case_name,
                mode=self.mode,
                policy_sha256=policy_sha256(),
                task_fence=fence,
            )
            self.cursor, self.fence = started.saved, fence
            if not started.executable:
                return RepairResult(self.cursor, cancelled=started.cancel_requested)
            self._web_store = self.store
            now = self.store._now().timestamp()
            self._last_wall = now
            remaining = self.cursor.task.started_at.timestamp() + spec.budget.timeout_seconds - now
            self.deadline = self.clock() + remaining
            result, interrupted = await _owned(self._watched_loop(), cancel_operation=True)
            return replace(result, cancelled=result.cancelled or interrupted)

    async def _watched_loop(self) -> RepairResult:
        running = asyncio.current_task()
        store = self._web_store
        assert running is not None and store is not None

        async def watch() -> None:
            while True:
                await asyncio.sleep(0.1)
                try:
                    requested = store.receipt(self.cursor.task.task_id).cancel_requested
                except StoreError:
                    self._reconcile = True
                    requested = True
                if requested:
                    running.cancel()
                    return

        watcher = asyncio.create_task(watch())
        try:
            return await self._loop()
        finally:
            watcher.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(watcher, return_exceptions=True)

    async def _loop(self) -> RepairResult:
        try:
            self.bridge = RepairToolBridge(
                store=self.store,
                registry=self.registry,
                baseline=self.baseline,
                snapshots=self.snapshots,
                image_root=self.image_root,
                fence=self.fence,
                remaining_seconds=self.remaining,
            )
            graph = StateGraph(Route)
            graph.add_node("turn", RunnableLambda(self._turn))
            graph.add_edge(START, "turn")
            graph.add_conditional_edges(
                "turn", lambda state: state["route"], {"continue": "turn", "end": END}
            )
            with tracing_context(enabled=False):
                await graph.compile().ainvoke(
                    {"route": "continue"}, config={"recursion_limit": 30, "callbacks": []}
                )
        except asyncio.CancelledError:
            self._cancelled = True
            self._stop("cancelled", "CANCELLED")
        except Halt as halt:
            self._stop(halt.outcome, halt.code)
        except StoreError as error:
            if self.cursor.task.phase in PENDING:
                self._reconcile = True
            else:
                self._stop(
                    "budget_exceeded"
                    if error.code in ("budget_exceeded", "deadline_exceeded", "candidate_limit")
                    else "outcome_unknown",
                    "BUDGET_EXCEEDED"
                    if error.code in ("budget_exceeded", "deadline_exceeded", "candidate_limit")
                    else "PERSISTENCE_FAILED",
                )
        except (PolicyError, ResourceError, GatewayError, ValueError):
            self._stop("inconclusive", "INVALID_MODEL_OUTPUT")
        except Exception:
            self._stop("outcome_unknown", "INTERNAL_ERROR")
        finally:
            self._materials.clear()
            self._completed_reads.clear()
            self._last_tool_feedback = None
            self._format_feedback = None
        return RepairResult(self.cursor, self._reconcile, self._cancelled)
