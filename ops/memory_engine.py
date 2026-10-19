"""M2-R explicit local engine commands; no HTTP/Hook installation or automatic ingestion."""

import argparse
import json
import os
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent_workbench.experience_memory.engine_models import Query  # noqa: E402
from agent_workbench.experience_memory.engine_store import EngineStore  # noqa: E402
from agent_workbench.providers.cognee.gateway import Providers  # noqa: E402
from agent_workbench.providers.cognee.runtime import CogneeRuntime  # noqa: E402
from ops.local_service import read_environment  # noqa: E402


def load_providers(model_path: Path, embedding_path: Path, *, profile: bool) -> Providers:
    model = read_environment(model_path)
    info = embedding_path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or info.st_size > 16384
    ):
        raise ValueError("private_embedding_configuration_required")
    embedding = json.loads(embedding_path.read_bytes())
    if (
        not isinstance(embedding, dict)
        or type(embedding.get("dimensions")) is not int
        or embedding.get("dimensions") != 1024
        or not isinstance(embedding.get("revision", "bge-m3-1024-host-v1"), str)
        or any(
            not isinstance(embedding.get(k), str) or not embedding[k]
            for k in ("endpoint", "api_key", "model")
        )
    ):
        raise ValueError("invalid_embedding_configuration")
    base = model["AWB_MODEL_BASE_URL"].rstrip("/")
    endpoint = base + ("/chat/completions" if base.endswith("/v1") else "/v1/chat/completions")
    embed = embedding["endpoint"].rstrip("/")
    return Providers(
        endpoint,
        model["AWB_MODEL_API_KEY"],
        model["AWB_MODEL_NAME"],
        embed if embed.endswith("/embeddings") else embed + "/embeddings",
        embedding["api_key"],
        embedding["model"],
        profile,
        embedding.get("revision", "bge-m3-1024-host-v1"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["index", "search", "cleanup"])
    parser.add_argument("--execute", action="store_true", required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--engine-root", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--sdk-python", type=Path, required=True)
    parser.add_argument("--model-config", type=Path)
    parser.add_argument("--embedding-config", type=Path)
    parser.add_argument("--accept-conditional-token-profile", action="store_true")
    parser.add_argument("--memory-id")
    parser.add_argument("--query-file", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    store = None
    try:
        providers = None
        if args.command != "cleanup":
            if args.model_config is None or args.embedding_config is None:
                raise ValueError("provider_configuration_required")
            providers = load_providers(
                args.model_config,
                args.embedding_config,
                profile=args.accept_conditional_token_profile,
            )

        store = EngineStore(args.state.resolve())
        runtime = CogneeRuntime(
            store, args.engine_root.absolute(), args.sdk_python.absolute(), providers
        )
        if args.command == "index":
            job = runtime.index(args.project, args.memory_id)
            report = {"job_id": job, "accounting": store.accounting(args.project, job)}
        elif args.command == "cleanup":
            report = {"removed_roots": runtime.cleanup(args.project)}
        else:
            if not args.query_file or args.query_file.stat().st_size > 8192:
                raise ValueError("bounded_query_required")
            result = runtime.search(
                args.project, Query.model_validate_json(args.query_file.read_bytes())
            )
            # CLI prints metadata only. Host SDK may consume body as explicitly untrusted context.
            report = result.model_dump(exclude={"references": {"__all__": {"body"}}})
            if result.job_id:
                report["accounting"] = store.accounting(args.project, result.job_id)
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report.get("status") not in ("limited", "unavailable") else 1
    except Exception:
        print('{"status":"unavailable","reason":"memory_command_failed"}')
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
