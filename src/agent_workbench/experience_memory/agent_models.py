"""M3-R host-only binding and ephemeral context; never model-owned permissions."""

from dataclasses import dataclass, field
from typing import Any, Literal

from agent_workbench.orchestration.contracts import Budget

from .models import Contract, Key

TaskKind = Literal["analysis", "repair"]
RETRIEVAL_TOKENS = 100_000
CONTEXT_DENIALS = frozenset(
    {
        "authorization_required",
        "memory_ineligible",
        "memory_context_changed",
        "connection_paused",
        "scope_conflict",
        "scope_limit",
        "index_required",
        "embedding_profile_changed",
        "policy_required",
        "policy_expired",
    }
)


class MemoryBinding(Contract):
    project_id: Key
    connection_id: Key
    allow_historical: bool = False


def agent_budget(binding: MemoryBinding | None, base: Budget) -> Budget:
    if binding is None:
        return base
    if base.total_tokens <= RETRIEVAL_TOKENS:
        raise ValueError("memory_budget_required")
    return base.model_copy(update={"total_tokens": base.total_tokens - RETRIEVAL_TOKENS})


@dataclass(frozen=True)
class ContextSelection:
    task_kind: TaskKind
    task_id: str
    snapshots: tuple[dict[str, Any], ...] = field(repr=False)
    references: tuple[dict[str, Any], ...] = field(repr=False)
