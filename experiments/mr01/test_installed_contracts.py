"""Opt-in installed-package contract check; no Cognee initialization or network."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / "artifacts/mr01/venv/bin/python"


@pytest.mark.skipif(
    os.environ.get("MR01_INSTALLED_CONTRACTS") != "1", reason="isolated Cognee environment required"
)
def test_explicit_sqlite_is_recorded_by_installed_settings(tmp_path):
    code = """
import importlib.util
import sysconfig
from pathlib import Path
path = Path(sysconfig.get_paths()["purelib"]) / "cognee/infrastructure/databases/cache/config.py"
spec = importlib.util.spec_from_file_location("isolated_cache_config", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
config = module.get_cache_config()
assert config.cache_backend == "sqlite"
assert "cache_backend" in config.model_fields_set
"""
    result = subprocess.run(
        [str(PYTHON), "-I", "-c", code],
        cwd=tmp_path,
        env={"CACHE_BACKEND": "sqlite", "HOME": str(tmp_path)},
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, "installed explicit cache config contract failed"
