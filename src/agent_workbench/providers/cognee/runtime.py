"""M2-R single-writer pinned engine execution. All returned context is untrusted data."""

import contextlib
import fcntl
import json
import os
import secrets
import shutil
import signal
import stat
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import chain
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_workbench.experience_memory.common import key, sha, validate
from agent_workbench.experience_memory.engine_models import Query, Retrieval
from agent_workbench.experience_memory.engine_store import EngineStore
from agent_workbench.experience_memory.models import Lease
from agent_workbench.run_store import StoreError

from .gateway import ERROR, Gateway, Providers
from .tokenizer_cache import WORKER_FAILURES


def private_tree(path: Path) -> None:
    if path.absolute() != path.resolve():
        raise StoreError("unsafe_engine_path")
    if not path.is_dir():
        raise StoreError("engine_directory_required")
    for i, p in enumerate(chain((path,), path.rglob("*"))):
        info = p.lstat()
        if (
            i > 50000
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
        ):
            raise StoreError("unsafe_engine_permissions")


class CogneeRuntime:
    def __init__(
        self, store: EngineStore, directory: Path, python: Path, providers: Providers | None
    ):
        self.store, self.directory, self.python, self.providers = (
            store,
            directory,
            python,
            providers,
        )
        if directory.absolute() != directory.resolve():
            raise StoreError("unsafe_engine_path")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        private_tree(directory)
        if not python.is_file():
            raise StoreError("engine_environment_required")
        # Both directions are pinned: another root cannot erase nonexistent copies,
        # and another authority DB cannot run a second writer over the same engine files.
        with self.writer():
            marker = directory / "authority.digest"
            expected = sha(str(store.path.resolve()))
            if marker.exists():
                if marker.stat().st_size != 64 or marker.read_text() != expected:
                    raise StoreError("engine_authority_changed")
            else:
                with open(marker, "x", opener=lambda p, f: os.open(p, f, 0o600)) as stream:
                    stream.write(expected)
            store.bind_runtime(sha(str(directory.resolve())))

    @contextlib.contextmanager
    def writer(self) -> Iterator[int]:
        private_tree(self.directory)
        fd = os.open(self.directory / "writer.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StoreError("engine_busy") from None
            yield fd
        finally:
            os.close(fd)

    def _environment(self, root: Path, gate: Gateway, endpoint: str) -> dict[str, str]:
        if self.providers is None:
            raise StoreError("provider_configuration_required")
        return {
            "PATH": "/usr/bin:/bin",
            "HOME": str(root),
            "TMPDIR": str(root),
            "TIKTOKEN_CACHE_DIR": str(
                self.python.absolute().parent.parent / "share/awb-tokenizers"
            ),
            "LANG": "en_US.UTF-8",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "ALLOW_HTTP_REQUESTS": "false",
            "TELEMETRY_DISABLED": "true",
            "COGNEE_TELEMETRY_ENABLED": "false",
            "COGNEE_LOG_FILE": "false",
            "CACHE_BACKEND": "sqlite",
            "COGNEE_SKIP_CONNECTION_TEST": "true",
            "LITELLM_LOCAL_MODEL_COST_MAP": "true",
            "LLM_PROVIDER": "custom",
            "LLM_MODEL": "openai/" + self.providers.model_name,
            "LLM_ENDPOINT": endpoint,
            "LLM_API_KEY": gate.token,
            "LLM_MAX_COMPLETION_TOKENS": "4096",
            "STRUCTURED_OUTPUT_FRAMEWORK": "litellm_native",
            "EMBEDDING_PROVIDER": "openai_compatible",
            "EMBEDDING_MODEL": "bge-m3",
            "EMBEDDING_ENDPOINT": endpoint,
            "EMBEDDING_API_KEY": gate.token,
            "EMBEDDING_DIMENSIONS": "1024",
            "EMBEDDING_BATCH_SIZE": "16",
            "SYSTEM_ROOT_DIRECTORY": str(root / "system"),
            "DATA_ROOT_DIRECTORY": str(root / "data"),
            "DB_PROVIDER": "sqlite",
            "GRAPH_DATABASE_PROVIDER": "kuzu",
            "KUZU_BUFFER_POOL_SIZE": "134217728",
            "KUZU_MAX_DB_SIZE": "1073741824",
            "KUZU_NUM_THREADS": "2",
            "SUBPROCESS_IDLE_TTL_SECONDS": "0",
            "VECTOR_DB_PROVIDER": "lancedb",
        }

    def _worker(
        self,
        project: str,
        lease: Lease,
        root_id: str,
        operation: str,
        text: str,
        fd: int,
        deadline: float,
        cancel: threading.Event,
    ) -> dict[str, Any]:
        if self.providers is None:
            raise StoreError("provider_configuration_required")
        root = self.directory / key(root_id)
        if operation == "search" and not root.is_dir():
            raise StoreError("engine_root_missing")
        root.mkdir(mode=0o700, exist_ok=True)
        private_tree(root)
        nonce = uuid4().hex
        input_path, output_path = root / (nonce + ".input"), root / (nonce + ".result")
        with open(input_path, "x", opener=lambda p, f: os.open(p, f, 0o600)) as stream:
            json.dump({"operation": operation, "root_id": root_id, "text": text}, stream)
        gate = Gateway(self.store, project, lease, self.providers, deadline)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                pass

            def do_POST(self) -> None:
                status, reply = 403, ERROR
                try:
                    self.connection.settimeout(5)
                    if secrets.compare_digest(
                        self.headers.get("Authorization", ""), "Bearer " + gate.token
                    ):
                        size = int(self.headers.get("Content-Length", "0"))
                        if 0 < size <= 65536:
                            status, reply = gate.forward(self.path, self.rfile.read(size))
                except Exception:
                    status, reply = 400, ERROR
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                with contextlib.suppress(OSError):
                    self.wfile.write(reply)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = False
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        child = None
        heartbeat_read, heartbeat_write = os.pipe()
        try:
            endpoint = f"http://127.0.0.1:{server.server_port}/v1"
            child = subprocess.Popen(
                [
                    str(self.python),
                    str(Path(__file__).with_name("worker.py")),
                    str(input_path),
                    str(output_path),
                ],
                cwd=root,
                env={
                    **self._environment(root, gate, endpoint),
                    "AWB_PARENT_PIPE": str(heartbeat_read),
                    "AWB_ENGINE_SECONDS": str(max(0.01, deadline - time.monotonic())),
                },
                start_new_session=True,
                pass_fds=(fd, heartbeat_read),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            renewed = time.monotonic()
            while child.poll() is None:
                if cancel.is_set() or gate.stopped.is_set() or time.monotonic() >= deadline:
                    raise StoreError("engine_stopped")
                if time.monotonic() - renewed >= 5:
                    self.store.renew_job(project, lease)
                    renewed = time.monotonic()
                cancel.wait(0.1)
            if cancel.is_set() or gate.stopped.is_set() or time.monotonic() >= deadline:
                raise StoreError("engine_stopped")
            private_tree(root)
            if child.returncode or not output_path.is_file() or output_path.stat().st_size > 65536:
                raise StoreError("engine_failed")
            result = json.loads(output_path.read_bytes())
            if not isinstance(result, dict) or result.get("status") != "ready":
                code = result.get("failure_code") if isinstance(result, dict) else None
                raise StoreError(
                    code if isinstance(code, str) and code in WORKER_FAILURES else "engine_failed"
                )
            if result.get("cache") != "sqlite" or result.get("engine_version") != "1.6.5":
                raise StoreError("engine_contract_changed")
            return result
        finally:
            os.close(heartbeat_write)
            os.close(heartbeat_read)
            gate.stopped.set()
            if child is not None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            input_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)

    def index(
        self,
        project: str,
        memory_id: str,
        *,
        cancel: threading.Event | None = None,
        on_job: Callable[[str], None] | None = None,
    ) -> str:
        if self.providers is None:
            raise StoreError("provider_configuration_required")
        cancel = cancel or threading.Event()
        with self.writer() as fd:
            job_id = self.store.prepare_index(
                project, memory_id, profile_digest=self.providers.embedding_fingerprint
            )
            lease = self.store.claim_job(project, job_id=job_id)
            if lease is None:
                raise StoreError("job_unavailable")
            try:
                if on_job is not None:
                    on_job(job_id)
                self.store.snapshot(project, lease)
                content = self.store.memory(project, memory_id).content
                if content is None or cancel.is_set():
                    raise StoreError("engine_stopped")
                result = self._worker(
                    project,
                    lease,
                    job_id,
                    "index",
                    content.body,
                    fd,
                    time.monotonic() + 180,
                    cancel,
                )
                if cancel.is_set():
                    raise StoreError("engine_stopped")
                self.store.publish_index(project, lease, result["dataset_id"], result["data_id"])
                self.store.finish_job(project, lease, succeeded=True)
            except Exception as error:
                self.store.record_failure(
                    project,
                    lease,
                    error.code
                    if isinstance(error, StoreError)
                    and error.code
                    in WORKER_FAILURES | {"engine_stopped", "engine_contract_changed"}
                    else "engine_failed",
                )
                self.store.cancel_job(project, job_id)
                raise StoreError("index_failed") from None
            return job_id

    def search(
        self,
        project: str,
        query: Query,
        *,
        cancel: threading.Event | None = None,
        on_job: Callable[[str], None] | None = None,
        deadline: float | None = None,
    ) -> Retrieval:
        if self.providers is None:
            raise StoreError("provider_configuration_required")
        query = validate(Query, query)
        cancel = cancel or threading.Event()
        job_id = None
        lease = None
        try:
            with self.writer() as fd:
                job_id = self.store.prepare_search(
                    project, query, profile_digest=self.providers.embedding_fingerprint
                )
                if on_job is not None:
                    on_job(job_id)
                lease = self.store.claim_job(project, job_id=job_id)
                if lease is None:
                    raise StoreError("job_unavailable")
                snapshot = self.store.snapshot(project, lease)
                if snapshot["query_digest"] != sha(query.text):
                    raise StoreError("query_changed")
                deadline = (
                    min(deadline, time.monotonic() + 180)
                    if deadline is not None
                    else time.monotonic() + 180
                )
                evidence = []
                for ref in snapshot["refs"]:
                    if cancel.is_set() or time.monotonic() >= deadline:
                        raise StoreError("engine_stopped")
                    result = self._worker(
                        project, lease, ref["root_id"], "search", query.text, fd, deadline, cancel
                    )
                    for dataset, data in result["evidence"]:
                        evidence.append((ref["root_id"], dataset, data))
                if cancel.is_set():
                    raise StoreError("engine_stopped")
                references = self.store.references(project, lease, evidence)
                self.store.finish_job(project, lease, succeeded=True)
                return Retrieval(
                    status="available",
                    reason="matched" if references else "no_match",
                    job_id=job_id,
                    references=references,
                )
        except StoreError as error:
            if job_id is not None:
                if lease is not None and error.code in WORKER_FAILURES:
                    self.store.record_failure(project, lease, error.code)
                self.store.cancel_job(project, job_id)
            return Retrieval(
                status="limited"
                if error.code
                in ("scope_limit", "scope_conflict", "index_required", "context_limit")
                else "unavailable",
                reason=error.code,
                job_id=job_id,
            )
        except Exception:
            if job_id is not None:
                self.store.cancel_job(project, job_id)
            return Retrieval(status="unavailable", reason="engine_failed", job_id=job_id)

    def cleanup(self, project: str) -> int:
        with self.writer():
            roots = self.store.roots_for_cleanup(project)
            for root_id in roots:
                path = self.directory / key(root_id)
                if path.exists():
                    private_tree(path)
                    shutil.rmtree(path)
                self.store.removed_root(project, root_id)
            return len(roots)
