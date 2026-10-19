"""MR-01 synthetic SDK probe. Opt-in, isolated files, bounded provider egress.

This is experimental validation, not the production memory gateway or job ledger.
Run with the main repository's Python; the child uses the isolated Cognee environment.
"""

import argparse
import asyncio
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from ops.local_service import read_environment  # noqa: E402


class Gate:
    """Lab-only per-wire-request admission; writes reservations before forwarding."""

    def __init__(self, directory: Path, model: dict[str, str], embedding: dict[str, Any]):
        self.directory = directory
        self.model = model
        self.embedding = embedding
        self.token = secrets.token_hex(24)
        self.lock = threading.Lock()
        self.deadline = time.monotonic() + 180
        self.stopped = False
        self.calls: list[dict[str, Any]] = []

    def save(self) -> None:
        tmp = self.directory / "calls.tmp"
        tmp.write_text(json.dumps(self.calls, indent=2))
        tmp.replace(self.directory / "calls.json")

    def admit(self, kind: str, size: int) -> dict[str, Any] | None:
        with self.lock:
            cap = 6 if kind == "llm" else 40
            if (
                self.stopped
                or time.monotonic() >= self.deadline
                or sum(c["kind"] == kind for c in self.calls) >= cap
                or size > 65536
            ):
                return None
            call = {
                "ordinal": len(self.calls) + 1,
                "kind": kind,
                "request_bytes": size,
                "state": "reserved",
                "usage": None,
            }
            self.calls.append(call)
            self.save()
            return call

    def stop(self) -> None:
        with self.lock:
            self.stopped = True

    async def request(self, endpoint: str, key: str, payload: dict) -> tuple[int, bytes]:
        async with asyncio.timeout(min(30, max(0.01, self.deadline - time.monotonic()))):
            async with httpx.AsyncClient(
                timeout=30, trust_env=False, follow_redirects=False
            ) as client:
                async with client.stream(
                    "POST", endpoint, headers={"Authorization": "Bearer " + key}, json=payload
                ) as response:
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if self.stopped or len(body) + len(chunk) > 1024 * 1024:
                            raise ValueError("response_limit_or_cancel")
                        body.extend(chunk)
                    return response.status_code, bytes(body)

    def forward(self, path: str, body: bytes) -> tuple[int, bytes]:
        kind = {"/v1/chat/completions": "llm", "/v1/embeddings": "embedding"}.get(path)
        if kind is None:
            return 404, b'{"error":{"message":"unsupported_path"}}'
        payload = json.loads(body)
        if not isinstance(payload, dict):
            return 400, b'{"error":{"message":"invalid_request"}}'
        if kind == "llm":
            base = self.model["AWB_MODEL_BASE_URL"].rstrip("/")
            endpoint = base + (
                "/chat/completions" if base.endswith("/v1") else "/v1/chat/completions"
            )
            key = self.model["AWB_MODEL_API_KEY"]
            payload["model"] = self.model["AWB_MODEL_NAME"]
            payload.pop("max_completion_tokens", None)
            payload["max_tokens"] = 4096
            payload["stream"] = False
        else:
            base = self.embedding["endpoint"].rstrip("/")
            endpoint = base if base.endswith("/embeddings") else base + "/embeddings"
            key = self.embedding["api_key"]
            payload["model"] = self.embedding["model"]
            inputs = payload.get("input")
            if isinstance(inputs, str):
                inputs = [inputs]
            if (
                not isinstance(inputs, list)
                or not 1 <= len(inputs) <= 16
                or any(not isinstance(v, str) or len(v) > 4096 for v in inputs)
            ):
                return 400, b'{"error":{"message":"input_limit"}}'
        call = self.admit(kind, len(body))
        if call is None:
            return 402, b'{"error":{"message":"Budget has been exceeded! Max budget: 0"}}'
        try:
            status, content = asyncio.run(self.request(endpoint, key, payload))
            if status != 200:
                reply = b'{"error":{"message":"provider_request_failed"}}'
                state = "unknown"
                usage = None
            else:
                parsed = json.loads(content)
                usage = parsed.get("usage")
                # Only numeric provider usage is persisted. Text stays in memory.
                usage = (
                    {
                        k: v
                        for k, v in usage.items()
                        if k in {"prompt_tokens", "completion_tokens", "total_tokens"}
                        and type(v) is int
                        and v >= 0
                    }
                    if isinstance(usage, dict)
                    else None
                )
                state = "received" if usage else "unknown"
                reply = content
        except Exception as error:
            call["error_type"] = type(error).__name__
            status, reply, state, usage = (
                504,
                b'{"error":{"message":"provider_result_unknown"}}',
                "unknown",
                None,
            )
        with self.lock:
            call.update(state=state, status=status, usage=usage)
            if state == "unknown":
                self.stopped = True
            self.save()
        return status, reply


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        parser.error("--execute required; only synthetic material will be sent")
    os.umask(0o077)
    directory = ROOT / "artifacts/mr01" / uuid.uuid4().hex
    directory.mkdir(mode=0o700)
    model = read_environment(ROOT / "artifacts/local-service/model-env.json")
    embedding = json.loads((ROOT / "artifacts/mr01/embedding-env.json").read_text())
    gate = Gate(directory, model, embedding)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_POST(self) -> None:
            if self.headers.get("Authorization") != "Bearer " + gate.token:
                self.send_error(403)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 65536:
                    raise ValueError()
                self.connection.settimeout(5)
                status, reply = gate.forward(self.path, self.rfile.read(size))
            except Exception:
                status, reply = 400, b'{"error":{"message":"invalid_request"}}'
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            try:
                self.wfile.write(reply)
            except OSError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/v1"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(directory),
        "TMPDIR": str(directory),
        "LANG": "en_US.UTF-8",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "TELEMETRY_DISABLED": "true",
        "COGNEE_TELEMETRY_ENABLED": "false",
        "COGNEE_LOG_FILE": "false",
        "CACHE_BACKEND": "sqlite",
        "COGNEE_SKIP_CONNECTION_TEST": "true",
        "LLM_PROVIDER": "custom",
        "LLM_MODEL": "openai/" + model["AWB_MODEL_NAME"],
        "LLM_ENDPOINT": endpoint,
        "LLM_API_KEY": gate.token,
        "LLM_MAX_COMPLETION_TOKENS": "4096",
        "STRUCTURED_OUTPUT_FRAMEWORK": "litellm_native",
        "EMBEDDING_PROVIDER": "openai_compatible",
        "EMBEDDING_MODEL": embedding["model"],
        "EMBEDDING_ENDPOINT": endpoint,
        "EMBEDDING_API_KEY": gate.token,
        "EMBEDDING_DIMENSIONS": "1024",
        "EMBEDDING_BATCH_SIZE": "16",
        "SYSTEM_ROOT_DIRECTORY": str(directory / "system"),
        "DATA_ROOT_DIRECTORY": str(directory / "data"),
        "DB_PROVIDER": "sqlite",
        "GRAPH_DATABASE_PROVIDER": "kuzu",
        "KUZU_BUFFER_POOL_SIZE": "134217728",
        "KUZU_MAX_DB_SIZE": "1073741824",
        "KUZU_NUM_THREADS": "2",
        "SUBPROCESS_IDLE_TTL_SECONDS": "0",
        "VECTOR_DB_PROVIDER": "lancedb",
        "MR01_RESULT": str(directory / "result.json"),
    }
    outcome: dict[str, object] = {"kind": "synthetic_sdk_probe"}
    try:
        child = subprocess.Popen(
            [
                str(ROOT / "artifacts/mr01/venv/bin/python"),
                str(Path(__file__).with_name("worker.py")),
            ],
            cwd=directory,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            outcome["worker_exit"] = child.wait(timeout=180)
        finally:
            # Cognee may create database workers. Reclaim only this new process group.
            gate.stop()
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=3)
    except subprocess.TimeoutExpired:
        outcome["worker_outcome"] = "timeout"
    finally:
        gate.stop()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        outcome["request_count"] = len(gate.calls)
        (directory / "summary.json").write_text(json.dumps(outcome, indent=2))
    print(json.dumps({"artifact": str(directory), **outcome}))


if __name__ == "__main__":
    run()
