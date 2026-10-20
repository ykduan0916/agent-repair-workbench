"""M3-R live gate validation; no actual model or Docker invocation in these tests."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("m3_gate_test", ROOT / "ops/m3r_live_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def evidence():
    return {
        "state": "done",
        "reason": "matched",
        "uses": [
            {"role": "diagnosis", "refs": [{}]},
            {"role": "repair", "refs": [{}]},
            {"role": "verification", "refs": []},
        ],
        "accounting": {
            "reserved_tokens": 5000,
            "calls": [{"kind": "embedding", "state": "received", "actual_tokens": 100}],
        },
    }, {
        "budget": {"total_tokens": 1900000},
        "usage": {
            "usage_complete": True,
            "model_calls": 3,
            "known_input_tokens": 200,
            "known_output_tokens": 100,
        },
    }


def test_gate_counts_retrieval_cost_and_role_usage():
    memory, task = evidence()
    gate.validate_memory(memory, task)
    assert memory["total_known_tokens"] == 400


@pytest.mark.parametrize("change", ["verification", "unknown", "budget", "calls", "audit"])
def test_gate_cannot_pass_partial_or_leaking_evidence(change):
    memory, task = evidence()
    if change == "verification":
        memory["uses"][-1]["refs"] = [{"memory_id": "leak"}]
    elif change == "unknown":
        memory["accounting"]["calls"][0]["actual_tokens"] = None
    elif change == "budget":
        task["budget"]["total_tokens"] = 2000000
    elif change == "calls":
        memory["accounting"]["calls"] = [copy.deepcopy(memory["accounting"]["calls"][0])] * 41
    else:
        task["usage"]["model_calls"] = 4
    with pytest.raises(gate.GateFailure):
        gate.validate_memory(memory, task)


def test_pre_execution_failure_is_saved_without_provider(monkeypatch, tmp_path):
    def fail(_):
        raise gate.GateFailure("fixture_unavailable")

    async def inventory(_):
        return {"confirmed": True, "tasks": []}

    monkeypatch.setattr(gate, "PORT", 0)
    monkeypatch.setattr(gate, "seed", fail)
    monkeypatch.setattr(gate, "reclaim", inventory)
    result = gate.run_gate(tmp_path)
    assert result["status"] == "failed"
    saved = json.loads((tmp_path / "acceptance.json").read_text())
    assert saved["failure"] == "fixture_unavailable"
    assert saved["api_cleanup"] == "not_started"


@pytest.mark.parametrize("verification_observed", [True, False])
def test_terminal_evidence_preserved_before_active_slot_release(monkeypatch, verification_observed):
    memory, task = evidence()
    if not verification_observed:
        memory["uses"].pop()
        task["usage"]["model_calls"] = 2
    task["outcome"] = "inconclusive"
    task["phase"] = "finished"
    receipt = {"task": task, "active": True}
    report = {}
    monkeypatch.setattr(gate, "get", lambda *args: memory)
    gate.capture_terminal_evidence(None, "task", receipt, report)
    assert report["repair_gate"] == "inconclusive"
    assert report["memory"] == memory
    assert report["memory_gate"] == ("passed" if verification_observed else "failed")
    if not verification_observed:
        assert report["memory_failure"] == "verification_not_observed"
    assert receipt["active"] is True
