"""Strict metadata-only events and private host execution reservations."""

from dataclasses import dataclass
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from agent_workbench.orchestration.contracts import (
    AnalysisSummary,
    Contract,
    Digest,
    Identifier,
    Reason,
    References,
    Role,
    unique_evidence,
)
from agent_workbench.run_store.models import Claim, RunView

EventKind = Literal[
    "created",
    "phase_changed",
    "model_started",
    "model_settled",
    "model_unknown",
    "tool_started",
    "tool_completed",
    "role_concluded",
    "finished",
    "reconciled",
]
ToolName = Literal[
    "read_evidence", "read_contract", "read_source", "check_contract", "request_reproduction"
]
Assessment = Literal[
    "hypothesis",
    "inconclusive",
    "proposal_only",
    "cannot_propose",
    "needs_evidence",
    "proposal_reviewed",
]
ErrorCode = Literal[
    "INVALID_INPUT",
    "PERMISSION_DENIED",
    "STALE_VERSION",
    "BUDGET_EXCEEDED",
    "RESOURCE_LIMIT",
    "TOOL_FAILURE",
    "RESULT_UNKNOWN",
    "ARTIFACT_MISMATCH",
    "INPUT_BOUND_UNVERIFIED",
    "MODEL_UNAVAILABLE",
    "MODEL_TIMEOUT",
    "MODEL_CANCELLED",
    "INVALID_MODEL_OUTPUT",
    "USAGE_UNKNOWN",
    "USAGE_INVALID",
    "OUTPUT_LIMIT",
    "CANCELLED",
    "PERSISTENCE_FAILED",
    "INTERRUPTED",
    "MODEL_CONFIGURATION_INVALID",
    "MODEL_NOT_CONFIGURED",
    "MODEL_TRANSPORT_ERROR",
    "MODEL_RESPONSE_INVALID",
    "MODEL_OUTPUT_TRUNCATED",
    "MODEL_USAGE_UNKNOWN",
    "MODEL_USAGE_BOUND_VIOLATION",
    "MODEL_DECISION_INVALID",
    "MODEL_DECISION_JSON_INVALID",
    "MODEL_ROLE_SCHEMA_INVALID",
    "MODEL_DECISION_LIMIT",
    "MODEL_EVIDENCE_INVALID",
    "MODEL_HANDOFF_INVALID",
    "MODEL_EVIDENCE_REQUIRED",
    "DEADLINE_EXCEEDED",
    "TOOL_FAILED",
    "TOOL_OUTCOME_UNKNOWN",
    "INTERNAL_ERROR",
]
TokenCount = Annotated[int, Field(ge=0, le=2_000_000)]


class AnalysisEvent(Contract):
    kind: EventKind
    role: Role | None = None
    assessment: Assessment | None = None
    reason_code: Reason | None = None
    tool: ToolName | None = None
    evidence_refs: References = ()
    call_id: Identifier | None = None
    request_sha256: Digest | None = None
    input_reserved: TokenCount | None = None
    output_reserved: Annotated[int, Field(ge=1, le=32768)] | None = None
    reservation_sha256: Digest | None = None
    known_input_tokens: TokenCount | None = None
    known_output_tokens: TokenCount | None = None
    error_code: ErrorCode | None = None

    @model_validator(mode="after")
    def shape(self) -> Self:
        unique_evidence(self.evidence_refs)
        model = self.kind.startswith("model_")
        if model:
            if any(
                value is None
                for value in (
                    self.role,
                    self.call_id,
                    self.request_sha256,
                    self.input_reserved,
                    self.output_reserved,
                    self.reservation_sha256,
                )
            ):
                raise ValueError("model_reservation_required")
        elif any(
            value is not None
            for value in (
                self.input_reserved,
                self.output_reserved,
                self.reservation_sha256,
                self.known_input_tokens,
                self.known_output_tokens,
            )
        ):
            raise ValueError("unexpected_accounting")
        if self.kind == "model_settled":
            if self.known_input_tokens is None or self.known_output_tokens is None:
                raise ValueError("model_usage_required")
        elif self.known_input_tokens is not None or self.known_output_tokens is not None:
            raise ValueError("unexpected_usage")
        if self.kind.startswith("tool_"):
            if (
                self.role is None
                or self.tool is None
                or self.call_id is None
                or self.request_sha256 is None
            ):
                raise ValueError("tool_identity_required")
        elif self.tool is not None:
            raise ValueError("unexpected_tool")
        if self.kind == "role_concluded":
            assessments = {
                "diagnosis": ("hypothesis", "inconclusive"),
                "repair": ("proposal_only", "cannot_propose"),
                "verification": ("needs_evidence", "proposal_reviewed"),
            }
            if self.role is None or self.assessment not in assessments[self.role]:
                raise ValueError("assessment_role_mismatch")
            if (
                self.assessment in ("hypothesis", "proposal_only", "proposal_reviewed")
                and not self.evidence_refs
            ):
                raise ValueError("evidence_required")
        elif self.assessment is not None or self.reason_code is not None:
            raise ValueError("unexpected_assessment")
        if self.kind not in (
            "model_started",
            "model_settled",
            "model_unknown",
            "tool_started",
            "tool_completed",
        ):
            if self.call_id is not None or self.request_sha256 is not None:
                raise ValueError("unexpected_call")
        return self


class EventView(Contract):
    revision: Annotated[int, Field(ge=0, le=255)]
    created_at: Annotated[float, Field(ge=0)]
    sha256: Digest
    event: AnalysisEvent


@dataclass(frozen=True)
class SaveResult:
    summary: AnalysisSummary
    sha256: str
    reused: bool


@dataclass(frozen=True)
class ExecutionReservation:
    created: bool
    run: RunView
    claim: Claim | None
    summary: AnalysisSummary
    summary_sha256: str
