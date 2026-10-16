"""B4 concurrency probes: the store lock/CAS must choose one writer."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

import tests.agents.test_repair_tool_bridge as tool_bridge
import tests.test_b4_decisions as decision_tests
import tests.test_repair_reader as reader_tests
from agent_workbench.mcp_config import canonical, digest
from agent_workbench.repair_store.decisions import DecisionStore
from agent_workbench.run_store.models import StoreError

completed = reader_tests.completed
ctx = tool_bridge.ctx


def test_two_workers_same_task_revision_choose_one_writer(completed):
    ctx = completed
    _, candidate, comparison = decision_tests.context(ctx)
    sha = candidate.snapshot.tree_sha256
    comparison_sha = digest(canonical(comparison.model_dump(mode="json")))

    def write(choice: str):
        store = DecisionStore(ctx.store.directory)
        try:
            return store.record(
                ctx.task_id,
                candidate.candidate_id,
                request_id=str(uuid4()),
                decision=choice,
                expected_revision=0,
                expected_candidate_sha256=sha,
                expected_comparison_sha256=comparison_sha,
                image_root=ctx.image,
            )
        except StoreError as error:
            return error.code
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ("accepted", "rejected")))
    assert sum(not isinstance(result, str) for result in results) == 1
    assert sum(result in {"decision_conflict", "repair_busy"} for result in results) == 1


def test_bounded_approval_worker_returns_busy_without_store_call(monkeypatch):
    from agent_workbench.web_repairs import approval

    held = [approval._WORKERS.acquire(blocking=False) for _ in range(4)]
    assert all(held)
    try:
        with pytest.raises(StoreError, match="approval_busy"):
            import asyncio

            asyncio.run(approval._bounded(lambda: None))
    finally:
        for _ in held:
            approval._WORKERS.release()
