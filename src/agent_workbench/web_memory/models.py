"""Strict management payloads; credentials never authorize these operations."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from agent_workbench.experience_memory.engine_models import Grant, Policy
from agent_workbench.experience_memory.models import Contract, DraftInput, Key


class ProjectInput(Contract):
    project_id: Key


class ConnectionInput(Contract):
    connection_id: Key
    origin: Literal["local_user", "external_report"]


class ConnectionChange(Contract):
    expected_version: Annotated[int, Field(ge=1, le=1_000_000)]
    enabled: bool


class CredentialChange(Contract):
    expected_version: Annotated[int, Field(ge=0, le=1_000_000)]
    action: Literal["rotate", "revoke"]


class CreateMemory(Contract):
    request_id: Key
    content: DraftInput


class ChangeMemory(Contract):
    request_id: Key
    expected_version: Annotated[int, Field(ge=1, le=1_000_000)]
    state: Literal["confirmed", "revoked", "deleted"] | None = None
    content: DraftInput | None = None

    @model_validator(mode="after")
    def choice(self) -> "ChangeMemory":
        if (self.state is None) == (self.content is None):
            raise ValueError("invalid_transition")
        return self


class GrantInput(Contract):
    expected_version: Annotated[int, Field(ge=0, le=1_000_000)]
    grant: Grant


class PolicyInput(Contract):
    expected_version: Annotated[int, Field(ge=0, le=1_000_000)]
    expected_generation: Annotated[int, Field(ge=1, le=1_000_000)]
    policy: Policy
