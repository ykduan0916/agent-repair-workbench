"""Bounded JSON decoder. Public errors never include input or validation details."""

import json
import math
from typing import TypeVar

from pydantic import TypeAdapter, ValidationError

from agent_workbench.agents.diagnosis.contracts import DiagnosisOutput
from agent_workbench.agents.repair.contracts import RepairOutput
from agent_workbench.agents.verification.contracts import VerificationOutput
from agent_workbench.orchestration.contracts import Role
from agent_workbench.orchestration.policy import PolicyError, RoleOutput

MAX_MESSAGE_BYTES = 65536

DIAGNOSIS_ADAPTER = TypeAdapter[DiagnosisOutput](DiagnosisOutput)
REPAIR_ADAPTER = TypeAdapter[RepairOutput](RepairOutput)
VERIFICATION_ADAPTER = TypeAdapter[VerificationOutput](VerificationOutput)
T = TypeVar("T")


class _MessageDepthLimit(ValueError):
    pass


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _constant(value: str) -> object:
    raise ValueError("non_finite_number")


def decode[T](raw: bytes, adapter: TypeAdapter[T], *, diagnostic: bool = False) -> T:
    if len(raw) > MAX_MESSAGE_BYTES:
        raise PolicyError("MESSAGE_LIMIT_EXCEEDED")
    try:
        # Preflight preserves original bytes; validate_json provides strict JSON tuple/datetime
        # semantics without making Python-mode coercions or allowing duplicate keys.
        parsed: object = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant
        )
        pending = [(parsed, 0)]
        while pending:
            value, depth = pending.pop()
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("non_finite_number")
            if depth > 20:
                raise _MessageDepthLimit("depth_limit")
            if isinstance(value, dict):
                pending.extend((item, depth + 1) for item in value.values())
            elif isinstance(value, list):
                pending.extend((item, depth + 1) for item in value)
    except _MessageDepthLimit:
        raise PolicyError("MESSAGE_LIMIT_EXCEEDED" if diagnostic else "INVALID_MESSAGE") from None
    except (ValueError, RecursionError, UnicodeError):
        raise PolicyError(
            "MODEL_DECISION_JSON_INVALID" if diagnostic else "INVALID_MESSAGE"
        ) from None
    try:
        return adapter.validate_json(raw)
    except ValidationError as error:
        if diagnostic and any(
            item["type"] == "handoff_evidence_required"
            for item in error.errors(include_input=False, include_context=False, include_url=False)
        ):
            raise PolicyError("MODEL_EVIDENCE_REQUIRED") from None
        raise PolicyError(
            "MODEL_ROLE_SCHEMA_INVALID" if diagnostic else "INVALID_MESSAGE"
        ) from None
    except (ValueError, RecursionError, UnicodeError):
        raise PolicyError(
            "MODEL_ROLE_SCHEMA_INVALID" if diagnostic else "INVALID_MESSAGE"
        ) from None


def decode_role(raw: bytes, role: Role) -> RoleOutput:
    if role == "diagnosis":
        return decode(raw, DIAGNOSIS_ADAPTER)
    if role == "repair":
        return decode(raw, REPAIR_ADAPTER)
    if role == "verification":
        return decode(raw, VERIFICATION_ADAPTER)
    raise PolicyError("UNKNOWN_ROLE")
