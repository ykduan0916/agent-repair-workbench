"""M2-R per-wire loopback proxy with durable reservations and safe failure bodies."""

import asyncio
import json
import math
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

import httpx

from agent_workbench.experience_memory.engine_store import EngineStore
from agent_workbench.experience_memory.models import Lease
from agent_workbench.run_store import StoreError

ERROR = b'{"error":{"message":"memory_request_stopped"}}'


@dataclass(frozen=True)
class Providers:
    model_endpoint: str
    model_key: str = field(repr=False)
    model_name: str
    embedding_endpoint: str
    embedding_key: str = field(repr=False)
    embedding_name: str
    conditional_token_profile: bool = False
    embedding_revision: str = "bge-m3-1024-host-v1"

    @property
    def embedding_fingerprint(self) -> str:
        from agent_workbench.experience_memory.common import encode, sha

        return sha(
            encode(
                {
                    "endpoint": self.embedding_endpoint,
                    "model": self.embedding_name,
                    "dimensions": 1024,
                    "revision": self.embedding_revision,
                }
            )
        )

    def __post_init__(self) -> None:
        if not self.embedding_revision or len(self.embedding_revision) > 96:
            raise StoreError("embedding_revision_required")
        if not self.conditional_token_profile:
            raise StoreError("token_profile_required")
        for endpoint in (self.model_endpoint, self.embedding_endpoint):
            url = httpx.URL(endpoint)
            if url.scheme not in ("http", "https") or not url.host or url.username or url.password:
                raise StoreError("invalid_provider_endpoint")
        if not self.model_key or not self.embedding_key or self.embedding_name != "bge-m3":
            raise StoreError("provider_configuration_required")


class Gateway:
    def __init__(
        self, store: EngineStore, project: str, lease: Lease, providers: Providers, deadline: float
    ):
        self.store, self.project, self.lease = store, project, lease
        self.providers, self.deadline = providers, deadline
        self.token = secrets.token_hex(32)
        self.stopped = threading.Event()
        self.lock = threading.Lock()

    async def request(
        self, endpoint: str, api_key: str, payload: dict[str, Any]
    ) -> tuple[int, bytes]:
        # Index extraction may require a longer completion than embedding. Both
        # remain bounded by the original job/parent deadline, without retries.
        cap = 60 if endpoint == self.providers.model_endpoint else 30
        timeout = min(cap, max(0.01, self.deadline - time.monotonic()))
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(
                timeout=timeout, trust_env=False, follow_redirects=False
            ) as client:
                async with client.stream(
                    "POST", endpoint, headers={"Authorization": "Bearer " + api_key}, json=payload
                ) as response:
                    body = bytearray()
                    async for part in response.aiter_bytes():
                        if self.stopped.is_set() or len(body) + len(part) > 1048576:
                            raise StoreError("response_limit_or_cancel")
                        body.extend(part)
                    return response.status_code, bytes(body)

    def forward(self, path: str, body: bytes) -> tuple[int, bytes]:
        # Serializing actual sends also prevents parallel SDK retries overspending.
        with self.lock:
            call_id = None
            failure = "request_rejected"
            try:
                if self.stopped.is_set() or time.monotonic() >= self.deadline:
                    return 503, ERROR
                if not 0 < len(body) <= 65536:
                    raise StoreError("request_limit")
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise StoreError("invalid_request")
                input_count = 0
                kind: Literal["llm", "embedding"]
                if path == "/v1/chat/completions":
                    kind = "llm"
                    endpoint, api_key = self.providers.model_endpoint, self.providers.model_key
                    payload["model"] = self.providers.model_name
                    payload.pop("max_completion_tokens", None)
                    payload.update(max_tokens=4096, stream=False)
                elif path == "/v1/embeddings":
                    kind = "embedding"
                    endpoint, api_key = (
                        self.providers.embedding_endpoint,
                        self.providers.embedding_key,
                    )
                    payload["model"] = self.providers.embedding_name
                    inputs = payload.get("input")
                    if isinstance(inputs, str):
                        inputs = [inputs]
                    if (
                        not isinstance(inputs, list)
                        or not 1 <= len(inputs) <= 16
                        or any(not isinstance(v, str) or len(v) > 4096 for v in inputs)
                    ):
                        raise StoreError("embedding_limit")
                    input_count = len(inputs)
                else:
                    raise StoreError("unsupported_path")
                wire = json.dumps(payload, ensure_ascii=False).encode()
                if len(wire) > 65536:
                    raise StoreError("request_limit")
                tokens = len(wire) + 1024 + (4096 if kind == "llm" else 0)
                proposed_id = uuid4().hex
                receipt = self.store.reserve_call(
                    self.project, self.lease, proposed_id, kind, tokens
                )
                if not receipt.admitted:
                    raise StoreError("request_already_reserved")
                call_id = proposed_id
                failure = "provider_transport_error"
                status, content = asyncio.run(self.request(endpoint, api_key, payload))
                failure = "provider_http_error" if status != 200 else "usage_missing"
                actual = None
                if status == 200:
                    parsed = json.loads(content)
                    usage = parsed.get("usage") if isinstance(parsed, dict) else None
                    total = usage.get("total_tokens") if isinstance(usage, dict) else None
                    if type(total) is int and 0 <= total <= 10**9:
                        actual = total
                if actual is not None and actual > tokens:
                    failure = "usage_overrun"
                receipt = self.store.settle_call(self.project, self.lease, call_id, actual)
                call_id = None
                if receipt.state != "received" or self.stopped.is_set():
                    raise StoreError("provider_result_unknown")
                if kind == "embedding":
                    failure = "embedding_invalid"
                    data = parsed.get("data")
                    if not isinstance(data, list) or len(data) != input_count:
                        raise StoreError("embedding_invalid")
                    for i, item in enumerate(data):
                        vector = item.get("embedding") if isinstance(item, dict) else None
                        if (
                            not isinstance(vector, list)
                            or len(vector) != 1024
                            or any(
                                type(v) not in (float, int) or not math.isfinite(v) for v in vector
                            )
                            or (
                                "index" in item
                                and (type(item["index"]) is not int or item["index"] != i)
                            )
                        ):
                            raise StoreError("embedding_invalid")
                failure = "authority_changed"
                self.store.snapshot(self.project, self.lease)
                return 200, content
            except Exception as error:
                if isinstance(error, (TimeoutError, httpx.TimeoutException)):
                    failure = "provider_timeout"
                self.store.record_failure(self.project, self.lease, failure)
                self.stopped.set()
                if call_id is not None:
                    self.store.settle_call(self.project, self.lease, call_id, None)
                return 503, ERROR
