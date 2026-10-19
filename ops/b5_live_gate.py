"""B5 opt-in live acceptance. Private isolated state; one attempt per fixed case.

Run with .venv/bin/python ops/b5_live_gate.py --execute.
This spends the existing model budget and runs the already audited Docker image.
No model response, credential, provider URL, or production database is exported.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
# Script and import invocation must use the same host-owned credential loader.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.local_service import read_environment  # noqa: E402

API_PORT, WEB_PORT = 8038, 3018
CASES = ("output_mismatch", "counter_offset")


class GateFailure(Exception):
    """Fixed, safe stage code. Never include subprocess or provider exception text."""


def write_json(path: Path, value: object) -> None:
    with path.open("w", encoding="utf-8") as output:
        os.chmod(path, 0o600)
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")


def check_ports() -> None:
    for port in (API_PORT, WEB_PORT):
        with socket.socket() as listener:
            try:
                listener.bind(("127.0.0.1", port))
            except OSError:
                raise GateFailure("isolated_port_busy") from None


def group_exists(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Darwin can transiently report EPERM for a just-terminated orphan group.
        # Confirm membership from kernel process metadata; EPERM alone is never clean.
        result = subprocess.run(
            ["/bin/ps", "-axo", "pid=,pgid=,uid=,stat="],
            capture_output=True,
            timeout=3,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise GateFailure("process_group_inventory_unavailable") from None
        members = []
        try:
            for line in result.stdout.decode("ascii", errors="strict").splitlines():
                fields = line.split()
                if len(fields) != 4:
                    raise ValueError
                member_pid, pgid, uid = map(int, fields[:3])
                if member_pid <= 0 or pgid < 0 or uid < 0:
                    raise ValueError
                if pgid == pid:
                    if uid != os.getuid():
                        raise ValueError
                    members.append(member_pid)
        except (UnicodeError, ValueError):
            raise GateFailure("process_group_inventory_unavailable") from None
        return bool(members)


def signal_group(pid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, PermissionError):
        # Reconfirm the exact owned group when it disappears between probe and signal.
        if group_exists(pid):
            raise


def wait_group_absent(pid: int, timeout: float = 3) -> bool:
    deadline = time.monotonic() + timeout
    while group_exists(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)
    return True


def stop_process(process: subprocess.Popen[bytes] | None) -> str:
    if process is None:
        return "not_started"
    try:
        # Reap the leader, but do not infer its descendants have also exited.
        process.poll()
        if not group_exists(process.pid):
            process.wait(timeout=5)
            return "already_exited"
        signal_group(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=45)
            if wait_group_absent(process.pid):
                return "terminated"
        except subprocess.TimeoutExpired:
            pass
        if group_exists(process.pid):
            signal_group(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        if wait_group_absent(process.pid):
            return "killed_requires_inventory"
        return "unconfirmed"
    except (OSError, subprocess.SubprocessError, GateFailure):
        return "unconfirmed"


def safe_stop_process(process: subprocess.Popen[bytes] | None) -> str:
    """One broken cleanup cannot prevent cleanup of the other owned children."""
    try:
        return stop_process(process)
    except Exception:
        return "unconfirmed"


def run_bounded(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    output: Any,
    timeout: int,
    cleanup: dict[str, str],
    stage: str,
) -> int:
    """Own the complete child process group, including on timeout/interruption."""
    process = subprocess.Popen(
        argv, cwd=cwd, env=env, stdout=output, stderr=output, start_new_session=True
    )
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        raise GateFailure(f"{stage}_timeout") from None
    finally:
        cleanup[stage] = safe_stop_process(process)
        if cleanup[stage] == "unconfirmed":
            raise GateFailure(f"{stage}_cleanup_unconfirmed")


def wait_ready(url: str, process: subprocess.Popen[bytes], timeout: int = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise GateFailure("service_exited_before_ready")
        try:
            if httpx.get(url, timeout=1, trust_env=False).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise GateFailure("service_readiness_timeout")


async def inventory(state: Path) -> dict[str, object]:
    """Read all attempts, including failed runs. No broad Docker deletion or replay."""
    from agent_workbench.candidate_execution.docker import CandidateDockerControl, verified_image
    from agent_workbench.candidates.snapshots import SnapshotHandle
    from agent_workbench.repair_recovery.inventory import RecoveryInventory
    from agent_workbench.runner.docker import DockerControl
    from agent_workbench.web_repairs.store import WebRepairStore

    saved = WebRepairStore(state)
    source = RecoveryInventory(state)
    rows: list[dict[str, object]] = []
    clean = True
    try:
        tasks = saved.list_tasks(50)
        control = await DockerControl.connect()
        image = verified_image(ROOT)
        for item in tasks:
            task_id = item.task.task_id
            value = source.inventory(task_id)
            targets = []
            for target in value.targets:
                handle = SnapshotHandle(
                    target.snapshot, state / "repair-snapshots" / target.snapshot.snapshot_id
                )
                docker = CandidateDockerControl(
                    control, handle, image, target.scenario, target.attempt_id
                )
                # inspect checks exact labels, image, name and snapshot binding.
                present = await docker.inspect() is not None
                clean = clean and not present
                targets.append(
                    {
                        "attempt_id": target.attempt_id,
                        "tool": target.tool,
                        "slot": target.slot,
                        "container_absent": not present,
                    }
                )
            receipt = saved.receipt(task_id)
            detail = source.comparisons(task_id)
            rows.append(
                {
                    "task_id": task_id,
                    "phase": item.task.phase,
                    "outcome": item.task.outcome,
                    "error_code": item.task.error_code,
                    "budget": item.task.budget.model_dump(mode="json"),
                    "usage": item.task.usage.model_dump(mode="json"),
                    "active": receipt.active,
                    "pending_operation": value.pending_operation,
                    "attempts": targets,
                    "comparisons": [
                        {
                            "comparison_id": c.plan.comparison_id,
                            "status": c.status,
                            "outcome": c.report.outcome if c.report else None,
                            "slots": [
                                {
                                    "slot": s.slot,
                                    "status": s.report.status,
                                    "cleanup": s.report.cleanup,
                                }
                                for s in c.results
                            ],
                        }
                        for c in detail
                    ],
                }
            )
            clean = clean and not receipt.active and value.pending_operation is None
        return {"confirmed": clean, "tasks": rows, "task_count": len(rows)}
    finally:
        saved.close()
        source.close()


def verify_browser_evidence(directory: Path, tasks: list[dict[str, Any]]) -> None:
    """Cross-check downloaded bytes against immutable local snapshots, not browser claims."""
    from agent_workbench.repair_reader.reader import RepairReader

    if len(tasks) != 3:
        raise GateFailure("unexpected_task_count")
    reader = RepairReader(directory / "state", ROOT)
    observed = set()
    try:
        for case_id in CASES:
            evidence = json.loads((directory / f"{case_id}.json").read_text())
            if evidence.get("status") != "passed" or evidence.get("case_id") != case_id:
                raise GateFailure("browser_case_evidence_incomplete")
            task_id = evidence["task_id"]
            observed.add(task_id)
            candidate_id = evidence["verified_candidate_id"]
            expected = reader.diff(task_id, candidate_id).diff.unified_diff.encode("utf-8")
            actual = (directory / f"{case_id}.patch").read_bytes()
            if (
                actual != expected
                or hashlib.sha256(actual).hexdigest() != evidence["download"]["sha256"]
            ):
                raise GateFailure("download_evidence_mismatch")
            record = next((task for task in tasks if task["task_id"] == task_id), None)
            if record is None or record["outcome"] != "candidate_verified":
                raise GateFailure("case_task_outcome_mismatch")
        cancelled = json.loads((directory / "model-cancel.json").read_text())
        if cancelled.get("status") != "passed":
            raise GateFailure("cancellation_evidence_incomplete")
        observed.add(cancelled["task_id"])
        record = next((task for task in tasks if task["task_id"] == cancelled["task_id"]), None)
        if record is None or (
            record["outcome"] != "outcome_unknown" or record["error_code"] != "MODEL_CANCELLED"
        ):
            raise GateFailure("cancellation_task_outcome_mismatch")
        if observed != {task["task_id"] for task in tasks}:
            raise GateFailure("unexpected_task_identity")
    finally:
        reader.close()


def run_gate(directory: Path) -> tuple[int, dict[str, Any]]:
    """Always retain stage failures and attempt/resource inventory, with no automatic rerun."""
    report: dict[str, Any] = {
        "schema_version": 1,
        "task": "P1-01-B5",
        "started_at": datetime.now(UTC).isoformat(),
        "cases": list(CASES),
        "mode": "three_role",
        "attempt_policy": "one_browser_submission_per_case_no_test_retries",
        "status": "failed",
    }
    api = web = None
    stage = "prepare"
    api_log = web_log = None
    tracked_files = {
        name: (ROOT / "frontend" / name).read_bytes() for name in ("next-env.d.ts", "tsconfig.json")
    }
    child_cleanup: dict[str, str] = {}
    try:
        check_ports()
        private_model = read_environment(ROOT / "artifacts/local-service/model-env.json")
        from agent_workbench.run_store.store import RunStore

        state = directory / "state"
        store = RunStore(state)
        try:
            store.initialize()
        finally:
            store.close()
        node = shutil.which("node")
        npx = shutil.which("npx")
        if node is None or npx is None:
            raise GateFailure("node_runtime_missing")
        # The browser and build never inherit the credential file contents.
        env = {k: v for k, v in os.environ.items() if not k.startswith("AWB_MODEL_")}
        env.update(
            {
                "AWB_API_PORT": str(API_PORT),
                "AWB_FRONTEND_PORT": str(WEB_PORT),
                "PW_PORT": str(WEB_PORT),
                "AWB_STATE_DIR": str(state),
                "AWB_B5_LIVE": "1",
                "AWB_B5_ARTIFACT_DIR": str(directory),
                "AWB_NEXT_DIST_DIR": ".next-b5-" + directory.name,
                "PLAYWRIGHT_CHANNEL": "chrome",
            }
        )
        stage = "build"
        with (directory / "build.log").open("wb") as build_log:
            build_code = run_bounded(
                [node, "node_modules/next/dist/bin/next", "build"],
                cwd=ROOT / "frontend",
                env=env,
                output=build_log,
                timeout=240,
                cleanup=child_cleanup,
                stage="build",
            )
        if build_code:
            raise GateFailure("production_build_failed")
        stage = "services"
        api_log = (directory / "api.log").open("wb")
        web_log = (directory / "web.log").open("wb")
        api = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "agent_workbench.api:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(API_PORT),
                "--no-access-log",
            ],
            cwd=ROOT,
            env={**env, **private_model},
            stdout=api_log,
            stderr=api_log,
            start_new_session=True,
        )
        wait_ready(f"http://127.0.0.1:{API_PORT}/api/health", api)
        web = subprocess.Popen(
            [
                node,
                "node_modules/next/dist/bin/next",
                "start",
                "--hostname",
                "127.0.0.1",
                "--port",
                str(WEB_PORT),
            ],
            cwd=ROOT / "frontend",
            env=env,
            stdout=web_log,
            stderr=web_log,
            start_new_session=True,
        )
        wait_ready(f"http://127.0.0.1:{WEB_PORT}/api/health", web)
        stage = "browser"
        with (directory / "browser.log").open("wb") as browser_log:
            browser_code = run_bounded(
                [npx, "playwright", "test", "--config=playwright.b5.config.ts"],
                cwd=ROOT / "frontend",
                env=env,
                output=browser_log,
                timeout=1600,
                cleanup=child_cleanup,
                stage="browser",
            )
        report["browser_exit_code"] = browser_code
        if browser_code:
            raise GateFailure("browser_acceptance_failed")
        report["status"] = "passed"
    except GateFailure as error:
        report["failure_code"] = str(error)
    except Exception:
        # Exception messages may embed secrets in environment/configuration failures.
        report["failure_code"] = "stage_failed"
    finally:
        report["last_stage"] = stage
        report["process_cleanup"] = {
            "web": safe_stop_process(web),
            "api": safe_stop_process(api),
        }
        report["child_process_cleanup"] = child_cleanup
        for log in (api_log, web_log):
            if log is not None:
                try:
                    log.close()
                except OSError:
                    report["status"] = "failed"
        # Next generates this tracked file with distDir-specific type imports.
        # Restore exact pre-build bytes after both services stop.
        restored = {}
        for name, original in tracked_files.items():
            target = ROOT / "frontend" / name
            try:
                if not target.exists() or target.read_bytes() != original:
                    target.write_bytes(original)
                restored[name] = target.read_bytes() == original
            except OSError:
                restored[name] = False
                report["status"] = "failed"
        report["build_files_restored"] = restored
        report["next_env_restored"] = restored["next-env.d.ts"]
        try:
            report["resource_inventory"] = asyncio.run(inventory(directory / "state"))
        except Exception:
            report["resource_inventory"] = {"confirmed": False, "error": "inventory_unavailable"}
        if not report["resource_inventory"]["confirmed"]:
            report["status"] = "failed"
        if report["status"] == "passed":
            try:
                verify_browser_evidence(directory, report["resource_inventory"]["tasks"])
                report["artifact_cross_check"] = "passed"
            except Exception:
                report["artifact_cross_check"] = "failed"
                report["status"] = "failed"
        if any(v == "unconfirmed" for v in report["process_cleanup"].values()):
            report["status"] = "failed"
        report["finished_at"] = datetime.now(UTC).isoformat()
        write_json(directory / "report.json", report)
    return (0 if report["status"] == "passed" else 1), report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Authorize exactly one live gate")
    args = parser.parse_args()
    if not args.execute:
        parser.error("--execute is required; this gate spends real model budget")
    previous = os.umask(0o077)
    try:
        directory = ROOT / "artifacts/b5-live" / str(uuid4())
        directory.mkdir(mode=0o700, parents=True)
        code, report = run_gate(directory)
        print(json.dumps({"status": report["status"], "report": str(directory / "report.json")}))
        return code
    finally:
        os.umask(previous)


if __name__ == "__main__":
    raise SystemExit(main())
