"""Safe persisted snapshot failure codes; never copy arbitrary exception text."""

from typing import cast

from agent_workbench.analysis_store.models import ErrorCode

KNOWN_FAILURES = frozenset(
    {
        "ATOMIC_PUBLISH_UNAVAILABLE",
        "BASELINE_REQUIRED",
        "PATCH_BASE_MISMATCH",
        "PATCH_EMPTY",
        "PATCH_REQUEST_INVALID",
        "PATCH_SCOPE_REJECTED",
        "PATCH_SYNTAX_INVALID",
        "SNAPSHOT_CONTENT_MISMATCH",
        "SNAPSHOT_ENCODING_INVALID",
        "SNAPSHOT_FILE_REJECTED",
        "SNAPSHOT_ID_CONFLICT",
        "SNAPSHOT_IO_ERROR",
        "SNAPSHOT_LIMIT_EXCEEDED",
        "SNAPSHOT_PATH_REJECTED",
        "SNAPSHOT_REQUEST_INVALID",
        "SOURCE_MANIFEST_MISMATCH",
    }
)


def candidate_failure_code(value: str) -> ErrorCode:
    if value == "SNAPSHOT_COMMIT_UNKNOWN":
        return "RESULT_UNKNOWN"
    return cast(ErrorCode, value) if value in KNOWN_FAILURES else "TOOL_FAILED"
