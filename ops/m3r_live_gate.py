"""Opt-in M3-R real model/engine/Docker/API gate. One repair; all attempts retained."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_workbench.experience_memory.engine_models import Grant, Policy  # noqa: E402
from agent_workbench.experience_memory.models import DraftInput, EventInput  # noqa: E402
from agent_workbench.repair_cases.profiles import get_profile  # noqa: E402
from agent_workbench.web_memory.engine import EngineWebStore  # noqa: E402
from ops.b5_live_gate import inventory, safe_stop_process, wait_ready, write_json  # noqa: E402
from ops.local_service import read_environment  # noqa: E402

PORT = 8039


class GateFailure(ValueError):
    """Only fixed safe codes, never provider/HTTP exception messages."""


def get(client: httpx.Client, path: str) -> dict[str, Any]:
    response = client.get(path)
    if response.status_code != 200:
        raise GateFailure("read_failed")
    return response.json()


def post(client: httpx.Client, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post(path, json=payload)
    if response.status_code not in (200, 201, 202):
        raise GateFailure("write_failed")
    return response.json()


def seed(store: EngineWebStore) -> str:
    store.initialize()
    store.register_project("synthetic")
    store.register_connection("synthetic", "fixture", "synthetic")
    store.accept_event(
        "synthetic",
        "fixture",
        EventInput(
            event_id="fixture",
            event_type="test_completed",
            source_run_id="synthetic_count",
            occurred_at=time.time(),
            outcome="failed",
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
            source_version=get_profile("output_mismatch").source_sha256,
            title="Synthetic lookup result contract experience",
            body=(
                "In this synthetic lookup tool the output contract requires an integer count. "
                "Inspect the result construction and preserve the integer value instead of "
                "converting count to text. Reproduce first and run the current frozen regression; "
                "this reference does not prove that the current candidate passes."
            ),
        ),
    )
    item = store.change_memory("synthetic", item.resource_id, "confirm", 1, state="confirmed")
    store.set_policy(
        "synthetic",
        item.resource_id,
        Policy(expires_at=time.time() + 3600),
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
                expires_at=time.time() + 3600,
            ),
            expected_version=0,
        )
    return item.resource_id


def validate_memory(report: dict[str, Any], task: dict[str, Any]) -> None:
    uses = report["uses"]
    if report["state"] != "done" or report["reason"] != "matched":
        raise GateFailure("memory_not_matched")
    if not all(
        any(u["role"] == role and u["refs"] for u in uses) for role in ("diagnosis", "repair")
    ):
        raise GateFailure("role_reference_missing")
    if any(u["refs"] for u in uses if u["role"] == "verification"):
        raise GateFailure("verification_memory_leak")
    if not any(u["role"] == "verification" for u in uses):
        raise GateFailure("verification_not_observed")
    if len(uses) != task["usage"]["model_calls"]:
        raise GateFailure("request_audit_incomplete")
    bill = report["accounting"]
    calls = bill["calls"]
    if any(c["state"] != "received" or type(c["actual_tokens"]) is not int for c in calls):
        raise GateFailure("memory_usage_unknown")
    if (
        bill["reserved_tokens"] > 100000
        or sum(c["kind"] == "llm" for c in calls) > 6
        or sum(c["kind"] == "embedding" for c in calls) > 40
    ):
        raise GateFailure("retrieval_budget_exceeded")
    usage = task["usage"]
    if not usage["usage_complete"] or usage["model_calls"] > 12:
        raise GateFailure("agent_usage_unknown")
    if task["budget"]["total_tokens"] != 1900000:
        raise GateFailure("budget_partition_missing")
    total = (
        usage["known_input_tokens"]
        + usage["known_output_tokens"]
        + sum(c["actual_tokens"] for c in calls)
    )
    if total > 2000000:
        raise GateFailure("total_budget_exceeded")
    report["total_known_tokens"] = total
    report["memory_gate"] = "passed"


def capture_terminal_evidence(client, task_id: str, receipt: dict, report: dict) -> None:
    """Save terminal memory evidence even when owner release is still pending."""
    report["repair_gate"] = receipt["task"]["outcome"]
    report["memory"] = get(client, f"/api/repair-runs/{task_id}/memory")
    try:
        validate_memory(report["memory"], receipt["task"])
    except GateFailure as error:
        report["memory_gate"] = "failed"
        report["memory_failure"] = str(error)
    else:
        report["memory_gate"] = "passed"


async def reclaim(state: Path) -> dict[str, object]:
    from agent_workbench.repair_store.fencing import RepairFence
    from agent_workbench.web_repairs.fencing import RepairWebFence
    from agent_workbench.web_repairs.recovery import WebRepairRecovery
    from agent_workbench.web_repairs.store import WebRepairStore

    store = WebRepairStore(state)
    try:
        with RepairWebFence(state) as owner:
            for item in store.list_tasks(50):
                task_id = item.task.task_id
                with RepairFence(state, task_id) as fence:
                    await WebRepairRecovery(store, ROOT).release_checked(
                        task_id, owner=owner, fence=fence, apply=True
                    )
    finally:
        store.close()
    return await inventory(state)


def run_gate(folder: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "gate": "M3-R",
        "synthetic": True,
        "status": "failed",
        "attempt_policy": "one_index_one_retrieval_one_repair_no_retries",
        "memory_gate": "not_completed",
        "repair_gate": "not_completed",
        "stage": "setup",
    }
    api = None
    store = EngineWebStore(folder / "state")
    try:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", PORT))
        memory_id = seed(store)
        env = {k: v for k, v in os.environ.items() if not k.startswith("AWB_MODEL_")}
        env.update(read_environment(ROOT / "artifacts/local-service/model-env.json"))
        env.update(AWB_STATE_DIR=str(folder / "state"), AWB_FRONTEND_PORT="3019")
        api = subprocess.Popen(
            [
                str(ROOT / ".venv/bin/python"),
                "-m",
                "uvicorn",
                "agent_workbench.api:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(PORT),
                "--no-access-log",
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        wait_ready(f"http://127.0.0.1:{PORT}/api/health", api)
        with httpx.Client(
            base_url=f"http://127.0.0.1:{PORT}",
            trust_env=False,
            timeout=10,
            headers={"Origin": "http://127.0.0.1:3019", "X-AWB-Local": "1"},
        ) as client:
            report["stage"] = "index"
            operation_id = str(uuid4())
            post(
                client,
                "/api/memory/projects/synthetic/engine",
                {"request_id": operation_id, "operation": "index", "memory_id": memory_id},
            )
            until = time.monotonic() + 210
            while time.monotonic() < until:
                operation = get(client, "/api/memory/projects/synthetic/engine/" + operation_id)
                report["index"] = operation
                write_json(folder / "acceptance.json", report)
                if operation["state"] != "running":
                    break
                time.sleep(0.5)
            else:
                raise GateFailure("index_timeout")
            if operation["state"] != "done":
                raise GateFailure("index_failed")
            report["stage"] = "repair"
            task_id = str(uuid4())
            report["task_id"] = task_id
            receipt = post(
                client,
                "/api/repair-runs",
                {
                    "request_id": task_id,
                    "case_id": "output_mismatch",
                    "mode": "three_role",
                    "memory": {
                        "project_id": "synthetic",
                        "connection_id": "fixture",
                        "allow_historical": False,
                    },
                },
            )
            report["admission"] = receipt
            until = time.monotonic() + 650
            while time.monotonic() < until:
                receipt = get(client, "/api/repair-runs/" + task_id)
                report["terminal"] = receipt
                if receipt["task"]["phase"] == "finished":
                    if "memory" not in report:
                        capture_terminal_evidence(client, task_id, receipt, report)
                    if not receipt["active"]:
                        break
                write_json(folder / "acceptance.json", report)
                time.sleep(1)
            else:
                raise GateFailure("repair_timeout")
            report["memory"] = get(client, f"/api/repair-runs/{task_id}/memory")
            report["repair_gate"] = receipt["task"]["outcome"]
            validate_memory(report["memory"], receipt["task"])
            report["memory_gate"] = "passed"
            if receipt["task"]["outcome"] != "candidate_verified":
                raise GateFailure("repair_not_verified")
            report["stage"] = "approve_and_draft"
            detail = get(client, "/api/repairs/" + task_id)
            current = [
                c
                for c in detail["comparisons"]
                if c["current_outcome"] == "candidate_verified" and c["verification"] == "verified"
            ]
            if len(current) != 1:
                raise GateFailure("current_frozen_evidence_missing")
            candidate_id = current[0]["candidate_id"]
            decision_path = f"/api/repairs/{task_id}/candidates/{candidate_id}/decision"
            before = get(client, decision_path)
            accepted = post(
                client,
                decision_path,
                {
                    "request_id": str(uuid4()),
                    "decision": "accepted",
                    "expected_revision": before["revision"],
                    "expected_candidate_sha256": before["candidate_sha256"],
                    "expected_comparison_sha256": before["comparison_sha256"],
                },
            )
            draft_path = f"/api/repairs/{task_id}/candidates/{candidate_id}/memory-draft"
            report["draft"] = post(
                client,
                draft_path,
                {
                    "request_id": str(uuid4()),
                    "project_id": "synthetic",
                    "connection_id": "fixture",
                    "expected_decision_revision": accepted["revision"],
                    "expected_candidate_sha256": accepted["candidate_sha256"],
                    "expected_comparison_sha256": accepted["comparison_sha256"],
                },
            )
            if report["draft"]["state"] != "draft":
                raise GateFailure("draft_autoconfirmed")
            report["comparison"] = current[0]
            report["status"] = "passed"
    except GateFailure as error:
        report["failure"] = str(error)
    except Exception:
        report["failure"] = "gate_incomplete"
    finally:
        report["api_cleanup"] = safe_stop_process(api)
        try:
            report["resources"] = asyncio.run(reclaim(folder / "state"))
        except Exception:
            report["resources"] = {"confirmed": False}
        if report["api_cleanup"] == "unconfirmed" or not report["resources"]["confirmed"]:
            report["status"] = "failed"
            report["cleanup_failure"] = "resource_cleanup_unconfirmed"
        store.close()
        write_json(folder / "acceptance.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", required=True)
    parser.parse_args()
    os.umask(0o077)
    folder = ROOT / "artifacts/m3r" / str(uuid4())
    folder.mkdir(parents=True, mode=0o700)
    report = run_gate(folder)
    print(json.dumps({"status": report["status"], "report": str(folder / "acceptance.json")}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
