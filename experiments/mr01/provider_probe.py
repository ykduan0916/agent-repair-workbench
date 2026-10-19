"""Live provider-shape check only. Does not claim Cognee SDK compatibility."""

import argparse
import json
import math
import os
import uuid

from probe import ROOT, Gate, read_environment


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    if not parser.parse_args().execute:
        parser.error("--execute required")
    os.umask(0o077)
    directory = ROOT / "artifacts/mr01" / ("providers-" + uuid.uuid4().hex)
    directory.mkdir(mode=0o700)
    gate = Gate(
        directory,
        read_environment(ROOT / "artifacts/local-service/model-env.json"),
        json.loads((ROOT / "artifacts/mr01/embedding-env.json").read_text()),
    )
    report: dict[str, object] = {"kind": "live_provider_shapes_not_cognee"}
    try:
        status, response = gate.forward(
            "/v1/embeddings",
            json.dumps(
                {
                    "input": ["Synthetic count mismatch.", "合成计数类型错误。"],
                    "encoding_format": "float",
                }
            ).encode(),
        )
        report["embedding_http"] = status
        if status == 200:
            value = json.loads(response)
            vectors = [item["embedding"] for item in value["data"]]
            report["embedding_contract_pass"] = len(vectors) == 2 and all(
                len(v) == 1024 and all(type(x) in {float, int} and math.isfinite(x) for x in v)
                for v in vectors
            )
            report["indices_present"] = all("index" in item for item in value["data"])
        status, response = gate.forward(
            "/v1/chat/completions",
            json.dumps(
                {
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Return JSON with symptom and remedy strings. Synthetic data only."
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                "Synthetic tool returned count as string. "
                                "Integer conversion fixed the fixture test."
                            ),
                        },
                    ],
                    "response_format": {"type": "json_object"},
                }
            ).encode(),
        )
        report["llm_http"] = status
        if status == 200:
            value = json.loads(json.loads(response)["choices"][0]["message"]["content"])
            report["llm_json_contract_pass"] = isinstance(value, dict) and all(
                isinstance(value.get(k), str) and value[k] for k in ("symptom", "remedy")
            )
    except Exception as error:
        report["error_type"] = type(error).__name__
    finally:
        gate.stop()
        (directory / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"artifact": str(directory), **report}))


if __name__ == "__main__":
    main()
