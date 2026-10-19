"""M2-R authoritative eligibility, staged projections and per-wire authorization."""

import json
from typing import Any

from pydantic import TypeAdapter
from sqlalchemy import Connection

from agent_workbench.run_store import StoreError

from .common import connection, encode, enqueue, head, key, one, project, sha, validate
from .engine_models import Grant, Policy, Query, Reference
from .models import Digest, DraftInput, Lease
from .store import MemoryStore


class EngineStore(MemoryStore):
    def bind_runtime(self, root_digest: str) -> None:
        TypeAdapter(Digest).validate_python(root_digest, strict=True)
        with self._transaction(write=True) as c:
            row = c.exec_driver_sql(
                "SELECT root_digest FROM memory_engine_config WHERE singleton=1"
            ).first()
            if row and row[0] != root_digest:
                raise StoreError("engine_root_changed")
            c.exec_driver_sql(
                "INSERT OR IGNORE INTO memory_engine_config VALUES(1,?)", (root_digest,)
            )

    def set_grant(self, project_id: str, grant: Grant, *, expected_version: int) -> int:
        grant = validate(Grant, grant)
        if type(expected_version) is not int or expected_version < 0:
            raise StoreError("invalid_version")
        with self._transaction(write=True) as c:
            connection(c, project_id, grant.connection_id, active=False)
            args = (project_id, grant.connection_id, grant.content_type, grant.purpose)
            row = c.exec_driver_sql(
                "SELECT version FROM memory_grants WHERE project_id=? AND connection_id=? "
                "AND content_type=? AND purpose=?",
                args,
            ).first()
            if (row[0] if row else 0) != expected_version:
                raise StoreError("version_conflict")
            version = expected_version + 1
            c.exec_driver_sql(
                "INSERT INTO memory_grants VALUES(?,?,?,?,?,?,?) ON CONFLICT "
                "(project_id,connection_id,content_type,purpose) DO UPDATE SET "
                "version=excluded.version,enabled=excluded.enabled,expires_at=excluded.expires_at",
                (*args, version, int(grant.enabled), grant.expires_at),
            )
            return version

    def set_policy(
        self,
        project_id: str,
        memory_id: str,
        policy: Policy,
        *,
        expected_generation: int,
        expected_version: int,
    ) -> int:
        policy = validate(Policy, policy)
        if any(type(v) is not int or v < 0 for v in (expected_generation, expected_version)):
            raise StoreError("invalid_version")
        with self._transaction(write=True) as c:
            item = head(c, project_id, memory_id)
            if item["version"] != expected_generation or item["state"] != "confirmed":
                raise StoreError("version_conflict")
            if item["kind"] == "project_constraint" and policy.conflict_key is None:
                raise StoreError("constraint_key_required")
            row = c.exec_driver_sql(
                "SELECT version FROM memory_policies WHERE memory_id=?",
                (memory_id,),
            ).first()
            if (row[0] if row else 0) != expected_version:
                raise StoreError("version_conflict")
            version = expected_version + 1
            c.exec_driver_sql(
                "INSERT INTO memory_policies VALUES(?,?,?,?) ON CONFLICT(memory_id) DO UPDATE "
                "SET version=excluded.version,generation=excluded.generation,"
                "payload=excluded.payload",
                (memory_id, version, expected_generation, encode(policy.model_dump())),
            )
            return version

    def _grant(self, c: Connection, project_id: str, source: str, kind: str, purpose: str) -> int:
        row = (
            c.exec_driver_sql(
                "SELECT * FROM memory_grants WHERE project_id=? AND connection_id=? "
                "AND content_type=? AND purpose=?",
                (project_id, source, kind, purpose),
            )
            .mappings()
            .first()
        )
        if row is None or not row["enabled"] or row["expires_at"] <= self._now():
            raise StoreError("authorization_required")
        return int(row["version"])

    def _ref(self, c: Connection, project_id: str, memory_id: str, purpose: str) -> dict[str, Any]:
        item = head(c, project_id, memory_id)
        if item["state"] != "confirmed":
            raise StoreError("memory_ineligible")
        rev = one(
            c,
            "SELECT * FROM memory_revisions WHERE memory_id=? AND revision=?",
            memory_id,
            item["revision"],
        )
        if rev["payload"] is None or sha(rev["payload"]) != rev["digest"]:
            raise StoreError("store_corrupt")
        body = DraftInput.model_validate_json(rev["payload"])
        source = connection(c, project_id, body.connection_id)
        policy_row = (
            c.exec_driver_sql(
                "SELECT * FROM memory_policies WHERE memory_id=?",
                (memory_id,),
            )
            .mappings()
            .first()
        )
        if policy_row is None or policy_row["generation"] != item["version"]:
            raise StoreError("policy_required")
        policy = Policy.model_validate_json(policy_row["payload"])
        if policy.expires_at <= self._now():
            raise StoreError("policy_expired")
        return {
            "memory_id": memory_id,
            "generation": item["version"],
            "revision": item["revision"],
            "digest": rev["digest"],
            "connection_id": body.connection_id,
            "event_id": body.event_id,
            "connection_version": source["version"],
            "kind": body.kind,
            "tool_id": body.tool_id,
            "source_version": body.source_version,
            "policy_version": policy_row["version"],
            "policy": policy.model_dump(),
            "grant_version": self._grant(c, project_id, body.connection_id, body.kind, purpose),
        }

    def _scope(self, c: Connection, project_id: str, query: dict[str, Any]) -> list[dict[str, Any]]:
        rows = (
            c.exec_driver_sql(
                "SELECT h.memory_id,r.payload,r.digest FROM memory_heads h JOIN memory_revisions r "
                "ON r.memory_id=h.memory_id AND r.revision=h.revision "
                "WHERE h.project_id=? AND h.state='confirmed' ORDER BY h.memory_id LIMIT 1001",
                (project_id,),
            )
            .mappings()
            .all()
        )
        if len(rows) > 1000:
            raise StoreError("scope_limit")
        refs: list[dict[str, Any]] = []
        constraints: dict[str, str] = {}
        for row in rows:
            if row["payload"] is None or sha(row["payload"]) != row["digest"]:
                raise StoreError("store_corrupt")
            content = DraftInput.model_validate_json(row["payload"])
            if content.tool_id != query["tool_id"]:
                continue
            ref = self._ref(c, project_id, row["memory_id"], "retrieve")
            policy = ref["policy"]
            exact = content.source_version == query["source_version"]
            if not exact and not (query["allow_historical"] and policy["allow_historical"]):
                continue
            ck, cv = policy["conflict_key"], policy["conflict_value"]
            if ck is not None:
                if ck in constraints and constraints[ck] != cv:
                    raise StoreError("scope_conflict")
                constraints[ck] = cv
            roots = (
                c.exec_driver_sql(
                    "SELECT r.root_id,r.dataset_id,r.data_id,r.profile_digest "
                    "FROM memory_engine_roots r "
                    "JOIN memory_jobs j ON j.job_id=r.root_id AND j.state='done' "
                    "WHERE r.project_id=? "
                    "AND r.memory_id=? AND r.generation=? AND r.state='ready' ORDER BY r.root_id",
                    (project_id, row["memory_id"], ref["generation"]),
                )
                .mappings()
                .all()
            )
            if not roots:
                raise StoreError("index_required")
            matching = [r for r in roots if r["profile_digest"] == query["profile_digest"]]
            if not matching:
                raise StoreError("embedding_profile_changed")
            ref.update(dict(matching[-1]), classification="exact" if exact else "historical_hint")
            refs.append(ref)
        if len(refs) > 8:
            raise StoreError("scope_limit")
        return refs

    def prepare_index(self, project_id: str, memory_id: str, *, profile_digest: str) -> str:
        TypeAdapter(Digest).validate_python(profile_digest, strict=True)
        with self._transaction(write=True) as c:
            ref = self._ref(c, project_id, memory_id, "index")
            job_id = enqueue(
                c,
                project_id,
                "project",
                ref["connection_id"],
                ref["event_id"],
                self._now(),
                memory_id,
                ref["generation"],
            )
            c.exec_driver_sql(
                "INSERT INTO memory_operations(job_id,operation,snapshot) VALUES(?, 'index', ?)",
                (job_id, encode({"refs": [ref], "profile_digest": profile_digest})),
            )
            c.exec_driver_sql(
                "INSERT INTO memory_engine_roots VALUES(?,?,?,?, 'staged',NULL,NULL,?)",
                (job_id, project_id, memory_id, ref["generation"], profile_digest),
            )
            return job_id

    def prepare_search(self, project_id: str, query: Query, *, profile_digest: str) -> str:
        TypeAdapter(Digest).validate_python(profile_digest, strict=True)
        query = validate(Query, query)
        with self._transaction(write=True) as c:
            source = connection(c, project_id, query.connection_id)
            one(
                c,
                "SELECT event_id FROM memory_events WHERE connection_id=? AND event_id=?",
                query.connection_id,
                query.event_id,
            )
            version = self._grant(c, project_id, query.connection_id, "query", "retrieve")
            scope = query.model_dump(exclude={"text"})
            scope["profile_digest"] = profile_digest
            snapshot = {
                "query": scope,
                "query_digest": sha(query.text),
                "query_grant": version,
                "connection_version": source["version"],
                "refs": self._scope(c, project_id, scope),
            }
            job_id = enqueue(
                c, project_id, "event", query.connection_id, query.event_id, self._now()
            )
            c.exec_driver_sql(
                "INSERT INTO memory_operations(job_id,operation,snapshot) VALUES(?, 'search', ?)",
                (job_id, encode(snapshot)),
            )
            return job_id

    def _check(self, c: Connection, project_id: str, lease: Lease) -> dict[str, Any]:
        self._owner(c, project_id, lease)
        op = one(c, "SELECT * FROM memory_operations WHERE job_id=?", lease.job_id)
        snapshot: dict[str, Any] = json.loads(op["snapshot"])
        if op["operation"] == "index":
            refs = [self._ref(c, project_id, lease.memory_id or "", "index")]
        else:
            query = snapshot["query"]
            source = connection(c, project_id, query["connection_id"])
            grant = self._grant(c, project_id, query["connection_id"], "query", "retrieve")
            if (
                grant != snapshot["query_grant"]
                or source["version"] != snapshot["connection_version"]
            ):
                raise StoreError("authorization_changed")
            refs = self._scope(c, project_id, query)
        if refs != snapshot["refs"]:
            raise StoreError("references_changed")
        return snapshot

    def _authorize_call(self, c: Connection, project_id: str, lease: Lease) -> None:
        self._check(c, project_id, lease)

    def snapshot(self, project_id: str, lease: Lease) -> dict[str, Any]:
        with self._transaction() as c:
            return self._check(c, project_id, lease)

    def publish_index(self, project_id: str, lease: Lease, dataset_id: str, data_id: str) -> None:
        key(dataset_id)
        key(data_id)
        with self._transaction(write=True) as c:
            snapshot = self._check(c, project_id, lease)
            ref = snapshot["refs"][0]
            root = one(c, "SELECT * FROM memory_engine_roots WHERE root_id=?", lease.job_id)
            if root["state"] != "staged":
                raise StoreError("projection_conflict")
            c.exec_driver_sql(
                "INSERT INTO memory_projections VALUES(?,?,?,?,?,?,?)",
                (
                    project_id,
                    ref["memory_id"],
                    ref["revision"],
                    ref["generation"],
                    ref["digest"],
                    dataset_id,
                    data_id,
                ),
            )
            c.exec_driver_sql(
                "UPDATE memory_engine_roots SET state='ready',dataset_id=?,data_id=? "
                "WHERE root_id=?",
                (dataset_id, data_id, lease.job_id),
            )

    def references(
        self, project_id: str, lease: Lease, evidence: list[tuple[str, str, str]]
    ) -> tuple[Reference, ...]:
        with self._transaction() as c:
            snapshot = self._check(c, project_id, lease)
            result, size = [], 0
            seen = set()
            for root_id, dataset, data in evidence:
                ref = next(
                    (
                        r
                        for r in snapshot["refs"]
                        if r.get("root_id") == root_id
                        and r.get("dataset_id") == dataset
                        and r.get("data_id") == data
                    ),
                    None,
                )
                if ref is None:
                    raise StoreError("unmapped_evidence")
                if ref["memory_id"] in seen:
                    continue
                seen.add(ref["memory_id"])
                revision = one(
                    c,
                    "SELECT payload FROM memory_revisions WHERE memory_id=? AND revision=?",
                    ref["memory_id"],
                    ref["revision"],
                )
                content = DraftInput.model_validate_json(revision["payload"])
                size += len(content.body.encode())
                if size > 8192:
                    raise StoreError("context_limit")
                result.append(
                    Reference(
                        **{
                            k: ref[k]
                            for k in (
                                "memory_id",
                                "revision",
                                "generation",
                                "digest",
                                "kind",
                                "connection_id",
                                "event_id",
                                "source_version",
                                "classification",
                            )
                        },
                        body=content.body,
                    )
                )
            return tuple(result)

    def roots_for_cleanup(self, project_id: str) -> list[str]:
        """Call only while holding the runtime's global writer lock."""
        with self._transaction(write=True) as c:
            project(c, project_id)
            roots = (
                c.exec_driver_sql(
                    "SELECT r.root_id,r.state,h.state AS head_state,h.version,r.generation,"
                    "j.state AS job_state "
                    "FROM memory_engine_roots r JOIN memory_heads h ON r.memory_id=h.memory_id "
                    "JOIN memory_jobs j ON j.job_id=r.root_id "
                    "WHERE r.project_id=? AND r.state!='removed'",
                    (project_id,),
                )
                .mappings()
                .all()
            )
            ids = []
            for row in roots:
                if (
                    row["head_state"] != "confirmed"
                    or row["version"] != row["generation"]
                    or row["state"] in ("staged", "removing")
                    or row["job_state"] != "done"
                ):
                    c.exec_driver_sql(
                        "UPDATE memory_engine_roots SET state='removing' WHERE root_id=?",
                        (row["root_id"],),
                    )
                    ids.append(str(row["root_id"]))
            return ids

    def removed_root(self, project_id: str, root_id: str) -> None:
        with self._transaction(write=True) as c:
            row = one(
                c,
                "SELECT state FROM memory_engine_roots WHERE project_id=? AND root_id=?",
                key(project_id),
                key(root_id),
            )
            if row["state"] != "removing":
                raise StoreError("projection_conflict")
            c.exec_driver_sql(
                "UPDATE memory_engine_roots SET state='removed' WHERE root_id=?", (root_id,)
            )

    def record_failure(self, project_id: str, lease: Lease, code: str) -> None:
        allowed = {
            "embedding_invalid",
            "provider_timeout",
            "provider_http_error",
            "usage_missing",
            "usage_overrun",
            "provider_transport_error",
            "request_rejected",
            "authority_changed",
            "engine_failed",
            "engine_stopped",
            "engine_contract_changed",
        }
        if code not in allowed:
            raise StoreError("invalid_failure_code")
        with self._transaction(write=True) as c:
            job = one(
                c,
                "SELECT lease_hash FROM memory_jobs WHERE project_id=? AND job_id=?",
                key(project_id),
                lease.job_id,
            )
            if job["lease_hash"] != sha(lease.token):
                raise StoreError("lease_conflict")
            c.exec_driver_sql(
                "UPDATE memory_operations SET failure_code=COALESCE(failure_code,?) WHERE job_id=?",
                (code, lease.job_id),
            )

    def accounting(self, project_id: str, job_id: str) -> dict[str, Any]:
        with self._transaction() as c:
            job = one(
                c,
                "SELECT state,tokens FROM memory_jobs WHERE project_id=? AND job_id=?",
                key(project_id),
                key(job_id),
            )
            calls = (
                c.exec_driver_sql(
                    "SELECT call_id,kind,state,reserved_tokens,actual_tokens "
                    "FROM memory_calls WHERE job_id=?",
                    (job_id,),
                )
                .mappings()
                .all()
            )
            return {
                "state": job["state"],
                "reserved_tokens": job["tokens"],
                "calls": [dict(row) for row in calls],
                "monetary_cost": "unknown",
                "failure_code": one(
                    c, "SELECT failure_code FROM memory_operations WHERE job_id=?", job_id
                )["failure_code"],
            }
