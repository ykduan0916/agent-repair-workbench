"""Ephemeral role conclusions cannot certify a repair or choose the next role."""

from typing import Literal, Self

from pydantic import model_validator
from pydantic_core import PydanticCustomError

from agent_workbench.orchestration.contracts import (
    Contract,
    References,
    Version,
    unique_evidence,
)

RepairRole = Literal["diagnosis", "repair", "verification", "single"]
RepairReason = Literal[
    "evidence_missing",
    "contract_mismatch",
    "serialization_mismatch",
    "unsupported",
    "candidate_requires_comparison",
    "comparison_requires_review",
]


class RoleConclusion(Contract):
    schema_version: Version = 1
    action: Literal["conclude"]
    disposition: Literal["handoff", "needs_evidence"]
    reason_code: RepairReason
    evidence_refs: References

    @model_validator(mode="after")
    def supporting_evidence(self) -> Self:
        unique_evidence(self.evidence_refs)
        if self.disposition == "handoff" and not self.evidence_refs:
            raise PydanticCustomError("handoff_evidence_required", "handoff_requires_evidence")
        return self
