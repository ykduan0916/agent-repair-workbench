"""Fixed browser request and private, once-only admission capabilities."""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import model_validator

from agent_workbench.analysis_store.models import SaveResult
from agent_workbench.experience_memory.agent_models import MemoryBinding
from agent_workbench.mcp_config import canonical, digest
from agent_workbench.orchestration.contracts import Budget, Contract, Identifier, Intent
from agent_workbench.web_analyses.fencing import AnalysisWebFence

CaseName = Literal["output_mismatch", "passing"]
WEB_BUDGET = Budget(total_tokens=2_000_000, output_tokens=8192)


class AnalysisRequest(Contract):
    request_id: Identifier
    case_name: CaseName
    intent: Intent

    memory: MemoryBinding | None = None

    @model_validator(mode="after")
    def memory_mode(self) -> "AnalysisRequest":
        if self.memory is not None and self.intent == "check_only":
            raise ValueError("memory_mode_unsupported")
        return self

    def fingerprint(self) -> str:
        return digest(
            canonical(
                {
                    "version": 2 if self.memory else 1,
                    **self.model_dump(mode="json", exclude_none=True),
                }
            )
        )


@dataclass(frozen=True)
class AnalysisAdmission:
    saved: SaveResult
    cancel_requested: bool
    claim: "AnalysisClaim | None" = field(default=None, repr=False)


@dataclass(frozen=True)
class AnalysisClaim:
    """Never serialized: only the original live owner receives the token."""

    task_id: str
    initial_sha256: str
    token: str = field(repr=False)
    owner: AnalysisWebFence = field(repr=False)


@dataclass(frozen=True)
class AnalysisStart:
    saved: SaveResult
    cancel_requested: bool
