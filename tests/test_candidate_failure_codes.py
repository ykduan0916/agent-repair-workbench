"""Persist allowlisted candidate rejection reasons, never patch text or arbitrary errors."""

import json

import pytest

from agent_workbench.candidates.contracts import CandidateError
from agent_workbench.candidates.diagnostics import KNOWN_FAILURES, candidate_failure_code
from tests.agents.test_repair_tool_bridge import apply_request, invoke, reproduce
from tests.agents.test_repair_tool_bridge import ctx as ctx


@pytest.mark.parametrize("code", sorted(KNOWN_FAILURES))
def test_candidate_code_allowlist(code):
    assert candidate_failure_code(code) == code


@pytest.mark.parametrize(
    "code,expected",
    [
        ("PATCH_SCOPE_REJECTED", "PATCH_SCOPE_REJECTED"),
        ("PATCH_SYNTAX_INVALID", "PATCH_SYNTAX_INVALID"),
        ("SNAPSHOT_IO_ERROR", "SNAPSHOT_IO_ERROR"),
        ("SNAPSHOT_COMMIT_UNKNOWN", "RESULT_UNKNOWN"),
        ("PRIVATE_NONCODE_EXCEPTION_TEXT", "TOOL_FAILED"),
    ],
)
def test_actual_candidate_failure_event_is_safe_and_specific(ctx, monkeypatch, code, expected):
    reproduce(ctx)

    def fail(*args):
        raise CandidateError(code)

    monkeypatch.setattr(ctx.snapshots, "create_candidate", fail)
    result = invoke(ctx, "repair", apply_request(ctx))
    assert result.cursor.candidate.error_code == expected
    assert result.cursor.request.error_code == expected
    with ctx.store._base._transaction() as c:
        events = [
            json.loads(row[0])
            for row in c.exec_driver_sql("SELECT payload FROM repair_events ORDER BY revision")
        ]
    settled = events[-1]
    assert settled["request"]["error_code"] == expected
    assert settled["candidate"]["error_code"] == expected
    assert "PRIVATE_NONCODE_EXCEPTION_TEXT" not in json.dumps(events)
    assert "replacement" not in json.dumps(events)
