"""P0-05b bounded feedback graph. Only routing flags enter LangGraph state."""

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypedDict, cast
from uuid import uuid4

from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from agent_workbench.agents.diagnosis.agent import build as diagnosis
from agent_workbench.agents.diagnosis.contracts import DiagnosisConclusion
from agent_workbench.agents.repair.agent import build as repair
from agent_workbench.agents.repair.contracts import RepairConclusion
from agent_workbench.agents.verification.agent import build as verification
from agent_workbench.analysis_store.fencing import AnalysisFence
from agent_workbench.analysis_store.models import AnalysisEvent, ErrorCode, SaveResult
from agent_workbench.analysis_store.store import AnalysisStore
from agent_workbench.controlled_tools.contracts import ToolDecision, target_key
from agent_workbench.controlled_tools.resources import TaskResourceRegistry
from agent_workbench.controlled_tools.runtime import ExecutionContext, ToolRuntime
from agent_workbench.model_gateway.gateway import (
    Adapter,
    GatewayError,
    PreparedCall,
    canonical,
    digest,
)
from agent_workbench.orchestration.budget import reserve, settle
from agent_workbench.orchestration.contracts import (
    AnalysisSummary,
    Outcome,
    Phase,
    Role,
    TaskState,
    Usage,
)
from agent_workbench.orchestration.policy import PolicyError, authorize, transition
from agent_workbench.run_store.models import StoreError
from agent_workbench.web_analyses.models import AnalysisClaim
from agent_workbench.web_analyses.store import WebAnalysisStore


class Route(TypedDict):
    route: str


class Stop(Exception):
    def __init__(self, outcome: Outcome, code: ErrorCode | None = None):
        self.outcome, self.code = outcome, code


class AnalysisRuntime:
    def __init__(
        self,
        registry: TaskResourceRegistry,
        store: AnalysisStore,
        adapter: Adapter | None,
        *,
        image_root: Path | None = None,
    ):
        self.registry, self.store, self.adapter = registry, store, adapter
        self.tools = ToolRuntime(registry, store=store, image_root=image_root)
        self.agents = {"diagnosis": diagnosis(), "repair": repair(), "verification": verification()}
        self.messages: dict[Role, list[dict[str, str]]] = {}
        self.proposal: RepairConclusion | None = None
        self.corrected = False
        self.seen_tools: set[str] = set()
        self.saved: SaveResult
        self.fence: AnalysisFence
        self.deadline = time.monotonic() + registry.task_spec().budget.timeout_seconds

    @property
    def summary(self) -> AnalysisSummary:
        return self.saved.summary

    def updated(self, **changes: object) -> AnalysisSummary:
        value = self.summary.model_dump(mode="python")
        value.update(changes, summary_revision=self.summary.summary_revision + 1)
        return AnalysisSummary.model_validate(value)

    def save(self, event: AnalysisEvent, **changes: object) -> None:
        current = self.saved
        self.saved = self.store.save(
            self.updated(**changes),
            event,
            expected_revision=current.summary.summary_revision,
            expected_sha256=current.sha256,
            fence=self.fence,
        )
        if self.saved.reused:
            # A replayed ledger entry never authorizes a second external action.
            raise Stop("outcome_unknown", "RESULT_UNKNOWN")

    def phase(self, phase: Phase) -> None:
        self.save(
            AnalysisEvent(kind="phase_changed"),
            state=transition(
                self.summary.state, phase, expected_revision=self.summary.state.revision
            ),
        )

    def finish(self, outcome: Outcome, code: ErrorCode | None = None) -> None:
        self.save(
            AnalysisEvent(kind="finished", error_code=code),
            state=transition(
                self.summary.state,
                "finished",
                expected_revision=self.summary.state.revision,
                outcome=outcome,
            ),
            finished_at=datetime.now(UTC),
        )

    def remaining(self) -> float:
        # Reserve cleanup/final-save time before launching any next action.
        value = self.deadline - time.monotonic() - 15
        if value <= 0:
            raise Stop("budget_exceeded", "DEADLINE_EXCEEDED")
        return value

    def context(self, role: Role) -> object:
        grants = self.registry.authorization()
        return {
            "task": self.registry.task_spec().model_dump(mode="json"),
            "initial_report": self.registry.initial_report().model_dump(mode="json"),
            "evidence_records": [
                {
                    "reference": ref.model_dump(mode="json"),
                    **self.registry.evidence(ref, "summary"),
                    **self.registry.evidence(ref, "findings"),
                }
                for ref in self.registry.task_spec().evidence
            ],
            "available_requests": [
                item.model_dump(mode="json") for item in self.registry.request_templates(role)
            ],
            "editable_files": [item.model_dump(mode="json") for item in grants.editable_files],
            "candidate_metadata": None
            if self.proposal is None
            else {
                "disposition": self.proposal.disposition,
                "evidence_refs": [
                    item.model_dump(mode="json") for item in self.proposal.evidence_refs
                ],
                "edits": [
                    {"path": item.path, "base_sha256": item.base_sha256}
                    for item in self.proposal.edits
                ],
                "executed": False,
            },
        }

    @staticmethod
    def model_event(
        kind: Literal["model_started", "model_settled", "model_unknown"],
        role: Role,
        call_id: str,
        request: PreparedCall,
        **extra: object,
    ) -> AnalysisEvent:
        return AnalysisEvent.model_validate(
            {
                "kind": kind,
                "role": role,
                "call_id": call_id,
                "request_sha256": request.request_sha256,
                "input_reserved": request.input_reserved,
                "output_reserved": request.output_reserved,
                "reservation_sha256": request.reservation_sha256,
                **extra,
            }
        )

    async def turn(self, role: Role) -> Route:
        if isinstance(self.store, WebAnalysisStore) and self.store.cancellation_requested(
            self.summary.task_id
        ):
            raise Stop("cancelled", "CANCELLED")
        if self.adapter is None:
            raise Stop("failed", "MODEL_NOT_CONFIGURED")
        self.remaining()
        messages = self.messages.setdefault(role, self.agents[role].messages(self.context(role)))
        # Context contains only registry resources; the fresh role never inherits previous chats.
        request = self.adapter.prepare(role, messages, self.summary.budget.output_tokens)
        usage = reserve(self.summary, request)
        call_id = str(uuid4())
        self.save(
            self.model_event("model_started", role, call_id, request),
            usage=usage,
            model_config_sha256=self.adapter.config_sha256,
        )
        try:
            async with asyncio.timeout(self.remaining()):
                reply = await self.adapter.send(request, min(120.0, self.remaining()))
        except (TimeoutError, asyncio.CancelledError) as exc:
            self.save(
                self.model_event(
                    "model_unknown", role, call_id, request, error_code="MODEL_TIMEOUT"
                )
            )
            if isinstance(exc, asyncio.CancelledError):
                raise Stop("cancelled", "CANCELLED") from None
            raise Stop("outcome_unknown", "MODEL_TIMEOUT") from None
        if reply.usage_known:
            self.save(
                self.model_event(
                    "model_settled",
                    role,
                    call_id,
                    request,
                    known_input_tokens=reply.input_tokens,
                    known_output_tokens=reply.output_tokens,
                ),
                usage=settle(self.summary.usage, request, reply),
            )
        else:
            self.save(
                self.model_event(
                    "model_unknown",
                    role,
                    call_id,
                    request,
                    error_code=reply.error or "MODEL_USAGE_UNKNOWN",
                )
            )
            raise Stop("outcome_unknown", cast(ErrorCode, reply.error or "MODEL_USAGE_UNKNOWN"))
        if reply.error and reply.error not in (
            "MODEL_DECISION_INVALID",
            "MODEL_DECISION_JSON_INVALID",
        ):
            raise Stop("inconclusive", cast(ErrorCode, reply.error))
        try:
            decision = self.agents[role].decode(reply.decision or b"")
        except PolicyError:
            if self.corrected:
                raise Stop("inconclusive", "MODEL_DECISION_INVALID") from None
            self.corrected = True
            messages.append(
                {
                    "role": "user",
                    "content": "HOST: INVALID_DECISION. Return one valid emit_decision "
                    "using the supplied schema.",
                }
            )
            return {"route": role}
        authorize(self.registry.task_spec(), role, decision, self.registry.authorization())
        if isinstance(decision, ToolDecision):
            tool_request = decision.request
            if tool_request.request_id in self.seen_tools:
                raise Stop("inconclusive", "INVALID_MODEL_OUTPUT")
            if self.summary.usage.tool_calls >= self.summary.budget.tool_calls:
                raise Stop("budget_exceeded", "BUDGET_EXCEEDED")
            self.seen_tools.add(tool_request.request_id)
            tool_usage = Usage.model_validate(
                {**self.summary.usage.model_dump(), "tool_calls": self.summary.usage.tool_calls + 1}
            )
            event = AnalysisEvent(
                kind="tool_started",
                role=role,
                tool=tool_request.tool,
                call_id=tool_request.request_id,
                request_sha256=target_key(tool_request),
                evidence_refs=self.registry.task_spec().evidence,
            )
            reservation = None
            if tool_request.tool == "request_reproduction":
                reservation = ExecutionContext(
                    summary=self.updated(usage=tool_usage),
                    expected_revision=self.summary.summary_revision,
                    expected_sha256=self.saved.sha256,
                    fence=self.fence,
                    event=event,
                )
            else:
                self.save(event, usage=tool_usage)
            result = await self.tools.execute(
                role,
                tool_request,
                timeout_seconds=min(30.0, self.remaining()),
                reservation=reservation,
            )
            if result.summary is not None:
                assert result.summary_sha256 is not None
                self.saved = SaveResult(result.summary, result.summary_sha256, False)
            if tool_request.tool == "request_reproduction" and result.summary is None:
                raise Stop("inconclusive", "TOOL_FAILED")
            self.save(
                AnalysisEvent(
                    kind="tool_completed",
                    role=role,
                    tool=tool_request.tool,
                    call_id=tool_request.request_id,
                    request_sha256=target_key(tool_request),
                    evidence_refs=result.response.evidence_refs,
                ),
                evidence_refs=self.registry.task_spec().evidence,
            )
            if result.cancelled:
                raise Stop("cancelled", "CANCELLED")
            if result.response.status != "ok":
                raise Stop(
                    "outcome_unknown"
                    if result.response.status == "outcome_unknown"
                    else "inconclusive",
                    "TOOL_OUTCOME_UNKNOWN"
                    if result.response.status == "outcome_unknown"
                    else "TOOL_FAILED",
                )
            messages.extend(
                [
                    {"role": "assistant", "content": decision.model_dump_json()},
                    {
                        "role": "user",
                        "content": canonical(
                            {
                                "tool_result": result.response.model_dump(mode="json"),
                                "registered_evidence": [
                                    item.model_dump(mode="json")
                                    for item in self.registry.task_spec().evidence
                                ],
                                "available_requests": [
                                    item.model_dump(mode="json")
                                    for item in self.registry.request_templates(role)
                                ],
                            }
                        ).decode("ascii"),
                    },
                ]
            )
            return {"route": role}
        if isinstance(decision, RepairConclusion):
            if (
                decision.disposition == "proposal_only"
                and self.summary.proposal_count >= self.summary.budget.candidates
            ):
                raise Stop("budget_exceeded", "BUDGET_EXCEEDED")
            self.proposal = decision
            self.save(
                AnalysisEvent(
                    kind="role_concluded",
                    role=role,
                    assessment=decision.disposition,
                    evidence_refs=decision.evidence_refs,
                ),
                proposal_count=self.summary.proposal_count
                + int(decision.disposition == "proposal_only"),
            )
            if decision.disposition == "cannot_propose":
                raise Stop("inconclusive")
            self.messages.pop("verification", None)
            self.phase("reviewing")
            return {"route": "verification"}
        self.save(
            AnalysisEvent(
                kind="role_concluded",
                role=role,
                assessment=decision.assessment,
                reason_code=decision.reason_code,
                evidence_refs=decision.evidence_refs,
            )
        )
        if isinstance(decision, DiagnosisConclusion):
            if decision.assessment == "inconclusive":
                raise Stop("inconclusive")
            next_role: Role = (
                "repair" if self.summary.state.intent == "propose_repair" else "verification"
            )
            self.phase("proposing" if next_role == "repair" else "reviewing")
            return {"route": next_role}
        if decision.assessment == "needs_evidence":
            if (
                self.summary.state.intent == "propose_repair"
                and self.summary.proposal_count < self.summary.budget.candidates
            ):
                self.messages.pop("repair", None)
                self.phase("proposing")
                return {"route": "repair"}
            if self.summary.state.intent == "propose_repair":
                raise Stop("budget_exceeded", "BUDGET_EXCEEDED")
            raise Stop("inconclusive")
        raise Stop(
            "proposal_only"
            if self.summary.state.intent == "propose_repair"
            else "diagnosis_complete"
        )

    def initial_summary(self) -> AnalysisSummary:
        """Host-only initial metadata; no model or tool side effect."""
        task = self.registry.task_spec()
        return AnalysisSummary(
            task_id=task.task_id,
            source_kind=task.source_kind,
            state=TaskState(task_id=task.task_id, intent=task.intent),
            started_at=datetime.now(UTC),
            budget=task.budget,
            usage=Usage(),
            evidence_refs=task.evidence,
        )

    async def run(self) -> SaveResult:
        task = self.registry.task_spec()
        with AnalysisFence(self.store.directory, task.task_id) as self.fence:
            self.saved = self.store.create(
                self.initial_summary(),
                AnalysisEvent(kind="created"),
                fence=self.fence,
            )
            if self.saved.reused:
                raise StoreError("analysis_conflict")
            self.phase("checking")
            return await self._run_started()

    async def run_created(
        self,
        claim: AnalysisClaim,
        saved: SaveResult,
        *,
        fence: AnalysisFence,
    ) -> SaveResult:
        """Browser owner entry; a read/replay result alone never authorizes execution."""
        if not isinstance(self.store, WebAnalysisStore) or not isinstance(claim, AnalysisClaim):
            raise StoreError("admission_invalid")
        task = self.registry.task_spec()
        summary = saved.summary
        if (
            digest(canonical(summary.model_dump(mode="json"))) != saved.sha256
            or summary.task_id != task.task_id
            or summary.state.intent != task.intent
            or summary.source_kind != task.source_kind
            or summary.budget != task.budget
            or summary.evidence_refs != task.evidence
        ):
            raise StoreError("admission_invalid")
        # This persistent, one-use transition is outside the execution exception handler:
        # denied/replayed startup must not alter someone else's live or terminal task.
        case_name = self.registry.case_name
        if case_name not in ("output_mismatch", "passing"):
            # Repair-only cases must not widen the separately admitted web-analysis catalog.
            raise StoreError("admission_invalid")
        started = self.store.start_created(claim, saved, fence=fence, case_name=case_name)
        self.fence, self.saved = fence, started.saved
        if started.cancel_requested:
            self.finish("cancelled", "CANCELLED")
            return self.saved
        return await self._run_started()

    async def _run_started(self) -> SaveResult:
        try:
            if self.registry.task_spec().intent == "check_only":
                self.finish("check_complete")
                return self.saved
            self.phase("diagnosing")
            graph = StateGraph(Route)

            async def diagnose_node(_: Route) -> Route:
                return await self.turn("diagnosis")

            async def repair_node(_: Route) -> Route:
                return await self.turn("repair")

            async def verify_node(_: Route) -> Route:
                return await self.turn("verification")

            for name, node in (
                ("diagnosis", diagnose_node),
                ("repair", repair_node),
                ("verification", verify_node),
            ):
                graph.add_node(name, RunnableLambda(node))
                graph.add_conditional_edges(
                    name,
                    lambda state: state["route"],
                    {
                        "diagnosis": "diagnosis",
                        "repair": "repair",
                        "verification": "verification",
                        "end": END,
                    },
                )
            graph.add_edge(START, "diagnosis")
            with tracing_context(enabled=False):
                await graph.compile().ainvoke(
                    {"route": "diagnosis"}, config={"recursion_limit": 100, "callbacks": []}
                )
        except Stop as stop:
            self.finish(stop.outcome, stop.code)
        except (PolicyError, GatewayError) as exc:
            code = str(exc)
            safe: ErrorCode = (
                "BUDGET_EXCEEDED" if code == "BUDGET_EXCEEDED" else "PERMISSION_DENIED"
            )
            self.finish("budget_exceeded" if code == "BUDGET_EXCEEDED" else "inconclusive", safe)
        except asyncio.CancelledError:
            self.finish("cancelled", "CANCELLED")
        except StoreError:
            # Do not overwrite a durable send/claim or pretend a failed save succeeded.
            raise
        except Exception:
            self.finish("outcome_unknown", "INTERNAL_ERROR")
        finally:
            self.messages.clear()
            self.proposal = None
        return self.saved
