"""REL-01/02 static diagnostics and task-wide format correction policy."""

from typing import Final, cast

from agent_workbench.analysis_store.models import ErrorCode

PROTOCOL_REVISION: Final = 2
FORMAT_CORRECTIONS: Final = 1
CORRECTABLE: Final = frozenset(
    {"MODEL_DECISION_INVALID", "MODEL_DECISION_JSON_INVALID", "MODEL_ROLE_SCHEMA_INVALID"}
)
_CODES: Final = CORRECTABLE | frozenset(
    {
        "MODEL_RESPONSE_INVALID",
        "MODEL_OUTPUT_TRUNCATED",
        "MODEL_USAGE_UNKNOWN",
        "MODEL_USAGE_BOUND_VIOLATION",
        "MODEL_TIMEOUT",
        "MODEL_CANCELLED",
        "MODEL_TRANSPORT_ERROR",
        "MODEL_DECISION_LIMIT",
        "MODEL_EVIDENCE_INVALID",
        "MODEL_HANDOFF_INVALID",
        "MODEL_EVIDENCE_REQUIRED",
    }
)


def rejection_code(value: str | None) -> ErrorCode:
    """Never persist arbitrary adapter messages or exception strings."""
    return cast(ErrorCode, value) if value in _CODES else "MODEL_RESPONSE_INVALID"
