"""Independent timeout/profile and streaming cancellation checks; no real provider."""

import asyncio
import threading
import time

import httpx
import pytest

from agent_workbench.providers.cognee.gateway import Gateway, Providers
from agent_workbench.run_store import StoreError


def gateway(seconds=90):
    providers = Providers(
        "http://model.test/chat",
        "private",
        "existing",
        "http://embed.test/embed",
        "private",
        "bge-m3",
        True,
    )
    return Gateway(None, "p", None, providers, time.monotonic() + seconds)


@pytest.mark.parametrize("kind,expected", [("llm", 60), ("embedding", 30)])
def test_http_timeout_caps_are_kind_specific_and_no_retry(monkeypatch, kind, expected):
    original = httpx.AsyncClient
    options, calls = [], []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, content=b"{}")

    def client(**kwargs):
        options.append(kwargs)
        return original(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    gate = gateway()
    endpoint = gate.providers.model_endpoint if kind == "llm" else gate.providers.embedding_endpoint
    assert asyncio.run(gate.request(endpoint, "private", {})) == (200, b"{}")
    assert options[0]["timeout"] == expected
    assert options[0]["follow_redirects"] is False and options[0]["trust_env"] is False
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["deadline", "flag", "task_cancel"])
def test_outer_deadline_and_cancellation_close_dripping_http(monkeypatch, mode):
    original = httpx.AsyncClient
    closed = threading.Event()
    gate = gateway(0.09 if mode == "deadline" else 90)
    options = []

    class Drip(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.015)
                yield b"x"

        async def aclose(self):
            closed.set()

    def client(**kwargs):
        options.append(kwargs)
        transport = httpx.MockTransport(lambda _: httpx.Response(200, stream=Drip()))
        return original(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)

    async def run():
        started = time.monotonic()
        work = asyncio.create_task(gate.request(gate.providers.model_endpoint, "private", {}))
        await asyncio.sleep(0.02)
        if mode == "flag":
            gate.stopped.set()
        elif mode == "task_cancel":
            work.cancel()
        expected = (
            TimeoutError
            if mode == "deadline"
            else StoreError
            if mode == "flag"
            else asyncio.CancelledError
        )
        with pytest.raises(expected):
            await work
        assert closed.is_set()
        assert time.monotonic() - started < 0.5
        if mode == "deadline":
            assert 0 < options[0]["timeout"] <= 0.09
        assert len(options) == 1

    asyncio.run(run())
