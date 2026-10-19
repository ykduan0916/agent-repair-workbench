"""Deterministic lab-gateway tests, not Cognee or real-model acceptance."""

import asyncio
import importlib.util
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

spec = importlib.util.spec_from_file_location("mr01_probe", Path(__file__).with_name("probe.py"))
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.fixture
def gate(tmp_path):
    return probe.Gate(
        tmp_path,
        {
            "AWB_MODEL_BASE_URL": "http://invalid",
            "AWB_MODEL_NAME": "test",
            "AWB_MODEL_API_KEY": "x",
        },
        {"endpoint": "http://invalid", "model": "test", "api_key": "x"},
    )


def test_parallel_admission_has_fixed_actual_request_cap(gate):
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: gate.admit("llm", 20), range(20)))
    assert sum(result is not None for result in results) == 6
    assert len(json.loads((gate.directory / "calls.json").read_text())) == 6


@pytest.mark.parametrize("stop", ["cancel", "deadline", "size"])
def test_refusal_occurs_before_network(gate, stop):
    if stop == "cancel":
        gate.stop()
    elif stop == "deadline":
        gate.deadline = 0
    body = json.dumps({"messages": [], "padding": "x" * (70000 if stop == "size" else 0)})
    with patch.object(gate, "request") as client:
        status, _ = gate.forward("/v1/chat/completions", body.encode())
    assert status == 402
    client.assert_not_called()
    assert gate.calls == []


def test_reserved_is_persisted_before_send_and_unknown_is_retained(gate):
    async def send(*_args, **_kwargs):
        saved = json.loads((gate.directory / "calls.json").read_text())
        assert saved[0]["state"] == "reserved"
        raise httpx.ReadTimeout("never log this secret provider text")

    with patch.object(gate, "request", side_effect=send) as post:
        status, reply = gate.forward("/v1/chat/completions", b'{"messages":[]}')
    assert status == 504
    assert post.call_count == 1
    assert gate.calls[0]["state"] == "unknown"
    assert gate.calls[0]["usage"] is None
    assert b"secret" not in reply
    assert "secret" not in (gate.directory / "calls.json").read_text()


def test_actual_wire_calls_are_capped_despite_sdk_retries(gate):
    # Provider returned billed output; an SDK may reject its format and try again.
    response = httpx.Response(200, json={"usage": {"total_tokens": 1}, "choices": []})
    with patch.object(
        gate, "request", return_value=(response.status_code, response.content)
    ) as post:
        statuses = [gate.forward("/v1/chat/completions", b'{"messages":[]}')[0] for _ in range(9)]
    assert statuses == [200] * 6 + [402] * 3
    assert post.call_count == 6
    assert all(c["state"] == "received" for c in gate.calls)


def test_bge_usage_without_index_is_preserved(gate):
    response = httpx.Response(
        200, json={"data": [{"embedding": [0.1] * 1024}], "usage": {"total_tokens": 7}}
    )
    with patch.object(gate, "request", return_value=(response.status_code, response.content)):
        status, reply = gate.forward("/v1/embeddings", b'{"input":["synthetic"]}')
    assert status == 200
    assert "index" not in json.loads(reply)["data"][0]
    assert gate.calls[0]["usage"] == {"total_tokens": 7}


def test_missing_usage_is_unknown_not_free(gate):
    response = httpx.Response(200, json={"choices": []})
    with patch.object(gate, "request", return_value=(response.status_code, response.content)):
        gate.forward("/v1/chat/completions", b'{"messages":[]}')
    assert gate.calls[0]["state"] == "unknown"
    assert gate.calls[0]["usage"] is None
    assert gate.forward("/v1/chat/completions", b'{"messages":[]}')[0] == 402
    assert len(gate.calls) == 1


@pytest.mark.parametrize("path,payload", [("/other", b"{}"), ("/v1/embeddings", b'{"input":[]}')])
def test_invalid_requests_do_not_reach_provider(gate, path, payload):
    with patch.object(gate, "request") as client:
        status, _ = gate.forward(path, payload)
    assert status in {400, 404}
    client.assert_not_called()


def test_response_bytes_are_bounded_on_actual_httpx_path(gate):
    async def handler(_request):
        return httpx.Response(200, content=b"x" * (1024 * 1024 + 1))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch.object(probe.httpx, "AsyncClient", return_value=client):
        with pytest.raises(ValueError):
            asyncio.run(gate.request("http://invalid", "x", {}))


def test_absolute_deadline_cancels_async_provider_wait(gate):
    async def handler(_request):
        await asyncio.sleep(60)
        return httpx.Response(200, json={})

    gate.deadline = time.monotonic() + 0.05
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    start = time.monotonic()
    with patch.object(probe.httpx, "AsyncClient", return_value=client):
        with pytest.raises(TimeoutError):
            asyncio.run(gate.request("http://invalid", "x", {}))
    assert time.monotonic() - start < 1


def test_inflight_stop_is_not_success_or_free(gate):
    async def handler(_request):
        gate.stop()
        return httpx.Response(200, json={"usage": {"total_tokens": 1}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch.object(probe.httpx, "AsyncClient", return_value=client):
        status, _ = gate.forward("/v1/chat/completions", b'{"messages":[]}')
    assert status == 504
    assert gate.calls[0]["state"] == "unknown"
