"""P0-05b: explicit provider bound, bounded HTTP, strict tool/usage decoding."""

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

import httpx

from agent_workbench.orchestration.codec import (
    DIAGNOSIS_ADAPTER,
    REPAIR_ADAPTER,
    VERIFICATION_ADAPTER,
    _constant,
    _pairs,
)
from agent_workbench.orchestration.contracts import Role

PROFILE = "deepseek-v4.1-flash-1m-v1"
MODEL_NAMES = frozenset({"deepseek-flash", "deepseek-v4-1-flash", "deepseek-v4-1-flash-260910"})
INPUT_BOUND = 1_048_576
ROLE_OUTPUT = {"diagnosis": 4096, "repair": 8192, "verification": 4096}


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


class GatewayError(ValueError):
    """Static codes only. Never propagate the HTTP body or exception string."""


@dataclass(frozen=True)
class Config:
    base_url: str = field(repr=False)
    model: str
    api_key: str = field(repr=False)
    limit_profile: str

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/", "/v1", "/v1/"}
            or any(ord(c) < 33 for c in self.base_url)
            or not self.api_key
            or len(self.api_key) > 4096
            or any(ord(c) < 33 or ord(c) > 126 for c in self.api_key)
        ):
            raise GatewayError("MODEL_NOT_CONFIGURED")
        if self.limit_profile != PROFILE or self.model not in MODEL_NAMES:
            raise GatewayError("INPUT_BOUND_UNVERIFIED")

    @classmethod
    def from_env(cls) -> "Config":
        values = [
            os.environ.get(key, "")
            for key in (
                "AWB_MODEL_BASE_URL",
                "AWB_MODEL_NAME",
                "AWB_MODEL_API_KEY",
                "AWB_MODEL_LIMIT_PROFILE",
            )
        ]
        if not all(values):
            raise GatewayError("MODEL_NOT_CONFIGURED")
        return cls(*values)

    @property
    def endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        return base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")

    @property
    def sha256(self) -> str:
        return digest(
            canonical(
                {
                    "endpoint": self.endpoint,
                    "model": self.model,
                    "profile": self.limit_profile,
                    "input_bound": INPUT_BOUND,
                    "usage": "prompt+completion=total;reasoning-in-completion",
                    "role_output": ROLE_OUTPUT,
                    "adapter": 2,
                    "role_prompt_revision": "v1.1-evidence-handoff",
                }
            )
        )


@dataclass(frozen=True)
class PreparedCall:
    body: bytes = field(repr=False)
    request_sha256: str
    input_reserved: int
    output_reserved: int
    reservation_sha256: str


@dataclass(frozen=True)
class Reply:
    decision: bytes | None = field(default=None, repr=False)
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None

    @property
    def usage_known(self) -> bool:
        return self.input_tokens is not None and self.output_tokens is not None


class Adapter(Protocol):
    @property
    def config_sha256(self) -> str: ...

    def prepare(
        self, role: Role, messages: list[dict[str, str]], output_cap: int
    ) -> PreparedCall: ...

    async def send(self, request: PreparedCall, timeout: float) -> Reply: ...


class _ResponseLimit(ValueError):
    pass


def _parse(raw: bytes) -> Any:
    parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    pending = [(parsed, 0)]
    while pending:
        value, depth = pending.pop()
        if depth > 30:
            raise _ResponseLimit("depth")
        if isinstance(value, dict):
            pending.extend((v, depth + 1) for v in value.values())
        elif isinstance(value, list):
            pending.extend((v, depth + 1) for v in value)
    return parsed


def decode_reply(raw: bytes, request: PreparedCall) -> Reply:
    try:
        if len(raw) > 131072:
            return Reply(error="MODEL_RESPONSE_INVALID")
        obj = _parse(raw)
        usage = obj.get("usage")
        if not isinstance(usage, dict):
            return Reply(error="MODEL_USAGE_UNKNOWN")
        i, o, total = (usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens"))
        if any(type(v) is not int or v < 0 for v in (i, o, total)):
            return Reply(error="MODEL_USAGE_UNKNOWN")
        i, o, total = cast(int, i), cast(int, o), cast(int, total)
        if i + o != total:
            return Reply(error="MODEL_USAGE_UNKNOWN")
        if i > request.input_reserved or o > request.output_reserved:
            return Reply(error="MODEL_USAGE_BOUND_VIOLATION")
        choices = obj.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            return Reply(input_tokens=i, output_tokens=o, error="MODEL_RESPONSE_INVALID")
        choice = choices[0]
        if choice.get("finish_reason") == "length":
            return Reply(input_tokens=i, output_tokens=o, error="MODEL_OUTPUT_TRUNCATED")
        message = choice.get("message")
        if not isinstance(message, dict) or choice.get("finish_reason") != "tool_calls":
            return Reply(input_tokens=i, output_tokens=o, error="MODEL_DECISION_INVALID")
        calls = message.get("tool_calls")
        if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
            return Reply(input_tokens=i, output_tokens=o, error="MODEL_DECISION_INVALID")
        call = calls[0]
        fn = call.get("function")
        if call.get("type") != "function" or not isinstance(fn, dict):
            return Reply(input_tokens=i, output_tokens=o, error="MODEL_DECISION_INVALID")
        args = fn.get("arguments")
        if fn.get("name") != "emit_decision" or not isinstance(args, str):
            return Reply(input_tokens=i, output_tokens=o, error="MODEL_DECISION_INVALID")
        try:
            argument_bytes = args.encode("utf-8")
            if len(argument_bytes) > 65536:
                return Reply(input_tokens=i, output_tokens=o, error="MODEL_DECISION_LIMIT")
            wrapper = _parse(argument_bytes)
        except _ResponseLimit:
            return Reply(input_tokens=i, output_tokens=o, error="MODEL_DECISION_LIMIT")
        except (UnicodeError, ValueError, RecursionError, TypeError):
            return Reply(input_tokens=i, output_tokens=o, error="MODEL_DECISION_JSON_INVALID")
        if (
            not isinstance(wrapper, dict)
            or set(wrapper) != {"decision"}
            or not isinstance(wrapper["decision"], dict)
        ):
            return Reply(input_tokens=i, output_tokens=o, error="MODEL_DECISION_INVALID")
        decision = canonical(wrapper["decision"])
        if len(decision) > 65536:
            return Reply(input_tokens=i, output_tokens=o, error="MODEL_DECISION_LIMIT")
        return Reply(decision=decision, input_tokens=i, output_tokens=o)
    except (ValueError, TypeError, AttributeError, RecursionError, UnicodeError):
        return Reply(error="MODEL_RESPONSE_INVALID")


def prepare_decision_call(
    config: Config,
    config_sha256: str,
    schema: dict[str, Any],
    messages: list[dict[str, str]],
    output_cap: int,
    role_limit: int,
) -> PreparedCall:
    """Build a bounded call from a host-owned schema; never mutate the schema cache."""
    if (
        type(output_cap) is not int
        or not 1 <= output_cap <= 32768
        or type(role_limit) is not int
        or not 1 <= role_limit <= 32768
    ):
        raise GatewayError("BUDGET_EXCEEDED")
    output = min(role_limit, output_cap)
    schema = dict(schema)
    parameters = {
        "type": "object",
        "$defs": schema.pop("$defs", {}),
        "properties": {"decision": schema},
        "required": ["decision"],
        "additionalProperties": False,
    }
    body = canonical(
        {
            "model": config.model,
            "messages": messages,
            "stream": False,
            "max_tokens": output,
            "thinking": {"type": "disabled"},
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "emit_decision",
                        "description": "Return one host-validated decision",
                        "parameters": parameters,
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": "emit_decision"}},
        }
    )
    if len(body) > 65536:
        raise GatewayError("MODEL_RESPONSE_INVALID")
    reservation_hash = digest(
        canonical({"config": config_sha256, "input": INPUT_BOUND, "output": output})
    )
    return PreparedCall(body, digest(body), INPUT_BOUND, output, reservation_hash)


class DeepSeekAdapter:
    def __init__(self, config: Config, *, transport: httpx.AsyncBaseTransport | None = None):
        self._config = config
        self._transport = transport

    @property
    def config_sha256(self) -> str:
        return self._config.sha256

    def prepare(self, role: Role, messages: list[dict[str, str]], output_cap: int) -> PreparedCall:
        schema = {
            "diagnosis": DIAGNOSIS_ADAPTER.json_schema(),
            "repair": REPAIR_ADAPTER.json_schema(),
            "verification": VERIFICATION_ADAPTER.json_schema(),
        }[role]
        return prepare_decision_call(
            self._config, self.config_sha256, schema, messages, output_cap, ROLE_OUTPUT[role]
        )

    async def send(self, request: PreparedCall, timeout: float) -> Reply:
        if not 0 < timeout <= 600:
            return Reply(error="MODEL_TIMEOUT")
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(
                    follow_redirects=False,
                    trust_env=False,
                    transport=self._transport,
                    timeout=httpx.Timeout(timeout),
                    limits=httpx.Limits(max_connections=1),
                ) as client:
                    async with client.stream(
                        "POST",
                        self._config.endpoint,
                        content=request.body,
                        headers={
                            "Authorization": "Bearer " + self._config.api_key,
                            "Content-Type": "application/json",
                            "Accept-Encoding": "identity",
                        },
                    ) as response:
                        if response.status_code != 200:
                            return Reply(error="MODEL_TRANSPORT_ERROR")
                        if response.headers.get("content-encoding", "identity") not in {
                            "identity",
                            "",
                        }:
                            return Reply(error="MODEL_RESPONSE_INVALID")
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(raw) + len(chunk) > 131072:
                                return Reply(error="MODEL_RESPONSE_INVALID")
                            raw.extend(chunk)
                return decode_reply(bytes(raw), request)
        except (TimeoutError, httpx.TimeoutException):
            return Reply(error="MODEL_TIMEOUT")
        except (httpx.HTTPError, OSError, ValueError):
            return Reply(error="MODEL_TRANSPORT_ERROR")
