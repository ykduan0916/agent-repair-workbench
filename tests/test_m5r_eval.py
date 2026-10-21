"""M5-R accounting and experiment integrity without external model calls."""

import importlib.util
import json
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("m5r_eval", ROOT / "ops/m5r_eval.py")
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def test_plan_is_balanced_reversed_and_has_unique_task_identity():
    cells = gate.cells()
    assert len(cells) == len({c["task_id"] for c in cells}) == 12
    assert Counter((c["case"], c["arm"]) for c in cells) == {
        (c, a): 2 for c in gate.CASES for a in gate.ARMS
    }
    assert [(c["case"], c["arm"]) for c in cells[:6]] == [
        (c["case"], c["arm"]) for c in cells[6:][::-1]
    ]
    assert gate.BUDGET.total_tokens == 1_900_000


def test_write_once_blocks_replay_and_atomic_update_preserves_json(tmp_path):
    path = tmp_path / "cell.json"
    gate.save(path, {"status": "running"}, exclusive=True)
    with pytest.raises(FileExistsError):
        gate.save(path, {"status": "running"}, exclusive=True)
    gate.save(path, {"status": "unknown"})
    assert json.loads(path.read_text()) == {"status": "unknown"}


def test_unknown_wire_cost_is_lower_bound_not_zero_or_complete():
    summary = gate.bill(
        {
            "reserved_tokens": 500,
            "calls": [
                {"kind": "llm", "state": "received", "actual_tokens": 40},
                {"kind": "embedding", "state": "unknown", "actual_tokens": None},
            ],
        }
    )
    assert summary["known_tokens"] == 40
    assert summary["unknown_calls"] == 1
    assert summary["usage_complete"] is False
    assert summary["reserved_tokens"] == 500
    assert summary["monetary_cost"] == "unknown"


def test_failed_and_missing_cells_stay_in_denominator_and_index_not_doubled():
    plan = {"cells": gate.cells()}
    cell = next(c for c in plan["cells"] if c["arm"] == "correct")
    rows = [{**cell, "status": "setup_blocked"}]
    setup = {
        "arm": "correct",
        "accounting": {"calls": [{"kind": "llm", "state": "received", "actual_tokens": 50}]},
    }
    result = gate.summarize(plan, rows, [setup])["arms"]["correct"]
    assert result["planned"] == 4
    assert result["recorded"] == 1
    assert result["verified"] == result["exposed"] == 0
    assert result["known_total_tokens"] == 50
    assert not result["all_usage_known"]


def test_setup_failure_preserves_admitted_job_and_unknown_bill(tmp_path, monkeypatch):
    class FailingRuntime:
        def __init__(self, *args):
            pass

        def index(self, project, memory_id, *, on_job):
            on_job("admitted")
            raise ValueError("sensitive exception must not be serialized")

    monkeypatch.setattr(gate, "CogneeRuntime", FailingRuntime)
    monkeypatch.setattr(gate, "providers_from_host", lambda: None)
    monkeypatch.setattr(
        gate.EngineStore,
        "accounting",
        lambda *a: {"calls": [{"kind": "llm", "state": "unknown", "actual_tokens": None}]},
    )
    path = tmp_path / "index.json"
    report = gate.prepare(tmp_path / "state", "output_mismatch", "wrong", path)
    assert report["status"] == "setup_failed"
    assert report["job_id"] == "admitted"
    assert report["accounting"]["calls"][0]["state"] == "unknown"
    assert "sensitive" not in path.read_text()


def test_control_has_no_memory_or_provider_initialization(tmp_path, monkeypatch):
    def forbidden():
        pytest.fail("Control setup must not access providers")

    monkeypatch.setattr(gate, "providers_from_host", forbidden)
    report = gate.prepare(tmp_path / "state", "counter_offset", "none", tmp_path / "index.json")
    assert report["status"] == "ready"
    assert "job_id" not in report


def test_wrong_verification_reference_or_missing_audit_stops_study(monkeypatch):
    class Image:
        def model_dump(self):
            return {"image": "frozen"}

    monkeypatch.setattr(gate, "code_digest", lambda: "code")
    monkeypatch.setattr(gate, "verified_image", lambda root: Image())
    plan = {
        "code_sha256": "code",
        "image": {"image": "frozen"},
        "agent_budget": gate.BUDGET.model_dump(),
        "model_config_sha256": "model",
    }
    row = {
        "arm": "wrong",
        "task": {
            "budget": gate.BUDGET.model_dump(),
            "model_config_sha256": "model",
            "usage": {"model_calls": 1},
        },
        "memory": {"uses": [{"role": "verification", "refs": []}]},
    }
    assert gate.invariants(plan, row)
    row["memory"]["uses"][0]["refs"] = [{"memory_id": "wrong"}]
    assert not gate.invariants(plan, row)
    row["memory"]["uses"] = []
    assert not gate.invariants(plan, row)


def test_collection_failure_still_reclaims_resources(tmp_path, monkeypatch):
    from types import SimpleNamespace

    calls = []

    class Reader:
        def __init__(self, *args):
            pass

        def detail(self, task_id):
            raise ValueError("corrupt comparison")

        def close(self):
            pass

    class Recovery:
        def __init__(self, *args):
            pass

        async def recover(self, task_id, *, apply=False):
            calls.append(apply)
            return SimpleNamespace(model_dump=lambda **kw: {}, exit_code=lambda: 0)

    monkeypatch.setattr(gate, "RepairReader", Reader)
    monkeypatch.setattr(gate, "RecoveryService", Recovery)
    result = gate.collect(tmp_path, {"task_id": "missing", "arm": "none"})
    assert calls == [True, False]
    assert result["collection_error"] == "EVIDENCE_UNAVAILABLE"
    assert result["resources_confirmed"] is False


def test_missing_retrieval_usage_cannot_be_complete():
    plan = {"cells": gate.cells()}
    rows = [{**c, "task": {"usage": {"usage_complete": True}}} for c in plan["cells"]]
    result = gate.summarize(plan, rows, [])
    assert result["arms"]["none"]["all_usage_known"]
    assert not result["arms"]["correct"]["all_usage_known"]


def test_frozen_configuration_denies_drift_without_external_calls(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(gate, "read_environment", lambda path: {})
    monkeypatch.setattr(gate.Config, "from_env", lambda: None)
    monkeypatch.setattr(
        gate, "RepairDeepSeekAdapter", lambda config: SimpleNamespace(config_sha256="model")
    )
    monkeypatch.setattr(
        gate, "providers_from_host", lambda: SimpleNamespace(embedding_fingerprint="changed")
    )
    monkeypatch.setattr(gate, "code_digest", lambda: "code")
    with pytest.raises(ValueError, match="configuration_changed"):
        gate.frozen_configuration(
            {
                "code_sha256": "code",
                "model_config_sha256": "model",
                "embedding_fingerprint": "original",
            }
        )
