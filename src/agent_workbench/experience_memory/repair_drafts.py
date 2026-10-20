"""BH-06: explicit, atomic experience drafts from current accepted repair evidence."""

from pathlib import Path
from typing import Annotated, Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import Field
from sqlalchemy import Connection

from agent_workbench.candidates.contracts import CandidateError
from agent_workbench.mcp_config import canonical, digest
from agent_workbench.repair_cases.bindings import validate_baseline_binding
from agent_workbench.repair_store.decisions import DecisionStore
from agent_workbench.repair_store.fencing import RepairFence
from agent_workbench.run_store import StoreError
from agent_workbench.run_store.store import _uuid

from .common import connection, encode, enqueue, head, key, sha, validate
from .models import Contract, Digest, DraftInput, EventInput, Key, Receipt
from .store import MemoryStore


class RepairDraftInput(Contract):
    request_id: Key
    project_id: Key
    connection_id: Key
    expected_decision_revision: Annotated[int, Field(ge=1, le=255)]
    expected_candidate_sha256: Digest
    expected_comparison_sha256: Digest


class RepairDraftStore(DecisionStore):
    def _proof(
        self, c: Connection, task_id: str, candidate_id: str, project_id: str, image_root: Path
    ) -> dict[str, Any]:
        row = (
            c.exec_driver_sql(
                "SELECT * FROM memory_repair_drafts WHERE task_id=? "
                "AND candidate_id=? AND project_id=?",
                (task_id, candidate_id, key(project_id)),
            )
            .mappings()
            .first()
        )
        if row is None:
            raise StoreError("not_found")
        item = head(c, project_id, row["memory_id"])
        task, _, candidates = self._read(c, task_id)
        candidate = candidates.get(candidate_id)
        comparison = self._read_comparison(c, task_id, candidate_id)
        decision = self._read_decision(c, task_id, candidate_id)
        valid = False
        if candidate and comparison and decision and decision.decision == "accepted":
            valid = self._decision_current(decision, candidate, comparison)
            if valid:
                try:
                    self._verified(task, candidate, comparison, image_root)
                except (StoreError, CandidateError, OSError, ValueError):
                    valid = False
        return {
            "project_id": project_id,
            "task_id": task_id,
            "candidate_id": candidate_id,
            "memory_id": row["memory_id"],
            "memory_url": f"/workspace/memory?project={project_id}&memory={row['memory_id']}",
            "state": item["state"],
            "saved_decision_revision": row["decision_revision"],
            "current_decision": decision.decision if decision else None,
            "current_decision_revision": self._global_revision(c, task_id),
            "source_current_valid": valid,
            "candidate_sha256": row["candidate_sha256"],
            "comparison_sha256": row["comparison_sha256"],
        }

    def proof(
        self, task_id: str, candidate_id: str, project_id: str, *, image_root: Path
    ) -> dict[str, Any]:
        _uuid(task_id)
        _uuid(candidate_id)
        with RepairFence(self.directory, task_id) as fence:
            with self._base._transaction() as c:
                fence.require(self.directory, task_id)
                return self._proof(c, task_id, candidate_id, project_id, image_root)

    def memory_source(self, project_id: str, memory_id: str, *, image_root: Path) -> dict[str, Any]:
        with self._base._transaction() as c:
            head(c, project_id, memory_id)
            row = c.exec_driver_sql(
                "SELECT task_id,candidate_id FROM memory_repair_drafts "
                "WHERE project_id=? AND memory_id=?",
                (project_id, memory_id),
            ).first()
        if row is None:
            return {"source": None}
        return {"source": self.proof(row[0], row[1], project_id, image_root=image_root)}

    def save_verified_repair_draft(
        self, task_id: str, candidate_id: str, payload: RepairDraftInput, *, image_root: Path
    ) -> dict[str, Any]:
        _uuid(task_id)
        _uuid(candidate_id)
        payload = validate(RepairDraftInput, payload)
        fingerprint = sha(
            encode(
                [
                    "verified_repair",
                    task_id,
                    candidate_id,
                    payload.model_dump(mode="json", exclude={"request_id"}),
                ]
            )
        )
        with RepairFence(self.directory, task_id) as fence:
            with self._base._transaction(write=True) as c:
                fence.require(self.directory, task_id)
                prior = MemoryStore._prior(c, payload.project_id, payload.request_id, fingerprint)
                existing = (
                    c.exec_driver_sql(
                        "SELECT * FROM memory_repair_drafts WHERE task_id=? AND candidate_id=?",
                        (task_id, candidate_id),
                    )
                    .mappings()
                    .first()
                )
                if existing:
                    if existing["fingerprint"] != fingerprint:
                        raise StoreError("idempotency_conflict")
                    if prior is None:
                        receipt = MemoryStore._receipt(
                            head(c, payload.project_id, existing["memory_id"])
                        )
                        MemoryStore._save_receipt(
                            c, payload.project_id, payload.request_id, fingerprint, receipt
                        )
                    return self._proof(c, task_id, candidate_id, payload.project_id, image_root)
                if prior is not None:
                    raise StoreError("store_corrupt")
                source = connection(c, payload.project_id, payload.connection_id)
                if source["origin"] not in ("local_user", "synthetic"):
                    raise StoreError("local_source_required")
                task, _, candidates = self._read(c, task_id)
                candidate = candidates.get(candidate_id)
                comparison = self._read_comparison(c, task_id, candidate_id)
                decision = self._read_decision(c, task_id, candidate_id)
                if (
                    candidate is None
                    or candidate.status != "committed"
                    or candidate.snapshot is None
                ):
                    raise StoreError("candidate_not_ready")
                if comparison is None or decision is None or decision.decision != "accepted":
                    raise StoreError("accepted_candidate_required")
                candidate_sha = candidate.snapshot.tree_sha256
                comparison_sha = digest(canonical(comparison.model_dump(mode="json")))
                if (
                    payload.expected_decision_revision != self._global_revision(c, task_id)
                    or payload.expected_candidate_sha256 != candidate_sha
                    or payload.expected_comparison_sha256 != comparison_sha
                    or not self._decision_current(decision, candidate, comparison)
                ):
                    raise StoreError("decision_stale")
                self._verified(task, candidate, comparison, image_root)
                profile = validate_baseline_binding(task.baseline)
                if (
                    c.exec_driver_sql(
                        "SELECT count(*) FROM memory_heads WHERE project_id=?",
                        (payload.project_id,),
                    ).scalar_one()
                    >= 10000
                ):
                    raise StoreError("memory_limit")
                if (
                    c.exec_driver_sql(
                        "SELECT count(*) FROM memory_events WHERE connection_id=?",
                        (payload.connection_id,),
                    ).scalar_one()
                    >= 10000
                ):
                    raise StoreError("event_limit")
                event_id = str(
                    uuid5(NAMESPACE_URL, f"awb:accepted-repair:{task_id}:{candidate_id}")
                )
                event = EventInput(
                    event_id=event_id,
                    event_type="test_completed",
                    source_run_id=task_id,
                    occurred_at=self._base._now(),
                    outcome="passed",
                    artifact_digests=(candidate_sha, comparison_sha),
                )
                raw = encode(event.model_dump(mode="json"))
                # This namespace is internal; an existing external event cannot supply the proof.
                if c.exec_driver_sql(
                    "SELECT 1 FROM memory_events WHERE connection_id=? AND event_id=?",
                    (payload.connection_id, event_id),
                ).first():
                    raise StoreError("event_conflict")
                content = DraftInput(
                    kind="repair_experience",
                    connection_id=payload.connection_id,
                    event_id=event_id,
                    tool_id="lookup",
                    source_version=profile.source_sha256,
                    title=f"Verified repair: {profile.case_id}",
                    body=(
                        f"Controlled synthetic case: {profile.case_id}. Tool: lookup. "
                        f"Source version: {profile.source_sha256}. "
                        "Modified path: source/server.py. "
                        f"Candidate tree: {candidate_sha}. Frozen comparison: {comparison_sha}. "
                        "This candidate passed that frozen regression "
                        "and was accepted by the user. "
                        "It does not prove future versions pass. The proof supports the observed "
                        "result, not a diagnosis of root cause or a reusable patch."
                    ),
                )
                memory_id = str(uuid4())
                c.exec_driver_sql(
                    "INSERT INTO memory_events VALUES(?,?,?,?,?)",
                    (payload.connection_id, event_id, sha(raw), raw, self._base._now()),
                )
                c.exec_driver_sql(
                    "INSERT INTO memory_heads VALUES(?,?,?,1,1,'draft',?)",
                    (memory_id, payload.project_id, "repair_experience", self._base._now()),
                )
                MemoryStore._revision(c, memory_id, 1, content)
                enqueue(
                    c,
                    payload.project_id,
                    "project",
                    payload.connection_id,
                    event_id,
                    self._base._now(),
                    memory_id,
                    1,
                )
                receipt = Receipt(resource_id=memory_id, version=1, revision=1, state="draft")
                MemoryStore._save_receipt(
                    c, payload.project_id, payload.request_id, fingerprint, receipt
                )
                c.exec_driver_sql(
                    "INSERT INTO memory_repair_drafts VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        task_id,
                        candidate_id,
                        payload.project_id,
                        memory_id,
                        payload.request_id,
                        fingerprint,
                        payload.expected_decision_revision,
                        candidate_sha,
                        comparison_sha,
                        self._base._now(),
                    ),
                )
                return self._proof(c, task_id, candidate_id, payload.project_id, image_root)
