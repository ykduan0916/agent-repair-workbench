"""M2-R trusted-host contracts; references never assert current verification."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from .models import Contract, Digest, Key, Kind


class Grant(Contract):
    connection_id: Key
    content_type: Literal["query", "episode", "repair_experience", "project_constraint"]
    purpose: Literal["index", "retrieve"]
    enabled: bool
    expires_at: Annotated[float, Field(ge=0, allow_inf_nan=False)]


class Policy(Contract):
    expires_at: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    allow_historical: bool = False
    conflict_key: Key | None = None
    conflict_value: Key | None = None

    @model_validator(mode="after")
    def paired(self) -> "Policy":
        if (self.conflict_key is None) != (self.conflict_value is None):
            raise ValueError("incomplete_conflict_policy")
        return self


class Query(Contract):
    connection_id: Key
    event_id: Key
    tool_id: Key
    source_version: Key
    text: Annotated[str, Field(min_length=1, max_length=1024)]
    allow_historical: bool = False


class Reference(Contract):
    memory_id: Key
    revision: int
    generation: int
    digest: Digest
    kind: Kind
    connection_id: Key
    event_id: Key
    source_version: Key
    classification: Literal["exact", "historical_hint"]
    body: str = Field(repr=False)


class Retrieval(Contract):
    status: Literal["available", "limited", "unavailable"]
    reason: str
    job_id: Key | None = None
    references: tuple[Reference, ...] = ()
    current_verification: Literal[False] = False
