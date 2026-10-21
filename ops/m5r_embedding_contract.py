"""Pinned SDK batching contract. Mock HTTP only; no provider calls or downloads."""

import asyncio
import importlib.metadata
import json
import os
from tempfile import TemporaryDirectory

_sandbox = TemporaryDirectory(prefix="awb-sdk-batching-")
os.environ.update(
    {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "TELEMETRY_DISABLED": "true",
        "COGNEE_TELEMETRY_ENABLED": "false",
        "EMBEDDING_BATCH_SIZE": "1",
        "EMBEDDING_MAX_CONCURRENT_DATA_POINTS": "1",
        "CACHE_BACKEND": "sqlite",
        "COGNEE_LOG_FILE": "false",
        "SYSTEM_ROOT_DIRECTORY": _sandbox.name + "/system",
        "DATA_ROOT_DIRECTORY": _sandbox.name + "/data",
    }
)

import httpx  # noqa: E402
from cognee.infrastructure.databases.vector.embeddings.config import get_embedding_context_config  # noqa: E402
from cognee.infrastructure.databases.vector.embeddings.OpenAICompatibleEmbeddingEngine import (  # noqa: E402
    OpenAICompatibleEmbeddingEngine,
)
from cognee.infrastructure.engine import DataPoint  # noqa: E402
from cognee.tasks.storage.index_data_points import index_data_points  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402


class Point(DataPoint):
    name: str


async def main() -> None:
    assert importlib.metadata.version("cognee") == "1.6.5"
    config = get_embedding_context_config()
    assert config.embedding_batch_size == config.embedding_max_concurrent_data_points == 1
    # Tokenizer is not part of this batching test and must not perform resolution/downloads.
    OpenAICompatibleEmbeddingEngine.get_tokenizer = lambda self: None
    engine = OpenAICompatibleEmbeddingEngine(
        model="bge-m3", dimensions=1024, batch_size=config.embedding_batch_size
    )
    await engine._client.close()
    calls = []

    def response(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(len(payload["input"]))
        assert len(payload["input"]) == 1
        return httpx.Response(
            200,
            json={
                "data": [{"index": 0, "embedding": [0.0] * 1024, "object": "embedding"}],
                "model": "bge-m3",
                "object": "list",
                "usage": {"prompt_tokens": 4, "total_tokens": 4},
            },
        )

    engine._client = AsyncOpenAI(
        api_key="test",
        base_url="http://local.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(response)),
    )

    class Vector:
        embedding_engine = engine

        async def create_vector_index(self, *args):
            pass

        async def index_data_points(self, name, field, points):
            result = await engine.embed_text([getattr(p, field) for p in points])
            assert len(result) == len(points)

    points = [Point(name=f"synthetic{i}") for i in range(3)]
    for point in points:
        point.metadata["index_fields"] = ["name"]
    await index_data_points(points, Vector())
    assert calls == [1, 1, 1]
    await engine._client.close()
    print(json.dumps({"sdk": "1.6.5", "requests": calls, "network": "mock_only"}))


if __name__ == "__main__":
    asyncio.run(main())
