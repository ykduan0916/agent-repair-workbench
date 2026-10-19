"""M2-R opt-in synthetic true SDK/model/embedding acceptance; preserves every attempt."""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent_workbench.experience_memory import DraftInput, EventInput  # noqa: E402
from agent_workbench.experience_memory.engine_models import Grant, Policy, Query  # noqa: E402
from agent_workbench.experience_memory.engine_store import EngineStore  # noqa: E402
from agent_workbench.providers.cognee.runtime import CogneeRuntime  # noqa: E402
from ops.memory_engine import load_providers  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", required=True)
    parser.parse_args()
    os.umask(0o077)
    folder = ROOT / "artifacts/m2r" / uuid4().hex
    folder.mkdir(mode=0o700, parents=True)
    report = {"kind": "synthetic_real_cognee_model_embedding", "status": "failed"}
    store = EngineStore(folder / "authority")
    store.initialize()
    runtime = None
    try:
        providers = load_providers(
            ROOT / "artifacts/local-service/model-env.json",
            ROOT / "artifacts/mr01/embedding-env.json",
            profile=True,
        )
        runtime = CogneeRuntime(
            store, folder / "engine", ROOT / "artifacts/mr01/venv/bin/python", providers
        )
        store.register_project("synthetic")
        store.register_connection("synthetic", "fixture", "synthetic")
        store.accept_event(
            "synthetic",
            "fixture",
            EventInput(
                event_id="failure",
                event_type="test_completed",
                source_run_id="fixture_run",
                occurred_at=time.time(),
                outcome="failed",
            ),
        )
        for kind, purpose in (
            ("repair_experience", "index"),
            ("repair_experience", "retrieve"),
            ("query", "retrieve"),
        ):
            store.set_grant(
                "synthetic",
                Grant(
                    connection_id="fixture",
                    content_type=kind,
                    purpose=purpose,
                    enabled=True,
                    expires_at=time.time() + 1800,
                ),
                expected_version=0,
            )
        draft = store.create_memory(
            "synthetic",
            "create",
            DraftInput(
                kind="repair_experience",
                connection_id="fixture",
                event_id="failure",
                tool_id="count",
                source_version="v1",
                title="Synthetic count output repair",
                body="Synthetic repair experience. "
                "The count tool returned a string instead of an integer. "
                "Converting the count to an integer fixed the synthetic output contract test. "
                "This is synthetic test data, not a real user incident.",
            ),
        )
        item = store.change_memory("synthetic", draft.resource_id, "confirm", 1, state="confirmed")
        store.set_policy(
            "synthetic",
            item.resource_id,
            Policy(expires_at=time.time() + 1800, allow_historical=True),
            expected_generation=2,
            expected_version=0,
        )
        index_id = runtime.index("synthetic", item.resource_id)
        report["index_job"] = index_id
        q = Query(
            connection_id="fixture",
            event_id="failure",
            tool_id="count",
            source_version="v2",
            text="The count tool returns a string. What previous repair is relevant?",
            allow_historical=True,
        )
        found = runtime.search("synthetic", q)
        report["retrieval"] = found.model_dump(exclude={"references": {"__all__": {"body"}}})
        if found.status != "available" or len(found.references) != 1:
            raise ValueError("retrieval_gate")
        if found.references[0].classification != "historical_hint":
            raise ValueError("classification_gate")
        store.change_memory("synthetic", item.resource_id, "revoke", 2, state="revoked")
        revoked = runtime.search("synthetic", q)
        report["after_revocation"] = revoked.model_dump()
        if revoked.references:
            raise ValueError("revocation_gate")
        report["removed_roots"] = runtime.cleanup("synthetic")
        if report["removed_roots"] != 1 or (runtime.directory / index_id).exists():
            raise ValueError("cleanup_gate")
        report["status"] = "passed"
    except Exception:
        report["failure"] = "acceptance_incomplete"
    finally:
        try:
            with store._transaction() as c:
                jobs = [r[0] for r in c.exec_driver_sql("SELECT job_id FROM memory_operations")]
            report["accounting"] = {job: store.accounting("synthetic", job) for job in jobs}
        except Exception:
            report.update(status="failed", accounting_error="accounting_unavailable")
        try:
            if runtime is not None and report["status"] != "passed":
                report["failed_roots_removed"] = runtime.cleanup("synthetic")
        except Exception:
            report.update(status="failed", cleanup_error="cleanup_incomplete")
        finally:
            (folder / "acceptance.json").write_text(
                json.dumps(report, indent=2, ensure_ascii=False)
            )
            store.close()
    print(json.dumps({"artifact": str(folder / "acceptance.json"), "status": report["status"]}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
