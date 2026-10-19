# MR-01 Cognee contract experiment

This is an opt-in synthetic compatibility experiment, not a production memory worker.
Decision, failure history, dependency risk and remaining gates: [doc45](../../docs/45-mr-01-cognee-contract-validation.md).

## Environment

Run from the repository root. Keep the root runtime dependencies unchanged:

```bash
uv venv --python 3.12 artifacts/mr01/venv
uv pip sync --python artifacts/mr01/venv/bin/python --require-hashes -r experiments/mr01/requirements.txt
```

`uv.lock` is the independent experiment lock; `requirements.txt` is its frozen hashed export.
`dependency-manifest.json` records the actual installed platform's versions and license metadata.
The known diskcache advisory remains recorded; a successful install is not a clean security audit.

## Private configuration

The parent reads the existing `artifacts/local-service/model-env.json` through the existing service loader.
It also reads `artifacts/mr01/embedding-env.json` with `endpoint`, `model`, `api_key`, and `dimensions` keys.
Use the authorized bge-m3 service and dimensions 1024. Endpoint must be its compatible embeddings URL or base ending `/v1`.
Keep directories 0700 and files 0600; do not paste credentials in commands, examples, reports or git.
The child receives only a random loopback token. No new model download is authorized by this experiment.

## Checks

```bash
MR01_INSTALLED_CONTRACTS=1 uv run --frozen pytest experiments/mr01/test_probe.py experiments/mr01/test_installed_contracts.py -o addopts='--import-mode=importlib' -q
uv run --frozen ruff check experiments/mr01
uv run --frozen ruff format --check experiments/mr01
```

The first file uses mocks for deterministic gateway limits. The second checks the installed cache configuration without initializing Cognee or making network calls. It skips unless explicitly enabled and requires the isolated environment above.

Explicit actual provider and SDK probes (consume existing gateway quota):

```bash
.venv/bin/python experiments/mr01/provider_probe.py --execute
.venv/bin/python experiments/mr01/probe.py --execute
```

Only the hardcoded synthetic fixture is sent. Each invocation creates a fresh private UUID directory under `artifacts/mr01/`, independent of the production database. Inspect `result.json`, `calls.json` and `summary.json`; process exit0 alone is NOT acceptance. Require completed phase, nonempty mapped evidence before tombstone, zero eligible cached references afterward, dataset removed, and inspect every request state/usage. Unknown usage is never free.

Limits: 6 actual LLM and40 embedding calls, 180-second job,30-second request deadline,64KiB request and1MiB response,16 embedding inputs with4096 characters each,4096 LLM output tokens. These are experiment controls, not production token reservations or remote-cancel guarantees. There is no durable restart or cross-process admission ledger here.

`evidence-summary.json` preserves all four SDK attempts including invalid provenance gates and timeout failure. Raw run artifacts and credentials remain ignored. The safe summary's reported tokens only sum returned usage, not unknown spend; prices are unset. Do not reuse experiment data as user repair history or an efficacy benchmark.
