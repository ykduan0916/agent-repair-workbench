"""M3-R wiring for existing host-configured memory providers; no browser secrets."""

from pathlib import Path

from agent_workbench.controlled_tools.resources import TaskResourceRegistry
from agent_workbench.experience_memory.agent_models import MemoryBinding, TaskKind
from agent_workbench.experience_memory.agent_runtime import MemorySession
from agent_workbench.experience_memory.agent_store import AgentMemoryStore
from agent_workbench.providers.cognee.runtime import CogneeRuntime
from agent_workbench.run_store import StoreError

from .engine import providers_from_host


def make_session(
    directory: Path,
    binding: MemoryBinding | None,
    registry: TaskResourceRegistry,
    kind: TaskKind,
) -> MemorySession | None:
    if binding is None:
        return None

    def factory(store: AgentMemoryStore) -> CogneeRuntime:
        root = Path(__file__).resolve().parents[3]
        try:
            providers = providers_from_host()
        except (ValueError, OSError, StoreError):
            raise StoreError("provider_configuration_required") from None
        return CogneeRuntime(
            store,
            directory.resolve() / "memory-engine",
            root / "artifacts/mr01/venv/bin/python",
            providers,
        )

    return MemorySession(directory, binding, registry, kind, runtime_factory=factory)
