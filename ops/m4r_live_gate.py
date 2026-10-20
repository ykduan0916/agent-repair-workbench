"""M4-R synthetic real HTTP -> Cognee gate, isolated state, retained attempts."""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx  # noqa: E402

from agent_workbench.experience_memory.engine_models import Grant, Policy  # noqa: E402
from agent_workbench.experience_memory.models import DraftInput, EventInput  # noqa: E402
from agent_workbench.web_memory.engine import EngineWebStore  # noqa: E402
from ops.local_service import read_environment  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", required=True)
    parser.parse_args()
    os.umask(0o077)
    folder = ROOT / "artifacts/m4r" / str(uuid4())
    folder.mkdir(parents=True, mode=0o700)
    store = EngineWebStore(folder / "state")
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
    receipt = store.create_memory(
        "synthetic",
        "create",
        DraftInput(
            kind="repair_experience",
            connection_id="fixture",
            event_id="fixture",
            tool_id="count",
            source_version="v1",
            title="Synthetic output type repair",
            body=(
                "Synthetic count tool test: converting a string count to an integer "
                "fixes the output type mismatch. Check the output schema and run the "
                "frozen regression before accepting a candidate."
            ),
        ),
    )
    receipt = store.change_memory("synthetic", receipt.resource_id, "confirm", 1, state="confirmed")
    store.set_policy(
        "synthetic",
        receipt.resource_id,
        Policy(expires_at=time.time() + 3600),
        expected_generation=receipt.version,
        expected_version=0,
    )
    for kind, purpose in [
        ("repair_experience", "index"),
        ("repair_experience", "retrieve"),
        ("query", "retrieve"),
    ]:
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
    env = {
        **os.environ,
        **read_environment(ROOT / "artifacts/local-service/model-env.json"),
        "AWB_STATE_DIR": str(folder / "state"),
        "AWB_FRONTEND_PORT": "3019",
    }
    report = {"gate": "m4-real-engine", "status": "failed", "synthetic": True, "operations": []}
    child = None
    try:
        child = subprocess.Popen(
            [
                str(ROOT / ".venv/bin/python"),
                "-m",
                "uvicorn",
                "agent_workbench.api:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8039",
                "--no-access-log",
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        with httpx.Client(
            base_url="http://127.0.0.1:8039",
            trust_env=False,
            timeout=10,
            headers={"Origin": "http://127.0.0.1:3019", "X-AWB-Local": "1"},
        ) as client:
            for _ in range(100):
                try:
                    if client.get("/api/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                raise ValueError("api_unavailable")
            for operation in ["index", "search", "cleanup"]:
                request = {"request_id": str(uuid4()), "operation": operation}
                if operation == "index":
                    request["memory_id"] = receipt.resource_id
                if operation == "search":
                    request["query"] = {
                        "connection_id": "fixture",
                        "event_id": "fixture",
                        "tool_id": "count",
                        "source_version": "v1",
                        "text": "How was the synthetic count output type mismatch fixed?",
                        "allow_historical": False,
                    }
                if operation == "cleanup":
                    store.change_memory(
                        "synthetic", receipt.resource_id, "revoke", receipt.version, state="revoked"
                    )
                response = client.post("/api/memory/projects/synthetic/engine", json=request)
                if response.status_code not in (200, 202):
                    raise ValueError("admission_failed")
                deadline = time.monotonic() + 210
                while time.monotonic() < deadline:
                    current = client.get(
                        "/api/memory/projects/synthetic/engine/" + request["request_id"]
                    )
                    current.raise_for_status()
                    value = current.json()
                    if value["state"] != "running":
                        break
                    time.sleep(0.3)
                else:
                    raise ValueError("operation_timeout")
                report["operations"].append(value)
                (folder / "acceptance.json").write_text(json.dumps(report, indent=2))
                if value["state"] != "done":
                    raise ValueError("operation_unknown")
                if operation == "search" and (
                    value["result"]["status"] != "available" or not value["result"]["references"]
                ):
                    raise ValueError("reference_missing")
                if operation == "cleanup" and value["result"]["removed_roots"] != 1:
                    raise ValueError("cleanup_incomplete")
            report["status"] = "passed"
    except Exception:
        report["failure"] = "gate_incomplete"
    finally:
        if child:
            child.terminate()
            try:
                child.wait(timeout=45)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
        store.close()
        (folder / "acceptance.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"status": report["status"], "report": str(folder / "acceptance.json")}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
