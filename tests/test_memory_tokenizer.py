"""REL-03: offline tokenizer and worker diagnostic boundaries."""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_workbench.providers.cognee import tokenizer_cache, worker

spec = importlib.util.spec_from_file_location(
    "prepare_memory_tokenizer",
    Path(__file__).resolve().parents[1] / "ops/prepare_memory_tokenizer.py",
)
assert spec and spec.loader
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
install = installer.install


def test_missing_invalid_and_symlink_cache(tmp_path):
    assert tokenizer_cache.validate_cache(tmp_path) == "engine_tokenizer_cache_missing"
    path = tmp_path / tokenizer_cache.CACHE_KEY
    path.write_bytes(b"private-invalid-data")
    path.chmod(0o600)
    assert tokenizer_cache.validate_cache(tmp_path) == "engine_tokenizer_cache_invalid"
    path.unlink()
    path.symlink_to(tmp_path / "absent")
    assert tokenizer_cache.validate_cache(tmp_path) == "engine_tokenizer_cache_invalid"


def test_installer_rejects_unverified_resource(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"not-the-pinned-resource")
    with pytest.raises(ValueError, match="tokenizer_source_invalid"):
        install(source, tmp_path / "cache")
    assert not (tmp_path / "cache").exists()


def test_prepare_denies_download_even_after_successful_preflight(tmp_path, monkeypatch):
    monkeypatch.setattr(tokenizer_cache, "validate_cache", lambda _: None)
    loader = SimpleNamespace(read_file=lambda _: b"network")
    seen = []
    encoding = SimpleNamespace(get_encoding=lambda name: seen.append(name))
    monkeypatch.setattr(
        tokenizer_cache.importlib,
        "import_module",
        lambda name: loader if name == "tiktoken.load" else encoding,
    )
    assert tokenizer_cache.prepare_tokenizer(tmp_path) is None
    assert seen == ["cl100k_base"]
    with pytest.raises(RuntimeError, match="tokenizer_download_disabled"):
        loader.read_file("https://example.invalid/unexpected-encoding")


@pytest.mark.parametrize(
    "stage",
    [
        "tokenizer",
        "sdk_import",
        "cache_check",
        "input",
        "add",
        "source_lookup",
        "cognify",
        "search",
        "output_validate",
    ],
)
def test_worker_fixed_stage_never_exports_exception(tmp_path, monkeypatch, stage):
    secret = "private-source-and-provider-key"
    payload = tmp_path / "input"
    output = tmp_path / "output"
    payload.write_text(
        "{"
        if stage == "input"
        else json.dumps(
            {
                "operation": "search" if stage in {"search", "output_validate"} else "index",
                "text": "synthetic",
                "root_id": "test",
            }
        )
    )
    monkeypatch.setattr(sys, "argv", ["worker", str(payload), str(output)])
    monkeypatch.setenv("AWB_PARENT_PIPE", "0")
    monkeypatch.setenv("AWB_ENGINE_SECONDS", "10")
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(worker.subprocess, "Popen", lambda *a, **kw: None)
    monkeypatch.setattr(worker.os, "umask", lambda _: None)
    monkeypatch.setattr(worker.importlib.metadata, "version", lambda _: "1.6.5")

    def fail():
        raise RuntimeError(secret)

    async def add(*a, **kw):
        if stage == "add":
            fail()

    async def datasets():
        if stage == "source_lookup":
            fail()
        return [SimpleNamespace(name="awb_test", id="dataset")]

    async def data(*a):
        return [SimpleNamespace(id="data")]

    async def cognify(**kw):
        if stage == "cognify":
            fail()

    async def search(**kw):
        if stage == "search":
            fail()
        return [{"evidence": [{"dataset_id": None}]}]

    def module(name):
        if name == "tokenizer_cache":
            return SimpleNamespace(
                prepare_tokenizer=lambda _: fail() if stage == "tokenizer" else None
            )
        if name == "cognee":
            if stage == "sdk_import":
                fail()
            return SimpleNamespace(
                add=add,
                cognify=cognify,
                search=search,
                datasets=SimpleNamespace(list_datasets=datasets, list_data=data),
            )
        if name.endswith("cache.config"):
            if stage == "cache_check":
                fail()
            return SimpleNamespace(
                get_cache_config=lambda: SimpleNamespace(
                    cache_backend="sqlite", model_fields_set={"cache_backend"}
                )
            )
        if name.endswith("get_cache_engine"):
            return SimpleNamespace(get_cache_engine=lambda: type("SqlCacheAdapter", (), {})())
        if name.endswith("data_item"):
            return SimpleNamespace(DataItem=lambda **kw: kw)
        if name.endswith("search"):
            return SimpleNamespace(SearchType=SimpleNamespace(RAG_COMPLETION="rag"))
        raise AssertionError(name)

    monkeypatch.setattr(worker.importlib, "import_module", module)
    asyncio.run(worker.main())
    result = json.loads(output.read_text())
    assert result == {"status": "engine_failed", "failure_code": f"engine_{stage}_failed"}
    assert secret not in output.read_text()
