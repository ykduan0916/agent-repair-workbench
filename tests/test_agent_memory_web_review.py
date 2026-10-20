"""Independent M3-R web binding, legacy compatibility and terminal projections."""

import asyncio
from uuid import uuid4

import pytest

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


@pytest.mark.parametrize("kind", ["analysis", "repair"])
def test_fingerprint_legacy_compatible_binding_versioned_and_strict(kind):
    fields = (
        {"case_name": "output_mismatch", "intent": "diagnose"}
        if kind == "analysis"
        else {"case_id": "output_mismatch", "mode": "three_role"}
    )
    model = AnalysisRequest if kind == "analysis" else RepairRequest
    raw = {"request_id": str(uuid4()), **fields}
    request = model.model_validate(raw)
    assert request.fingerprint() == digest(canonical({"version": 1, **raw}))
    binding = {"project_id": "project", "connection_id": "source", "allow_historical": False}
    bound = model.model_validate(raw | {"memory": binding})
    assert bound.fingerprint() == digest(canonical({"version": 2, **raw, "memory": binding}))
    for forbidden in ({"tool_id": "other"}, {"query": "PRIVATE"}, {"allow_historical": "true"}):
        with pytest.raises(ValueError):
            model.model_validate(raw | {"memory": binding | forbidden})


def test_analysis_binding_survives_reopen_and_conflicts(analysis_host):
    store, request, _, adapter, owner, fence = analysis_host
    binding = MemoryBinding(project_id="project", connection_id="source")
    request = request.model_copy(update={"memory": binding})
    registry = load_case(
        request.case_name,
        task_id=request.request_id,
        intent=request.intent,
        budget=agent_budget(binding, WEB_BUDGET),
    )
    runtime = AnalysisRuntime(registry, store, adapter)
    admitted = store.admit(
        request, runtime.initial_summary(), policy_sha256="a" * 64, owner=owner, fence=fence
    )
    other = type(store)(store.directory)
    try:
        assert other.existing(request).saved.summary.budget.total_tokens == 1_900_000
        for changed in (None, binding.model_copy(update={"allow_historical": True})):
            with pytest.raises(StoreError, match="request_conflict"):
                other.existing(request.model_copy(update={"memory": changed}))
    finally:
        other.close()
    assert admitted.claim is not None


def test_repair_binding_survives_reopen_and_conflicts(repair_host):
    store, request, task, owner, fence, _ = repair_host
    binding = MemoryBinding(project_id="project", connection_id="source")
    request = request.model_copy(update={"memory": binding})
    task = task.model_copy(update={"budget": agent_budget(binding, task.budget)})
    store.admit(request, task, policy_sha256="a" * 64, owner=owner, task_fence=fence)
    other = type(store)(store.directory)
    try:
        assert other.receipt(request.request_id).request.memory == binding
        assert other.receipt(request.request_id).saved.task.budget.total_tokens == 1_900_000
        for changed in (None, binding.model_copy(update={"project_id": "elsewhere"})):
            with pytest.raises(StoreError, match="request_conflict"):
                other.existing(request.model_copy(update={"memory": changed}))
    finally:
        other.close()


def test_cancel_before_prefetch_does_not_project_forever_pending(analysis_host):
    store, request, _, adapter, owner, fence = analysis_host
    binding = MemoryBinding(project_id="project", connection_id="source")
    request = request.model_copy(update={"memory": binding})
    registry = load_case(
        request.case_name,
        task_id=request.request_id,
        intent=request.intent,
        budget=agent_budget(binding, WEB_BUDGET),
    )
    runtime = AnalysisRuntime(registry, store, adapter)
    admitted = store.admit(
        request, runtime.initial_summary(), policy_sha256="a" * 64, owner=owner, fence=fence
    )
    store.request_cancel(request.request_id)
    result = asyncio.run(runtime.run_created(admitted.claim, admitted.saved, fence=fence))
    assert result.summary.state.outcome == "cancelled" and adapter.calls == 0
    memory = WebMemoryStore(store.directory)
    try:
        projected = usage(memory, "analysis", request.request_id)
        assert projected["state"] not in ("pending", "disabled")
        assert projected["references"] == [] and projected["uses"] == []
    finally:
        memory.close()


def test_cli_analysis_without_web_receipt_is_disabled_not_not_found(analysis_host):
    from agent_workbench.analysis_store.models import AnalysisEvent

    store, request, runtime, _, _, fence = analysis_host
    store.create(runtime.initial_summary(), AnalysisEvent(kind="created"), fence=fence)
    memory = WebMemoryStore(store.directory)
    try:
        assert usage(memory, "analysis", request.request_id) == {
            "state": "disabled",
            "references": [],
            "uses": [],
        }
        with pytest.raises(StoreError, match="not_found"):
            usage(memory, "analysis", str(uuid4()))
    finally:
        memory.close()


def test_cli_repair_without_web_receipt_is_disabled_not_not_found(repair_host):
    store, request, task, _, fence, _ = repair_host
    store.create_task(task, fence=fence)
    memory = WebMemoryStore(store.directory)
    try:
        assert usage(memory, "repair", request.request_id) == {
            "state": "disabled",
            "references": [],
            "uses": [],
        }
        with pytest.raises(StoreError, match="not_found"):
            usage(memory, "repair", str(uuid4()))
    finally:
        memory.close()


def test_full_recovery_releases_memory_bound_terminal_task(repair_host):
    from agent_workbench.mcp_config import ROOT
    from agent_workbench.web_repairs.recovery import WebRepairRecovery

    store, request, task, owner, fence, _ = repair_host
    binding = MemoryBinding(project_id="project", connection_id="source")
    request = request.model_copy(update={"memory": binding})
    task = task.model_copy(update={"budget": agent_budget(binding, task.budget)})
    admission = store.admit(request, task, policy_sha256="a" * 64, owner=owner, task_fence=fence)
    started = store.consume(
        admission.claim,
        admission.saved,
        case_id=request.case_id,
        mode=request.mode,
        policy_sha256="a" * 64,
        task_fence=fence,
    )
    store.finish_task(
        request.request_id,
        "failed",
        error_code="TOOL_FAILED",
        expected_revision=started.saved.task.revision,
        expected_sha256=started.saved.task_sha256,
        fence=fence,
    )
    assert store.receipt(request.request_id).active
    result = asyncio.run(
        WebRepairRecovery(store, ROOT).release_checked(
            request.request_id, owner=owner, fence=fence, apply=True
        )
    )
    assert result.released and result.recovery.error is None
    assert not store.receipt(request.request_id).active
    assert store.receipt(request.request_id).request.memory == binding
    assert store.active_id() is None
