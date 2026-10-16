"""B4 candidate approval: deterministic ledger and exact authorized downloads."""

import pytest

import tests.agents.test_repair_tool_bridge as tool_bridge
import tests.test_repair_reader as reader_tests
from agent_workbench.mcp_config import canonical, digest
from agent_workbench.repair_store.decisions import DecisionStore
from agent_workbench.run_store.models import StoreError

completed = reader_tests.completed
ctx = tool_bridge.ctx


def context(ctx):
    store = DecisionStore(ctx.store.directory)
    candidates = store.candidates(ctx.task_id)
    comparisons = store.comparisons(ctx.task_id)
    candidate = candidates[0]
    comparison = comparisons[0]
    return store, candidate, comparison


def test_accept_is_cas_idempotent_and_download_is_exact(completed):
    ctx = completed
    store, candidate, comparison = context(ctx)
    try:
        before = store.decision(ctx.task_id, candidate.candidate_id)
        assert before.decision is None
        candidate_sha = candidate.snapshot.tree_sha256
        comparison_sha = digest(canonical(comparison.model_dump(mode="json")))
        receipt = store.record(
            ctx.task_id,
            candidate.candidate_id,
            request_id="a0000000-0000-4000-8000-000000000001",
            decision="accepted",
            expected_revision=0,
            expected_candidate_sha256=candidate_sha,
            expected_comparison_sha256=comparison_sha,
            image_root=ctx.image,
        )
        assert receipt.decision and receipt.decision.decision == "accepted"
        repeat = store.record(
            ctx.task_id,
            candidate.candidate_id,
            request_id="a0000000-0000-4000-8000-000000000001",
            decision="accepted",
            expected_revision=0,
            expected_candidate_sha256=candidate_sha,
            expected_comparison_sha256=comparison_sha,
            image_root=ctx.image,
        )
        assert repeat.decision == receipt.decision
        name, patch = store.download(ctx.task_id, candidate.candidate_id, ctx.image)
        assert name.endswith(".patch") and b'+    return {"count": count}' in patch
    finally:
        store.close()


def test_decision_conflict_and_repeated_change_are_visible(completed):
    ctx = completed
    store, candidate, comparison = context(ctx)
    try:
        sha = candidate.snapshot.tree_sha256
        comp_sha = digest(canonical(comparison.model_dump(mode="json")))
        first = store.record(
            ctx.task_id,
            candidate.candidate_id,
            request_id="a0000000-0000-4000-8000-000000000002",
            decision="rejected",
            expected_revision=0,
            expected_candidate_sha256=sha,
            expected_comparison_sha256=comp_sha,
            image_root=ctx.image,
        )
        with pytest.raises(StoreError, match="decision_conflict"):
            store.record(
                ctx.task_id,
                candidate.candidate_id,
                request_id="a0000000-0000-4000-8000-000000000003",
                decision="accepted",
                expected_revision=0,
                expected_candidate_sha256=sha,
                expected_comparison_sha256=comp_sha,
                image_root=ctx.image,
            )
        second = store.record(
            ctx.task_id,
            candidate.candidate_id,
            request_id="a0000000-0000-4000-8000-000000000003",
            decision="accepted",
            expected_revision=first.decision.revision,
            expected_candidate_sha256=sha,
            expected_comparison_sha256=comp_sha,
            image_root=ctx.image,
        )
        assert [event.decision for event in second.decision.history] == ["rejected", "accepted"]
    finally:
        store.close()


def test_tampered_versions_block_acceptance_and_download(completed):
    ctx = completed
    store, candidate, comparison = context(ctx)
    try:
        sha = candidate.snapshot.tree_sha256
        comp_sha = digest(canonical(comparison.model_dump(mode="json")))
        path = ctx.snapshots.root / candidate.candidate_id / "server.py"
        path.chmod(0o600)
        with pytest.raises(StoreError, match="version_mismatch"):
            store.record(
                ctx.task_id,
                candidate.candidate_id,
                request_id="a0000000-0000-4000-8000-000000000004",
                decision="accepted",
                expected_revision=0,
                expected_candidate_sha256=sha,
                expected_comparison_sha256=comp_sha,
                image_root=ctx.image,
            )
        with pytest.raises(StoreError, match="download_not_authorized"):
            store.download(ctx.task_id, candidate.candidate_id, ctx.image)
    finally:
        store.close()


def test_old_request_is_idempotent_after_later_revision_and_history_is_bounded(completed):
    ctx = completed
    store, candidate, comparison = context(ctx)
    try:
        sha = candidate.snapshot.tree_sha256
        comp_sha = digest(canonical(comparison.model_dump(mode="json")))
        first_id = "a0000000-0000-4000-8000-000000000010"
        first = store.record(
            ctx.task_id,
            candidate.candidate_id,
            request_id=first_id,
            decision="accepted",
            expected_revision=0,
            expected_candidate_sha256=sha,
            expected_comparison_sha256=comp_sha,
            image_root=ctx.image,
        )
        current = first
        for index in range(11):
            current = store.record(
                ctx.task_id,
                candidate.candidate_id,
                request_id=f"a0000000-0000-4000-8000-{index + 20:012d}",
                decision="rejected" if index % 2 == 0 else "accepted",
                expected_revision=index + 1,
                expected_candidate_sha256=sha,
                expected_comparison_sha256=comp_sha,
                image_root=ctx.image,
            )
        assert current.revision == 12
        assert [item.revision for item in current.decision.history] == list(range(5, 13))
        replay = store.record(
            ctx.task_id,
            candidate.candidate_id,
            request_id=first_id,
            decision="accepted",
            expected_revision=0,
            expected_candidate_sha256=sha,
            expected_comparison_sha256=comp_sha,
            image_root=ctx.image,
        )
        assert replay.decision is not None
        assert replay.decision.request_id == first_id
        assert replay.decision.revision == 1
        assert replay.revision == 12
    finally:
        store.close()
