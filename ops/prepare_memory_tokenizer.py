"""Install the pinned tokenizer from an existing local file. No network or models."""

import argparse
import hashlib
import os
import tempfile
from pathlib import Path

from agent_workbench.providers.cognee.tokenizer_cache import (
    CACHE_KEY,
    CACHE_SHA256,
    CACHE_SIZE,
    validate_cache,
)


def install(source: Path, directory: Path) -> None:
    if source.stat().st_size != CACHE_SIZE:
        raise ValueError("tokenizer_source_invalid")
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != CACHE_SHA256:
        raise ValueError("tokenizer_source_invalid")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.absolute() != directory.resolve():
        raise ValueError("tokenizer_destination_invalid")
    # Never overwrite an existing resource, including a symlink.
    target = directory / CACHE_KEY
    if target.exists():
        if validate_cache(directory):
            raise ValueError("tokenizer_destination_invalid")
        return
    fd, temporary = tempfile.mkstemp(dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            if validate_cache(directory):
                raise ValueError("tokenizer_destination_invalid") from None
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--sdk-python", type=Path, required=True)
    args = parser.parse_args()
    install(args.source, args.sdk_python.absolute().parent.parent / "share/awb-tokenizers")
    print("tokenizer_cache_ready")


if __name__ == "__main__":
    main()
