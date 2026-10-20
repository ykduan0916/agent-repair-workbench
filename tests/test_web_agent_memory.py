"""M3 request identity, persisted admission, and no-memory compatibility."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

import tests.test_web_analysis_store as analysis_tests
import tests.test_web_repair_store as repair_tests
from agent_workbench.controlled_tools.resources import load_case
from agent_workbench.experience_memory.agent_models import MemoryBinding, agent_budget
from agent_workbench.mcp_config import canonical, digest
from agent_workbench.orchestration.runtime import AnalysisRuntime
from agent_workbench.run_store import StoreError
from agent_workbench.web_analyses.models import WEB_BUDGET, AnalysisRequest
from agent_workbench.web_memory.agent_api import usage
from agent_workbench.web_memory.store import WebMemoryStore
from agent_workbench.web_repairs.models import RepairRequest

analysis_host = analysis_tests.host
repair_host = repair_tests.host
BINDING = MemoryBinding(project_id="project", connection_id="local")


@pytest.mark.parametrize("kind", ["analysis", "repair"])
def test_no_memory_fingerprint_and_invalid_modes(kind):
    values = {"request_id": str(uuid4())}
    model = AnalysisRequest if kind == "analysis" else RepairRequest
    values.update(
        {"case_name": "output_mismatch", "intent": "diagnose"}
        if kind == "analysis"
        else {"case_id": "output_mismatch", "mode": "three_role"}
    )
    plain = model.model_validate(values)
    assert plain.fingerprint() == digest(canonical({"version": 1, **values}))
    assert model.model_validate({**values, "memory": None}).fingerprint() == plain.fingerprint()
    remembered = model.model_validate({**values, "memory": BINDING.model_dump()})
    assert remembered.fingerprint() != plain.fingerprint()
    forbidden = {"intent": "check_only"} if kind == "analysis" else {"mode": "single_role"}
    with pytest.raises(ValidationError):
        model.model_validate({**values, **forbidden, "memory": BINDING.model_dump()})
    with pytest.raises(ValidationError):
        model.model_validate(
            {**values, "memory": {**BINDING.model_dump(), "source_version": "fake"}}
        )


def test_analysis_binding_survives_read_and_rejects_changed_replay(analysis_host):
    store, original, _, adapter, owner, fence = analysis_host
    request = AnalysisRequest.model_validate({**original.model_dump(), "memory": BINDING})
    registry = load_case(
        request.case_name,
        task_id=request.request_id,
        intent=request.intent,
        budget=agent_budget(BINDING, WEB_BUDGET),
    )
    runtime = AnalysisRuntime(registry, store, adapter)
    first = store.admit(
        request, runtime.initial_summary(), policy_sha256="a" * 64, owner=owner, fence=fence
    )
    assert store.existing(request).saved.sha256 == first.saved.sha256
    assert first.saved.summary.budget.total_tokens == 1_900_000
    with pytest.raises(StoreError, match="request_conflict"):
        store.existing(original)
    with WebMemoryStore(store.directory)._transaction() as c:
        raw = c.exec_driver_sql("SELECT memory_binding FROM web_analysis_requests").scalar_one()
        assert MemoryBinding.model_validate_json(raw) == BINDING


def test_repair_binding_survives_read_and_fixed_budget(repair_host):
    store, original, task, owner, fence, _ = repair_host
    request = RepairRequest.model_validate({**original.model_dump(), "memory": BINDING})
    task = task.model_copy(update={"budget": agent_budget(BINDING, WEB_BUDGET)})
    store.admit(request, task, policy_sha256="a" * 64, owner=owner, task_fence=fence)
    receipt = store.receipt(request.request_id)
    assert receipt.request.memory == BINDING
    assert receipt.saved.task.budget.total_tokens == 1_900_000
    assert store.existing(request) is not None
    with pytest.raises(StoreError, match="request_conflict"):
        store.existing(original)
    memory = WebMemoryStore(store.directory)
    try:
        assert usage(memory, "repair", request.request_id)["state"] == "pending"
        with pytest.raises(StoreError, match="not_found"):
            usage(memory, "repair", str(uuid4()))
    finally:
        memory.close()


def test_unselected_task_reports_disabled_without_creating_binding(repair_host):
    store, request, task, owner, fence, _ = repair_host
    store.admit(request, task, policy_sha256="a" * 64, owner=owner, task_fence=fence)
    memory = WebMemoryStore(store.directory)
    try:
        assert usage(memory, "repair", request.request_id) == {
            "state": "disabled",
            "references": [],
            "uses": [],
        }
        with memory._transaction() as c:
            assert c.exec_driver_sql("SELECT count(*) FROM memory_task_bindings").scalar_one() == 0
    finally:
        memory.close()
