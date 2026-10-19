"""Host-owned candidate review decisions; decisions never write to a project."""

from datetime import UTC
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from agent_workbench.orchestration.contracts import Contract, Digest, Identifier, Version

Decision = Literal["accepted", "rejected"]


class DecisionEvent(Contract):
    candidate_id: Identifier
    decision: Decision
    revision: Annotated[int, Field(ge=1, lt=256)]
    request_id: Identifier
    created_at: AwareDatetime

    @model_validator(mode="after")
    def utc(self) -> Self:
        if self.created_at.utcoffset() != UTC.utcoffset(self.created_at):
            raise ValueError("utc_required")
        return self


class CandidateDecision(Contract):
    schema_version: Version = 1
    decision_id: Identifier
    task_id: Identifier
    candidate_id: Identifier
    request_id: Identifier
    decision: Decision
    revision: Annotated[int, Field(ge=1, lt=256)]
    candidate_sha256: Digest
    comparison_id: Identifier
    comparison_sha256: Digest
    created_at: AwareDatetime
    # The ledger is the complete history. The receipt embeds a bounded tail so
    # its payload stays small while still showing recent decisions in the UI.
    history: Annotated[tuple[DecisionEvent, ...], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (
            self.history[-1].revision != self.revision
            or self.history[-1].request_id != self.request_id
        ):
            raise ValueError("decision_history_mismatch")
        first = max(1, self.revision - len(self.history) + 1)
        if tuple(item.revision for item in self.history) != tuple(range(first, self.revision + 1)):
            raise ValueError("decision_revision_gap")
        return self


class DecisionReceipt(Contract):
    schema_version: Version = 1
    task_id: Identifier
    candidate_id: Identifier
    decision: CandidateDecision | None
    candidate_sha256: Digest | None
    comparison_sha256: Digest | None
    # Task-wide CAS revision. CandidateDecision.revision remains candidate-local
    # for backwards-compatible history display; this is the value clients must
    # send back in expected_revision.
    revision: Annotated[int, Field(ge=0, lt=256)]
    current_valid: bool
    can_accept: bool
    can_reject: bool
    can_download: bool


class DecisionRequest(Contract):
    request_id: Identifier
    decision: Decision
    expected_revision: Annotated[int, Field(ge=0, lt=256)]
    expected_candidate_sha256: Digest
    expected_comparison_sha256: Digest
