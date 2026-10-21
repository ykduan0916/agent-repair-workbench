"""M5-R frozen synthetic memory ablation; no retries, no automatic learning.

Uses the production repair controller, gateway, Cognee and Docker oracle directly.
The no-memory arm also leaves 100k tokens unused, for identical Agent budgets.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_workbench.candidate_execution.docker import verified_image  # noqa: E402
from agent_workbench.candidate_execution.oracle import ORACLE_SHA256, SUITE_SHA256  # noqa: E402
from agent_workbench.candidates.snapshots import SnapshotStore  # noqa: E402
from agent_workbench.controlled_tools.resources import load_repair_case  # noqa: E402
from agent_workbench.experience_memory.agent_models import MemoryBinding  # noqa: E402
from agent_workbench.experience_memory.agent_runtime import MemorySession  # noqa: E402
from agent_workbench.experience_memory.agent_store import AgentMemoryStore  # noqa: E402
from agent_workbench.experience_memory.engine_models import Grant, Policy  # noqa: E402
from agent_workbench.experience_memory.engine_store import EngineStore  # noqa: E402
from agent_workbench.experience_memory.models import DraftInput, EventInput  # noqa: E402
from agent_workbench.model_gateway.gateway import Config  # noqa: E402
from agent_workbench.model_gateway.repair import RepairDeepSeekAdapter  # noqa: E402
from agent_workbench.orchestration.contracts import Budget  # noqa: E402
from agent_workbench.providers.cognee.runtime import CogneeRuntime  # noqa: E402
from agent_workbench.repair_cases.profiles import get_profile  # noqa: E402
from agent_workbench.repair_reader.reader import RepairReader  # noqa: E402
from agent_workbench.repair_recovery.inventory import RecoveryInventory  # noqa: E402
from agent_workbench.repair_recovery.service import RecoveryService  # noqa: E402
from agent_workbench.repair_runtime.controller import RepairController  # noqa: E402
from agent_workbench.repair_store.reproductions import ReproductionStore  # noqa: E402
from agent_workbench.web_memory.engine import providers_from_host  # noqa: E402
from ops.local_service import read_environment  # noqa: E402

CASES = ("output_mismatch", "counter_offset")
ARMS = ("none", "correct", "wrong")
BUDGET = Budget(total_tokens=1_900_000, output_tokens=8192)
ADVICE = {
    "output_mismatch": {
        "correct": "Preserve count as an integer rather than converting it to text.",
        "wrong": "Convert count to text rather than preserving it as an integer.",
    },
    "counter_offset": {
        "correct": "Return the number of matched items without adding an extra offset.",
        "wrong": "Return the number of matched items after adding an extra offset.",
    },
}


def save(path: Path, value: Any, *, exclusive: bool = False) -> None:
    data = json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    if exclusive:
        with path.open("x") as stream:
            stream.write(data)
    else:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(data)
        temporary.replace(path)


def sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def code_digest() -> str:
    paths = sorted(p for p in (ROOT / "src").rglob("*") if p.suffix in (".py", ".txt", ".json"))
    paths += [ROOT / "ops/m5r_eval.py", ROOT / "uv.lock"]
    return sha(
        {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    )


def cells() -> list[dict[str, Any]]:
    # Reverse both case/arm order in block 2 to reduce simple time-order bias.
    return [
        {
            "cell": f"{case}-{arm}-{rep}",
            "case": case,
            "arm": arm,
            "rep": rep,
            "task_id": str(uuid4()),
        }
        for rep in (1, 2)
        for case in (CASES if rep == 1 else CASES[::-1])
        for arm in (ARMS if rep == 1 else ARMS[::-1])
    ]


def body(case: str, arm: str) -> str:
    return (
        "Synthetic benchmark advice for lookup, deliberately authored rather than mined "
        "from historical repairs. " + ADVICE[case][arm] + " Reproduce first and run the "
        "current frozen regression. This reference is not proof of current correctness."
    )


def bill(accounting: dict[str, Any]) -> dict[str, Any]:
    calls = accounting.get("calls", [])
    return {
        "known_tokens": sum(c["actual_tokens"] for c in calls if type(c["actual_tokens"]) is int),
        "usage_complete": accounting.get("state") == "done"
        and all(c["state"] == "received" and type(c["actual_tokens"]) is int for c in calls),
        "unknown_calls": sum(
            c["state"] != "received" or type(c["actual_tokens"]) is not int for c in calls
        ),
        "reserved_tokens": accounting.get("reserved_tokens", 0),
        "llm_calls": sum(c["kind"] == "llm" for c in calls),
        "embedding_calls": sum(c["kind"] == "embedding" for c in calls),
        "monetary_cost": "unknown",
    }


def prepare(
    state: Path, case: str, arm: str, record: Path, providers: Any = None
) -> dict[str, Any]:
    store = EngineStore(state)
    report: dict[str, Any] = {"status": "preparing", "case": case, "arm": arm}
    save(record, report, exclusive=True)
    started = time.monotonic()
    try:
        store.initialize()
        if arm == "none":
            report["status"] = "ready"
            return report
        store.register_project("synthetic")
        store.register_connection("synthetic", "fixture", "synthetic")
        store.accept_event(
            "synthetic",
            "fixture",
            EventInput(
                event_id="fixture",
                event_type="test_completed",
                source_run_id="benchmark",
                occurred_at=time.time(),
                outcome="unknown",
            ),
        )
        item = store.create_memory(
            "synthetic",
            "create",
            DraftInput(
                kind="repair_experience",
                connection_id="fixture",
                event_id="fixture",
                tool_id="lookup",
                source_version=get_profile(case).source_sha256,
                title="Synthetic lookup benchmark advice",
                body=body(case, arm),
            ),
        )
        item = store.change_memory("synthetic", item.resource_id, "confirm", 1, state="confirmed")
        expiry = time.time() + 86400
        store.set_policy(
            "synthetic",
            item.resource_id,
            Policy(expires_at=expiry),
            expected_generation=item.version,
            expected_version=0,
        )
        for kind, purpose in (
            ("repair_experience", "index"),
            ("repair_experience", "retrieve"),
            ("repair_experience", "agent_context"),
            ("query", "retrieve"),
        ):
            store.set_grant(
                "synthetic",
                Grant(
                    connection_id="fixture",
                    content_type=kind,
                    purpose=purpose,
                    enabled=True,
                    expires_at=expiry,
                ),
                expected_version=0,
            )
        runtime = CogneeRuntime(
            store,
            state / "memory-engine",
            ROOT / "artifacts/mr01/venv/bin/python",
            providers if providers is not None else providers_from_host(),
        )

        def admitted(job_id: str) -> None:
            report["job_id"] = job_id
            save(record, report)

        runtime.index("synthetic", item.resource_id, on_job=admitted)
        report["status"] = "ready"
    except Exception:
        report["status"] = "setup_failed"
        report["error"] = "INDEX_OR_SETUP_FAILED"
    finally:
        if "job_id" in report:
            report["accounting"] = store.accounting("synthetic", report["job_id"])
        report["seconds"] = time.monotonic() - started
        save(record, report)
        store.close()
    return report


def worker(folder: Path, cell_id: str) -> int:
    plan = json.loads((folder / "plan.json").read_text())
    cell = next(c for c in plan["cells"] if c["cell"] == cell_id)
    record = folder / (cell_id + ".json")
    row: dict[str, Any] = {**cell, "status": "running"}
    save(record, row, exclusive=True)  # Existing attempts cannot be replayed.
    state = folder / (cell["case"] + "-" + cell["arm"])
    store = ReproductionStore(state)
    started = time.monotonic()
    try:
        adapter, providers = frozen_configuration(plan)
        registry = load_repair_case(cell["case"], task_id=cell["task_id"], budget=BUDGET)
        snapshots = SnapshotStore(state / "repair-snapshots")
        baseline = snapshots.create_baseline(str(uuid4()), case_id=cell["case"])
        binding = (
            None
            if cell["arm"] == "none"
            else MemoryBinding(project_id="synthetic", connection_id="fixture")
        )
        result = asyncio.run(
            RepairController(
                store=store,
                snapshots=snapshots,
                baseline=baseline,
                registry=registry,
                adapter=adapter,
                mode="three_role",
                image_root=ROOT,
                memory=None
                if binding is None
                else MemorySession(
                    state,
                    binding,
                    registry,
                    "repair",
                    runtime_factory=lambda authority: CogneeRuntime(
                        authority,
                        state / "memory-engine",
                        ROOT / "artifacts/mr01/venv/bin/python",
                        providers,
                    ),
                ),
            ).run()
        )
        row["task"] = result.cursor.task.model_dump(mode="json")
        row["status"] = "finished"
    except Exception:
        row["status"] = "execution_error"
        row["error"] = "EXECUTION_INCOMPLETE"
    finally:
        row["seconds"] = time.monotonic() - started
        save(record, row)
        store.close()
    return 0 if row["status"] == "finished" else 2


def collect(state: Path, row: dict[str, Any]) -> dict[str, Any]:
    reader = RepairReader(state, ROOT)
    memory = AgentMemoryStore(state)
    recovery = RecoveryInventory(state)
    try:
        detail = reader.detail(row["task_id"])
        row["task"] = reader.store.read_detail(row["task_id"])[0].task.model_dump(mode="json")
        row["comparisons"] = [c.model_dump(mode="json") for c in detail.comparisons]
        row["regression"] = any(
            c.verification == "verified"
            and any(s.slot == "baseline_pass" and s.status == "passed" for s in c.slots)
            and any(s.slot == "candidate_pass" and s.status == "failed" for s in c.slots)
            for c in detail.comparisons
        )
        if row["arm"] != "none":
            try:
                row["memory"] = memory.task_usage("repair", row["task_id"])
            except Exception:
                row["memory"] = {"state": "unavailable", "reason": "audit_missing", "uses": []}
        row["verified"] = row["task"]["outcome"] == "candidate_verified" and any(
            c.verification == "verified" and c.current_outcome == "candidate_verified"
            for c in detail.comparisons
        )
    except Exception:
        row["resources_confirmed"] = False
        row["collection_error"] = "EVIDENCE_UNAVAILABLE"
    finally:
        try:
            row["recovery"] = asyncio.run(
                RecoveryService(recovery, ROOT).recover(row["task_id"], apply=True)
            ).model_dump(mode="json")
            confirmed = asyncio.run(RecoveryService(recovery, ROOT).recover(row["task_id"]))
            row["resources_confirmed"] = confirmed.exit_code() == 0
            row["resources_confirmed"] = row["resources_confirmed"] and memory_idle(state)
            row["after_recovery_task"] = recovery.read_detail(row["task_id"])[0].task.model_dump(
                mode="json"
            )
        except Exception:
            row["resources_confirmed"] = False
        reader.close()
        memory.close()
        recovery.close()
    return row


def memory_idle(state: Path) -> bool:
    lock = state / "memory-engine/writer.lock"
    if not lock.exists():
        return True
    fd = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store = EngineStore(state)
        try:
            with store._transaction() as c:
                return (
                    c.exec_driver_sql(
                        "SELECT count(*) FROM memory_jobs WHERE state='leased'"
                    ).scalar_one()
                    == 0
                )
        finally:
            store.close()
    except BlockingIOError:
        return False
    finally:
        os.close(fd)


def frozen_configuration(plan: dict[str, Any]) -> tuple[RepairDeepSeekAdapter, Any]:
    os.environ.update(read_environment(ROOT / "artifacts/local-service/model-env.json"))
    adapter = RepairDeepSeekAdapter(Config.from_env())
    providers = providers_from_host()
    if (
        code_digest() != plan["code_sha256"]
        or adapter.config_sha256 != plan["model_config_sha256"]
        or providers.embedding_fingerprint != plan["embedding_fingerprint"]
        or verified_image(ROOT).model_dump() != plan["image"]
    ):
        raise ValueError("configuration_changed")
    return adapter, providers


def invariants(plan: dict[str, Any], row: dict[str, Any]) -> bool:
    task = row.get("task", {})
    comparisons = row.get("comparisons", [])
    memory = row.get("memory", {})
    uses = memory.get("uses", [])
    return (
        code_digest() == plan["code_sha256"]
        and verified_image(ROOT).model_dump() == plan["image"]
        and task.get("budget") == plan["agent_budget"]
        and task.get("model_config_sha256") == plan["model_config_sha256"]
        and all(
            c["oracle_sha256"] == plan["oracle_sha256"]
            and c["suite_sha256"] == plan["suite_sha256"]
            for c in comparisons
        )
        and not any(u["refs"] for u in uses if u["role"] == "verification")
        and (row["arm"] == "none" or len(uses) == task.get("usage", {}).get("model_calls", 0))
    )


def summarize(
    plan: dict[str, Any], rows: list[dict[str, Any]], setups: list[dict[str, Any]]
) -> dict[str, Any]:
    by_id = {r["cell"]: r for r in rows}
    arms = {}
    for arm in ARMS:
        planned = [c for c in plan["cells"] if c["arm"] == arm]
        observed = [by_id[c["cell"]] for c in planned if c["cell"] in by_id]
        arms[arm] = {
            "planned": len(planned),
            "recorded": len(observed),
            "verified": sum(r.get("verified", False) for r in observed),
            "exposed": sum(
                any(u["refs"] for u in r.get("memory", {}).get("uses", [])) for r in observed
            ),
            "finished": sum(r.get("status") == "finished" for r in observed),
            "known_agent_tokens": sum(
                r.get("task", {}).get("usage", {}).get("known_input_tokens", 0)
                + r.get("task", {}).get("usage", {}).get("known_output_tokens", 0)
                for r in observed
            ),
            "retrieval": [
                bill(r["memory"]["accounting"])
                for r in observed
                if "accounting" in r.get("memory", {})
            ],
            "index": [
                bill(s["accounting"]) for s in setups if s["arm"] == arm and "accounting" in s
            ],
            "all_usage_known": len(observed) == len(planned)
            and all(
                r.get("task", {}).get("usage", {}).get("usage_complete", False)
                and (arm == "none" or "accounting" in r.get("memory", {}))
                for r in observed
            ),
            "monetary_cost": "unknown",
        }
        retrieval = arms[arm]["retrieval"]
        indexes = arms[arm]["index"]
        arms[arm]["all_usage_known"] = (
            arms[arm]["all_usage_known"]
            and all(b["usage_complete"] for b in retrieval + indexes)
            and all(s["status"] == "ready" for s in setups if s["arm"] == arm)
        )
        arms[arm]["known_total_tokens"] = arms[arm]["known_agent_tokens"] + sum(
            b["known_tokens"] for b in retrieval + indexes
        )
        arms[arm]["regressions"] = sum(r.get("regression", False) for r in observed)
        arms[arm]["seconds"] = [r.get("seconds") for r in observed]
        arms[arm]["model_calls"] = [
            r.get("task", {}).get("usage", {}).get("model_calls") for r in observed
        ]
        arms[arm]["tool_calls"] = [
            r.get("task", {}).get("usage", {}).get("tool_calls") for r in observed
        ]
    return {"arms": arms, "scope": "synthetic_same_version_only", "monetary_cost": "unknown"}


def execute() -> Path:
    os.environ.update(read_environment(ROOT / "artifacts/local-service/model-env.json"))
    adapter = RepairDeepSeekAdapter(Config.from_env())
    folder = ROOT / "artifacts/m5r" / uuid4().hex
    folder.mkdir(parents=True, mode=0o700)
    plan = {
        "schema_version": 2,
        "cells": cells(),
        "agent_budget": BUDGET.model_dump(),
        "code_sha256": code_digest(),
        "model_config_sha256": adapter.config_sha256,
        "embedding_fingerprint": providers_from_host().embedding_fingerprint,
        "image": verified_image(ROOT).model_dump(),
        "case_profiles": {c: get_profile(c).profile_digest for c in CASES},
        "advice_sha256": {c: {a: sha(body(c, a)) for a in ARMS[1:]} for c in CASES},
        "index_reuse": 2,
        "mode": "three_role",
        "retries": 0,  # Whole cell / index replay remains forbidden.
        "embedding_request_policy": {
            "revision": "REL-03",
            "max_attempts": 3,
            "retryable": "500:new_api_error:do_request_failed",
            "unknown_usage_preserved": True,
        },
        "oracle_sha256": ORACLE_SHA256,
        "suite_sha256": SUITE_SHA256,
    }
    save(folder / "plan.json", plan, exclusive=True)
    print(json.dumps({"study": str(folder), "plan_sha256": sha(plan)}), flush=True)
    setups = []
    rows = []
    halted = False
    for case in CASES:
        for arm in ARMS:
            state = folder / (case + "-" + arm)
            index_file = folder / (case + "-" + arm + "-index.json")
            try:
                if halted:
                    raise ValueError("study_stopped")
                _, providers = frozen_configuration(plan)
            except Exception:
                halted = True
                report = {"case": case, "arm": arm, "status": "not_run_safety_stop"}
                save(index_file, report, exclusive=True)
            else:
                report = prepare(state, case, arm, index_file, providers)
                halted = not memory_idle(state)
            setups.append(report)
            print(json.dumps({"setup": case + "-" + arm, "status": report["status"]}), flush=True)
    for cell in plan["cells"]:
        file = folder / (cell["cell"] + ".json")
        setup = next(s for s in setups if (s["case"], s["arm"]) == (cell["case"], cell["arm"]))
        if halted or setup["status"] != "ready":
            row = {**cell, "status": "not_run_safety_stop" if halted else "setup_blocked"}
            save(file, row, exclusive=True)
        else:
            try:
                subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "--worker",
                        str(folder),
                        "--cell",
                        cell["cell"],
                    ],
                    timeout=780,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except subprocess.TimeoutExpired:
                pass  # Collect exact ledger/resources; never retry the cell.
            row = (
                json.loads(file.read_text())
                if file.exists()
                else {**cell, "status": "worker_missing"}
            )
            if row["status"] == "running":
                row["status"] = "worker_interrupted"
            row = collect(folder / (cell["case"] + "-" + cell["arm"]), row)
            try:
                row["invariants_ok"] = invariants(plan, row)
            except Exception:
                row["invariants_ok"] = False
            halted = not row.get("resources_confirmed", False) or not row["invariants_ok"]
            save(file, row)
        rows.append(row)
        save(folder / "summary.json", summarize(plan, rows, setups))
        print(
            json.dumps(
                {
                    "cell": cell["cell"],
                    "status": row["status"],
                    "verified": row.get("verified", False),
                    "halted": halted,
                }
            ),
            flush=True,
        )
    return folder


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--cell")
    args = parser.parse_args()
    os.umask(0o077)
    if args.worker and args.cell:
        return worker(args.worker, args.cell)
    if not args.execute:
        parser.error("--execute required; uses existing authorized model and embedding")
    try:
        execute()
        return 0
    except Exception:
        print('{"error":"EVALUATION_INCOMPLETE"}', flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
