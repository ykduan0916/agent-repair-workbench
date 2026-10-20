"""Strict request DTOs and process-local capabilities; no tokens in public receipts."""

from dataclasses import dataclass, field
from typing import Literal, Never, SupportsIndex

from pydantic import model_validator

from agent_workbench.experience_memory.agent_models import MemoryBinding
from agent_workbench.mcp_config import canonical, digest
from agent_workbench.orchestration.contracts import Budget, Contract, Identifier
from agent_workbench.repair_cases.profiles import RepairCaseId
from agent_workbench.repair_store.models import Mutation
from agent_workbench.web_repairs.fencing import RepairWebFence

RepairMode = Literal["single_role", "three_role"]
WEB_BUDGET = Budget(total_tokens=2_000_000, output_tokens=8192)


class RepairRequest(Contract):
    request_id: Identifier
    case_id: RepairCaseId
    mode: RepairMode

    memory: MemoryBinding | None = None

    @model_validator(mode="after")
    def memory_mode(self) -> "RepairRequest":
        if self.memory is not None and self.mode == "single_role":
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


class RepairAdmissionClaim:
    """Internal immutable capability; standard JSON/dataclass encoders reject it."""

    __slots__ = ("task_id", "initial_sha256", "token", "owner", "nonce")
    task_id: str
    initial_sha256: str
    token: str
    owner: RepairWebFence
    nonce: str

    def __init__(
        self, task_id: str, initial_sha256: str, token: str, owner: RepairWebFence, nonce: str
    ) -> None:
        for key, value in (
            ("task_id", task_id),
            ("initial_sha256", initial_sha256),
            ("token", token),
            ("owner", owner),
            ("nonce", nonce),
        ):
            object.__setattr__(self, key, value)

    def __setattr__(self, name: str, value: object) -> Never:
        raise TypeError("private_capability")

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        raise TypeError("private_capability")


@dataclass(frozen=True)
class RepairAdmission:
    saved: Mutation
    cancel_requested: bool
    claim: RepairAdmissionClaim | None = field(default=None, repr=False)


@dataclass(frozen=True)
class RepairReceipt:
    request: RepairRequest
    saved: Mutation
    cancel_requested: bool
    active: bool
    consumed: bool


@dataclass(frozen=True)
class RepairStart:
    saved: Mutation
    cancel_requested: bool
    # False for a cancelled/expired admission. Never authorize a terminal task.
    executable: bool
