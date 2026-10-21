"""REL-03: pinned local tokenizer resource; never download during a memory job."""

import hashlib
import importlib
import os
import stat
from pathlib import Path
from typing import Any

CACHE_KEY = "9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
CACHE_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
CACHE_SIZE = 1681126
WORKER_FAILURES = frozenset(
    {
        "engine_tokenizer_cache_missing",
        "engine_tokenizer_cache_invalid",
        "engine_tokenizer_failed",
        "engine_sdk_import_failed",
        "engine_cache_check_failed",
        "engine_input_failed",
        "engine_add_failed",
        "engine_source_lookup_failed",
        "engine_cognify_failed",
        "engine_search_failed",
        "engine_output_validate_failed",
    }
)


def validate_cache(directory: Path) -> str | None:
    """Return fixed diagnostics only, including for unreadable or symlinked resources."""
    path = directory / CACHE_KEY
    try:
        if directory.absolute() != directory.resolve() or not directory.is_dir():
            return "engine_tokenizer_cache_missing"
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_size != CACHE_SIZE
            or hashlib.sha256(path.read_bytes()).hexdigest() != CACHE_SHA256
        ):
            return "engine_tokenizer_cache_invalid"
    except FileNotFoundError:
        return "engine_tokenizer_cache_missing"
    except OSError:
        return "engine_tokenizer_cache_invalid"
    return None


def prepare_tokenizer(directory: Path) -> str | None:
    failure = validate_cache(directory)
    if failure:
        return failure
    os.environ["TIKTOKEN_CACHE_DIR"] = str(directory)
    loader: Any = importlib.import_module("tiktoken.load")

    def deny_download(_path: str) -> bytes:
        raise RuntimeError("tokenizer_download_disabled")

    # A different encoding or a cache disappearing must not silently enable networking.
    loader.read_file = deny_download
    importlib.import_module("tiktoken").get_encoding("cl100k_base")
    return None
