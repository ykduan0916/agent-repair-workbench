"""P0-06 model protocol: equal output limits and versioned, role-owned contracts."""

from importlib.resources import files
from typing import Any, Protocol

import httpx

from agent_workbench.agents.diagnosis.repair_contracts import ADAPTER as DIAGNOSIS_ADAPTER
from agent_workbench.agents.repair.repair_contracts import ADAPTER as REPAIR_ADAPTER
from agent_workbench.agents.single.repair_contracts import ADAPTER as SINGLE_ADAPTER
from agent_workbench.agents.verification.repair_contracts import ADAPTER as VERIFICATION_ADAPTER
from agent_workbench.model_gateway.gateway import (
    Config,
    DeepSeekAdapter,
    GatewayError,
    PreparedCall,
    Reply,
    canonical,
    digest,
    prepare_decision_call,
)
from agent_workbench.model_gateway.rejections import FORMAT_CORRECTIONS, PROTOCOL_REVISION
from agent_workbench.repair_contracts.common import RepairRole

OUTPUT_LIMIT = 8192
ROLES: tuple[RepairRole, ...] = ("diagnosis", "repair", "verification", "single")


class RepairAdapter(Protocol):
    @property
    def config_sha256(self) -> str: ...

    def prompt(self, role: RepairRole) -> str: ...

    def prepare(
        self, role: RepairRole, messages: list[dict[str, str]], output_cap: int
    ) -> PreparedCall: ...

    async def send(self, request: PreparedCall, timeout: float) -> Reply: ...


class RepairDeepSeekAdapter:
    """One configuration covers both experiment modes; transport is shared with P0-05.

    Prompts and schemas are frozen at construction and fingerprinted together. The
    caller keeps messages ephemeral and must persist admission before calling send.
    This class does not execute tools or decide the repair outcome.
    """

    def __init__(self, config: Config, *, transport: httpx.AsyncBaseTransport | None = None):
        self._config = config
        self._sender = DeepSeekAdapter(config, transport=transport)
        self._schemas: dict[RepairRole, dict[str, Any]] = {
            "diagnosis": DIAGNOSIS_ADAPTER.json_schema(),
            "repair": REPAIR_ADAPTER.json_schema(),
            "verification": VERIFICATION_ADAPTER.json_schema(),
            "single": SINGLE_ADAPTER.json_schema(),
        }
        self._prompts: dict[RepairRole, str] = {}
        try:
            for role in ROLES:
                raw = (
                    files(f"agent_workbench.agents.{role}")
                    .joinpath("prompts/p006.txt")
                    .read_bytes()
                )
                if not raw or len(raw) > 8192:
                    raise ValueError("prompt_limit")
                self._prompts[role] = raw.decode("utf-8")
        except (OSError, UnicodeError, ValueError):
            raise GatewayError("MODEL_CONFIGURATION_INVALID") from None
        self._sha256 = digest(
            canonical(
                {
                    "transport_config": config.sha256,
                    "repair_protocol": PROTOCOL_REVISION,
                    "format_corrections": FORMAT_CORRECTIONS,
                    "role_output_limit": OUTPUT_LIMIT,
                    "schemas": self._schemas,
                    "prompts": {
                        role: digest(text.encode()) for role, text in self._prompts.items()
                    },
                }
            )
        )

    @property
    def config_sha256(self) -> str:
        return self._sha256

    def prompt(self, role: RepairRole) -> str:
        if role not in ROLES:
            raise GatewayError("MODEL_CONFIGURATION_INVALID")
        return self._prompts[role]

    def prepare(
        self, role: RepairRole, messages: list[dict[str, str]], output_cap: int
    ) -> PreparedCall:
        if role not in ROLES:
            raise GatewayError("MODEL_CONFIGURATION_INVALID")
        return prepare_decision_call(
            self._config,
            self.config_sha256,
            self._schemas[role],
            messages,
            output_cap,
            OUTPUT_LIMIT,
        )

    async def send(self, request: PreparedCall, timeout: float) -> Reply:
        return await self._sender.send(request, timeout)
