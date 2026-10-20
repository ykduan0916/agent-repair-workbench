"""M4-R durable one-shot engine operations; no replay after unknown outcomes."""

import asyncio
import fcntl
import json
import os
import stat
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import model_validator

from agent_workbench.experience_memory.common import encode, key, one, project, sha
from agent_workbench.experience_memory.engine_models import Query
from agent_workbench.experience_memory.models import Contract, Key
from agent_workbench.model_gateway.gateway import Config
from agent_workbench.providers.cognee.gateway import Providers
from agent_workbench.providers.cognee.runtime import CogneeRuntime
from agent_workbench.run_store import StoreError

from .store import WebMemoryStore


class EngineRequest(Contract):
    request_id: Key
    operation: Literal["index", "search", "cleanup"]
    memory_id: Key | None = None
    query: Query | None = None

    @model_validator(mode="after")
    def shape(self) -> "EngineRequest":
        if (self.operation == "index") != (self.memory_id is not None) or (
            self.operation == "search"
        ) != (self.query is not None):
            raise ValueError("invalid_operation")
        return self


class EngineWebStore(WebMemoryStore):
    def web_operation(self, project_id: str, request_id: str) -> dict[str, Any]:
        key(request_id)
        with self._transaction() as c:
            project(c, project_id)
            row = one(
                c,
                "SELECT * FROM memory_web_operations WHERE project_id=? AND request_id=?",
                project_id,
                request_id,
            )
            result = json.loads(row["result"]) if row["result"] is not None else None
            report = {
                "request_id": request_id,
                "operation": row["operation"],
                "state": row["state"],
                "result": result,
                "job_id": row["job_id"],
            }
        if row["job_id"]:
            report["accounting"] = self.accounting(project_id, row["job_id"])
        return report

    def admit_operation(self, project_id: str, request: EngineRequest) -> bool:
        fingerprint = sha(encode(request.model_dump(mode="json")))
        with self._transaction(write=True) as c:
            project(c, project_id)
            old = c.exec_driver_sql(
                "SELECT fingerprint FROM memory_web_operations WHERE project_id=? AND request_id=?",
                (project_id, request.request_id),
            ).first()
            if old:
                if old[0] != fingerprint:
                    raise StoreError("idempotency_conflict")
                return False
            if (
                c.exec_driver_sql(
                    "SELECT count(*) FROM memory_web_operations WHERE project_id=?", (project_id,)
                ).scalar_one()
                >= 10000
            ):
                raise StoreError("operation_limit")
            c.exec_driver_sql(
                "INSERT INTO memory_web_operations(project_id,request_id,fingerprint,operation,"
                "state,result,created_at,job_id) VALUES(?,?,?,?,'running',NULL,?,NULL)",
                (project_id, request.request_id, fingerprint, request.operation, self._now()),
            )
            return True

    def attach_job(self, project_id: str, request_id: str, job_id: str) -> None:
        with self._transaction(write=True) as c:
            one(
                c,
                "SELECT job_id FROM memory_jobs WHERE project_id=? AND job_id=?",
                project_id,
                job_id,
            )
            result = c.exec_driver_sql(
                "UPDATE memory_web_operations SET job_id=? WHERE project_id=? AND request_id=? "
                "AND state='running' AND job_id IS NULL",
                (job_id, project_id, request_id),
            )
            if result.rowcount != 1:
                raise StoreError("operation_changed")

    def finish_operation(
        self, project_id: str, request_id: str, result: dict[str, Any], *, unknown: bool = False
    ) -> None:
        raw = encode(result)
        if len(raw) > 65536:
            raise StoreError("result_limit")
        with self._transaction(write=True) as c:
            updated = c.exec_driver_sql(
                "UPDATE memory_web_operations SET state=?,result=? WHERE project_id=? "
                "AND request_id=? AND state='running'",
                ("unknown" if unknown else "done", raw, project_id, request_id),
            )
            if updated.rowcount != 1:
                raise StoreError("operation_changed")

    def recover_operations(self) -> None:
        # Caller holds the process-shared web-engine fence: no live web owner exists.
        with self._transaction(write=True) as c:
            c.exec_driver_sql(
                "UPDATE memory_web_operations SET state='unknown',result=? WHERE state='running'",
                (encode({"reason": "owner_interrupted"}),),
            )


def providers_from_host() -> Providers:
    model = Config.from_env()
    root = Path(__file__).resolve().parents[3]
    file = Path(
        os.environ.get(
            "AWB_MEMORY_EMBEDDING_CONFIG", str(root / "artifacts/mr01/embedding-env.json")
        )
    )
    info = file.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or info.st_size > 16384
    ):
        raise StoreError("provider_configuration_required")
    data = json.loads(file.read_bytes())
    if (
        not isinstance(data, dict)
        or type(data.get("dimensions")) is not int
        or data["dimensions"] != 1024
        or any(
            not isinstance(data.get(k), str) or not data[k]
            for k in ("endpoint", "api_key", "model")
        )
    ):
        raise StoreError("provider_configuration_required")
    endpoint = data["endpoint"].rstrip("/")
    return Providers(
        model.endpoint,
        model.api_key,
        model.model,
        endpoint if endpoint.endswith("/embeddings") else endpoint + "/embeddings",
        data["api_key"],
        data["model"],
        True,
        data.get("revision", "bge-m3-1024-host-v1"),
    )


class EngineService:
    def __init__(
        self,
        directory: Path,
        *,
        factory: Callable[[EngineWebStore, bool], CogneeRuntime] | None = None,
    ):
        self.directory = directory
        self.factory = factory or self._runtime
        self.tasks: set[asyncio.Task[Any]] = set()
        self.stop = threading.Event()
        self.admissions = asyncio.Semaphore(4)

    def _runtime(self, store: EngineWebStore, cleanup: bool) -> CogneeRuntime:
        root = Path(__file__).resolve().parents[3]
        return CogneeRuntime(
            store,
            self.directory.resolve() / "memory-engine",
            root / "artifacts/mr01/venv/bin/python",
            None if cleanup else providers_from_host(),
        )

    def fence(self) -> int:
        fd = os.open(
            self.directory / "memory-web.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(fd)
            raise StoreError("engine_busy") from None
        return fd

    def track(self, task: asyncio.Task[Any]) -> None:
        self.tasks.add(task)

        def finished(done: asyncio.Task[Any]) -> None:
            self.tasks.discard(done)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)

    def _admit(self, project_id: str, request: EngineRequest) -> tuple[dict[str, Any], int | None]:
        key(project_id)
        store = EngineWebStore(self.directory)
        fd = None
        try:
            if self.stop.is_set():
                raise StoreError("service_stopping")
            try:
                previous = store.web_operation(project_id, request.request_id)
            except StoreError as exc:
                if exc.code != "not_found":
                    raise
            else:
                store.admit_operation(project_id, request)
                if previous["state"] == "running":
                    try:
                        fd = self.fence()
                    except StoreError:
                        return previous, None
                    store.recover_operations()
                    return store.web_operation(project_id, request.request_id), None
                return previous, None
            fd = self.fence()
            store.recover_operations()
            if not store.admit_operation(project_id, request):
                return store.web_operation(project_id, request.request_id), None
            report = store.web_operation(project_id, request.request_id)
            transferred = fd
            fd = None
            return report, transferred
        finally:
            store.close()
            if fd is not None:
                os.close(fd)

    async def submit(self, project_id: str, request: EngineRequest) -> dict[str, Any]:
        if self.stop.is_set():
            raise StoreError("service_stopping")

        if self.admissions.locked():
            raise StoreError("engine_busy")
        await self.admissions.acquire()

        # Admission is owned independently of HTTP cancellation. Its thread owns any
        # acquired fd until this continuation transfers it to the physical worker.
        async def admit() -> dict[str, Any]:
            worker = asyncio.create_task(asyncio.to_thread(self._admit, project_id, request))
            try:
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        self.stop.set()
                report, fd = worker.result()
                if fd is not None:
                    self.track(asyncio.create_task(self._execute(project_id, request, fd)))
                return report
            finally:
                self.admissions.release()

        task = asyncio.create_task(admit())
        self.track(task)
        return await asyncio.shield(task)

    async def _execute(self, project_id: str, request: EngineRequest, fd: int) -> None:
        def run() -> None:
            store = EngineWebStore(self.directory)
            try:
                runtime = self.factory(store, request.operation == "cleanup")
                if self.stop.is_set():
                    raise StoreError("engine_stopped")

                def callback(job: str) -> None:
                    store.attach_job(project_id, request.request_id, job)

                if request.operation == "index":
                    assert request.memory_id is not None
                    job = runtime.index(
                        project_id, request.memory_id, cancel=self.stop, on_job=callback
                    )
                    result: dict[str, Any] = {"job_id": job}
                elif request.operation == "search":
                    assert request.query is not None
                    outcome = runtime.search(
                        project_id, request.query, cancel=self.stop, on_job=callback
                    )
                    result = outcome.model_dump(
                        mode="json", exclude={"references": {"__all__": {"body"}}}
                    )
                else:
                    result = {"removed_roots": runtime.cleanup(project_id)}
                store.finish_operation(project_id, request.request_id, result)
            except Exception:
                # M2 per-wire-call accounting remains available through the attached job.
                store.finish_operation(
                    project_id,
                    request.request_id,
                    {"reason": "engine_operation_failed"},
                    unknown=True,
                )
            finally:
                store.close()

        worker = asyncio.create_task(asyncio.to_thread(run))
        try:
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    self.stop.set()
                    # Repeated cancellation cannot release a live thread's fence.
            worker.result()
        finally:
            os.close(fd)

    async def close(self) -> None:
        self.stop.set()
        # Admission may hand off to an execution task during shutdown.
        while self.tasks:
            joined = asyncio.gather(*tuple(self.tasks), return_exceptions=True)
            while not joined.done():
                try:
                    await asyncio.shield(joined)
                except asyncio.CancelledError:
                    self.stop.set()
            joined.result()
