"""Runs only in the pinned separate SDK environment; no upstream secrets available."""

import asyncio
import importlib
import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path


async def main() -> None:
    os.umask(0o077)
    # A separate stdlib process enforces parent death and time even during SDK native code.
    pipe = int(os.environ["AWB_PARENT_PIPE"])
    subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("watchdog.py")),
            str(pipe),
            os.environ["AWB_ENGINE_SECONDS"],
        ],
        pass_fds=(pipe,),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    output = Path(sys.argv[2])
    result: dict[str, object] = {"status": "engine_failed"}
    stage = "tokenizer"
    failure = None
    try:
        failure = importlib.import_module("tokenizer_cache").prepare_tokenizer(
            Path(os.environ["TIKTOKEN_CACHE_DIR"])
        )
        if failure:
            raise RuntimeError("tokenizer_cache_unavailable")
        stage = "sdk_import"
        if importlib.metadata.version("cognee") != "1.6.5":
            raise RuntimeError("engine_version")
        cognee = importlib.import_module("cognee")
        stage = "cache_check"
        config = importlib.import_module(
            "cognee.infrastructure.databases.cache.config"
        ).get_cache_config()
        if config.cache_backend != "sqlite" or "cache_backend" not in config.model_fields_set:
            raise RuntimeError("cache_configuration")
        cache = importlib.import_module(
            "cognee.infrastructure.databases.cache.get_cache_engine"
        ).get_cache_engine()
        if type(cache).__name__ != "SqlCacheAdapter":
            raise RuntimeError("cache_adapter")
        stage = "input"
        payload = json.loads(Path(sys.argv[1]).read_bytes())
        dataset = "awb_" + payload["root_id"].replace("-", "")
        if payload["operation"] == "index":
            data_item = importlib.import_module("cognee.tasks.ingestion.data_item").DataItem
            # Body authorization must never become file/URL/repository read authorization.
            stage = "add"
            await cognee.add(
                data_item(data=payload["text"], literal_text=True), dataset_name=dataset
            )
            stage = "source_lookup"
            target = next(d for d in await cognee.datasets.list_datasets() if d.name == dataset)
            data = await cognee.datasets.list_data(target.id)
            if len(data) != 1:
                raise RuntimeError("ambiguous_source")
            stage = "cognify"
            await cognee.cognify(datasets=[dataset], raise_on_error=True)
            result = {"status": "ready", "dataset_id": str(target.id), "data_id": str(data[0].id)}
        else:
            stage = "search"
            search_type = importlib.import_module("cognee.api.v1.search").SearchType
            found = await cognee.search(
                query_type=search_type.RAG_COMPLETION,
                query_text=payload["text"],
                datasets=[dataset],
                only_context=True,
                verbose=True,
                include_references=True,
                top_k=3,
            )
            stage = "output_validate"
            refs = []
            for entry in found:
                for ref in entry.get("evidence", []):
                    if not isinstance(ref.get("dataset_id"), str) or not isinstance(
                        ref.get("data_id"), str
                    ):
                        raise RuntimeError("missing_source")
                    refs.append([ref["dataset_id"], ref["data_id"]])
            result = {"status": "ready", "evidence": refs}
        result["cache"] = "sqlite"
        result["engine_version"] = "1.6.5"
    except Exception:
        # Raw SDK/provider exceptions can contain source text. Never export them.
        result = {"status": "engine_failed", "failure_code": failure or f"engine_{stage}_failed"}
    output.write_text(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(main())
