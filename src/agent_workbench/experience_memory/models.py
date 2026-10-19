"""M1-R trusted-host commands. No external authentication or model calls here."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Key = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,96}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Version = Annotated[int, Field(ge=1, le=1_000_000)]
Kind = Literal["episode", "repair_experience", "project_constraint"]
State = Literal["draft", "confirmed", "revoked", "deleted"]
Origin = Literal["external_report", "local_user", "synthetic"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class EventInput(Contract):
    schema_version: Literal[1] = 1
    event_id: Key
    event_type: Literal["test_completed", "contract_changed", "user_correction"]
    source_run_id: Key
    sequence: Annotated[int | None, Field(ge=0, le=1_000_000)] = None
    occurred_at: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    outcome: Literal["passed", "failed", "unknown"]
    artifact_digests: Annotated[tuple[Digest, ...], Field(max_length=16)] = ()

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_schema_type(cls, value: object) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("invalid_schema_version")
        return value


class DraftInput(Contract):
    kind: Kind
    connection_id: Key
    event_id: Key
    tool_id: Key
    source_version: Key
    title: Annotated[str, Field(min_length=1, max_length=160)]
    body: Annotated[str, Field(min_length=1, max_length=16384)]

    @field_validator("body")
    @classmethod
    def bounded_body(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 16384:
            raise ValueError("body_limit")
        return value


class Receipt(Contract):
    resource_id: Key
    version: Version
    revision: Version
    state: State


class MemoryView(Contract):
    receipt: Receipt
    kind: Kind
    digest: Digest
    content: DraftInput | None


class Lease(Contract):
    job_id: Key
    token: Key
    attempt: Annotated[int, Field(ge=1, le=3)]
    kind: Literal["event", "project", "cleanup"]
    memory_id: Key | None
    generation: Annotated[int, Field(ge=0)]
    expires_at: float


class CallReceipt(Contract):
    admitted: bool = False
    call_id: Key
    state: Literal["reserved", "received", "unknown"]
    reserved_tokens: Annotated[int, Field(ge=1, le=65536)]
    actual_tokens: Annotated[int | None, Field(ge=0)]
