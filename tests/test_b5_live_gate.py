"""B5 runner's failure semantics and production-build isolation. No live model calls."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("b5_live_gate", ROOT / "ops/b5_live_gate.py")
assert SPEC and SPEC.loader
live = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live)


def test_no_implicit_budget_spend(monkeypatch):
    monkeypatch.setattr(live.sys, "argv", ["b5_live_gate.py"])
    monkeypatch.setattr(live, "run_gate", lambda _: pytest.fail("must not execute"))
    with pytest.raises(SystemExit) as error:
        live.main()
    assert error.value.code == 2


def test_readiness_checks_owned_process_before_trusting_port(monkeypatch):
    monkeypatch.setattr(live.httpx, "get", lambda *a, **k: pytest.fail("dead process"))
    with pytest.raises(live.GateFailure, match="service_exited_before_ready"):
        live.wait_ready("http://127.0.0.1:8038/api/health", SimpleNamespace(poll=lambda: 1))


def test_shutdown_escalation_remains_explicit(monkeypatch):
    signals = []
    waits = []

    def wait(timeout):
        waits.append(timeout)
        if len(waits) == 1:
            raise subprocess.TimeoutExpired("synthetic", timeout)

    monkeypatch.setattr(live.os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(live, "group_exists", lambda pid: True)
    monkeypatch.setattr(live, "wait_group_absent", lambda pid: True)
    process = SimpleNamespace(pid=123, poll=lambda: None, wait=wait)
    assert live.stop_process(process) == "killed_requires_inventory"
    assert waits == [45, 5]
    assert signals == [(123, live.signal.SIGTERM), (123, live.signal.SIGKILL)]


def test_shutdown_escalation_failure_does_not_skip_remaining_cleanup(monkeypatch):
    def wait(timeout):
        raise subprocess.TimeoutExpired("synthetic", timeout)

    def kill(pid, sig):
        if sig == live.signal.SIGKILL:
            raise ProcessLookupError

    monkeypatch.setattr(live.os, "killpg", kill)
    monkeypatch.setattr(live, "group_exists", lambda pid: True)
    process = SimpleNamespace(pid=123, poll=lambda: None, wait=wait)
    assert live.stop_process(process) == "unconfirmed"


@pytest.mark.parametrize("has_member", [False, True])
def test_eperm_uses_exact_pgid_owned_metadata_instead_of_assuming_clean(monkeypatch, has_member):
    def denied(pid, sig):
        raise PermissionError

    calls = []

    def metadata(argv, **kwargs):
        calls.append((argv, kwargs))
        rows = f" 41 41 {os.getuid()} S\n"
        if has_member:
            rows += f" 1235 123 {os.getuid()} S\n"
        return SimpleNamespace(returncode=0, stdout=rows.encode())

    monkeypatch.setattr(live.os, "killpg", denied)
    monkeypatch.setattr(live.subprocess, "run", metadata)
    assert live.group_exists(123) is has_member
    assert calls[0][0] == ["/bin/ps", "-axo", "pid=,pgid=,uid=,stat="]
    assert calls[0][1]["timeout"] == 3


@pytest.mark.parametrize(
    "status,rows",
    [
        (1, b""),
        (0, b""),
        (0, b"malformed"),
        (0, b"1235 123 999999 S\n"),
    ],
)
def test_eperm_with_unverifiable_inventory_is_never_clean(monkeypatch, status, rows):
    def denied(pid, sig):
        raise PermissionError

    monkeypatch.setattr(live.os, "killpg", denied)
    monkeypatch.setattr(
        live.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=status, stdout=rows)
    )
    with pytest.raises(live.GateFailure, match="process_group_inventory_unavailable"):
        live.group_exists(123)


def test_signal_group_eperm_only_succeeds_with_confirmed_group_absence(monkeypatch):
    def denied(pid, sig):
        raise PermissionError

    monkeypatch.setattr(live.os, "killpg", denied)
    monkeypatch.setattr(live, "group_exists", lambda pid: False)
    live.signal_group(123, live.signal.SIGTERM)
    monkeypatch.setattr(live, "group_exists", lambda pid: True)
    with pytest.raises(PermissionError):
        live.signal_group(123, live.signal.SIGTERM)


@pytest.mark.parametrize("_iteration", range(3))
def test_real_process_group_reaps_descendant_after_parent_exits(tmp_path, _iteration):
    child_path = tmp_path / "child-pid"
    source = (
        "import subprocess,sys;from pathlib import Path;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(90)']);"
        "Path(sys.argv[1]).write_text(str(child.pid))"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", source, str(child_path)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert process.wait(timeout=10) == 0
        child_pid = int(child_path.read_text())
        os.kill(child_pid, 0)
        assert live.stop_process(process) in {"terminated", "killed_requires_inventory"}
        assert not live.group_exists(process.pid)
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
    finally:
        if live.group_exists(process.pid):
            live.signal_group(process.pid, live.signal.SIGKILL)
        process.wait(timeout=5)


def test_bounded_child_timeout_owns_process_group_and_preserves_cleanup(monkeypatch):
    started, stopped = [], []

    def start(argv, **kwargs):
        started.append(kwargs)
        return SimpleNamespace(
            wait=lambda **kw: (_ for _ in ()).throw(
                subprocess.TimeoutExpired("synthetic", kw["timeout"])
            )
        )

    monkeypatch.setattr(live.subprocess, "Popen", start)
    monkeypatch.setattr(live, "stop_process", lambda p: stopped.append(p) or "terminated")
    cleanup = {}
    with pytest.raises(live.GateFailure, match="browser_timeout"):
        live.run_bounded(
            ["safe"], cwd=ROOT, env={}, output=None, timeout=1, cleanup=cleanup, stage="browser"
        )
    assert started[0]["start_new_session"] is True
    assert len(stopped) == 1 and cleanup == {"browser": "terminated"}


def test_bounded_child_unexpected_cleanup_error_remains_failure(monkeypatch):
    monkeypatch.setattr(
        live.subprocess, "Popen", lambda *a, **kw: SimpleNamespace(wait=lambda **kw: 0)
    )
    monkeypatch.setattr(live, "stop_process", lambda p: (_ for _ in ()).throw(RuntimeError()))
    cleanup = {}
    with pytest.raises(live.GateFailure, match="build_cleanup_unconfirmed"):
        live.run_bounded(
            ["safe"], cwd=ROOT, env={}, output=None, timeout=1, cleanup=cleanup, stage="build"
        )
    assert cleanup == {"build": "unconfirmed"}


@pytest.mark.parametrize("browser_code,inventory_clean", [(1, True), (0, False)])
def test_browser_or_inventory_failure_stops_both_services_restores_types_and_keeps_failure(
    tmp_path, monkeypatch, browser_code, inventory_clean
):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    next_env = frontend / "next-env.d.ts"
    original = b"// exact original\r\n"
    next_env.write_bytes(original)
    tsconfig = frontend / "tsconfig.json"
    ts_original = b'{"include": [".next/types/*.ts"]}\n'
    tsconfig.write_bytes(ts_original)
    directory = tmp_path / "evidence"
    directory.mkdir()
    monkeypatch.setattr(live, "ROOT", tmp_path)
    monkeypatch.setattr(live, "check_ports", lambda: None)
    monkeypatch.setattr(live, "read_environment", lambda p: {"AWB_MODEL_API_KEY": "private"})
    monkeypatch.setattr(live.shutil, "which", lambda p: "/bin/" + p)
    monkeypatch.setattr(live, "wait_ready", lambda *a: None)
    monkeypatch.setattr(live, "verify_browser_evidence", lambda *a: pytest.fail("failed gate"))
    calls, started, stopped = [], [], []

    def run(argv, **kwargs):
        calls.append(argv)
        assert "AWB_MODEL_API_KEY" not in kwargs["env"]
        if len(calls) == 1:
            next_env.write_text("// generated isolated path")
            tsconfig.write_text('{"include": ["isolated"]}')
        return 0 if len(calls) == 1 else browser_code

    def start(argv, **kwargs):
        process = object()
        started.append((process, kwargs))
        return process

    async def inspect(state):
        return {"confirmed": inventory_clean, "tasks": [], "task_count": 0}

    monkeypatch.setattr(live, "run_bounded", run)
    monkeypatch.setattr(live.subprocess, "Popen", start)
    monkeypatch.setattr(live, "stop_process", lambda p: stopped.append(p) or "terminated")
    monkeypatch.setattr(live, "inventory", inspect)
    code, report = live.run_gate(directory)
    assert code == 1 and report["status"] == "failed"
    assert report["browser_exit_code"] == browser_code
    assert len(started) == 2 and stopped == [started[1][0], started[0][0]]
    assert started[0][1]["env"]["AWB_MODEL_API_KEY"] == "private"
    assert "AWB_MODEL_API_KEY" not in started[1][1]["env"]
    assert next_env.read_bytes() == original and report["next_env_restored"]
    assert tsconfig.read_bytes() == ts_original
    assert "private" not in (directory / "report.json").read_text()
    assert all(item[1]["stdout"].closed for item in started)


def test_browser_exit_alone_cannot_pass_without_exact_case_count(tmp_path):
    with pytest.raises(live.GateFailure, match="unexpected_task_count"):
        live.verify_browser_evidence(tmp_path, [])


def test_one_cleanup_or_restore_failure_does_not_skip_other_resources(tmp_path, monkeypatch):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    next_env = frontend / "next-env.d.ts"
    tsconfig = frontend / "tsconfig.json"
    next_env.write_bytes(b"original types")
    tsconfig.write_bytes(b"original config")
    directory = tmp_path / "evidence"
    directory.mkdir()
    monkeypatch.setattr(live, "ROOT", tmp_path)
    stopped, inspected = [], []

    def preparation_failure():
        next_env.write_text("modified types")
        tsconfig.write_text("modified config")
        raise live.GateFailure("isolated_port_busy")

    def stop(process):
        stopped.append(process)
        if len(stopped) == 1:
            raise RuntimeError("private exception")
        return "not_started"

    original_write = Path.write_bytes

    def partial_restore(path, value):
        if path == next_env:
            raise OSError("private exception")
        return original_write(path, value)

    async def inspect(state):
        inspected.append(state)
        return {"confirmed": True, "tasks": [], "task_count": 0}

    monkeypatch.setattr(live, "check_ports", preparation_failure)
    monkeypatch.setattr(live, "stop_process", stop)
    monkeypatch.setattr(live, "inventory", inspect)
    monkeypatch.setattr(Path, "write_bytes", partial_restore)
    code, report = live.run_gate(directory)
    assert code == 1 and len(stopped) == 2 and len(inspected) == 1
    assert tsconfig.read_bytes() == b"original config"
    assert report["build_files_restored"] == {"next-env.d.ts": False, "tsconfig.json": True}
    assert report["process_cleanup"] == {"web": "unconfirmed", "api": "not_started"}
    assert "private exception" not in (directory / "report.json").read_text()


@pytest.mark.parametrize("fail_inventory", [False, True])
def test_prepare_failure_preserves_report_and_runs_cleanup(tmp_path, monkeypatch, fail_inventory):
    stopped = []
    inventory_calls = []
    monkeypatch.setattr(
        live, "check_ports", lambda: (_ for _ in ()).throw(live.GateFailure("isolated_port_busy"))
    )
    monkeypatch.setattr(live, "stop_process", lambda p: stopped.append(p) or "not_started")

    async def inspect(state):
        inventory_calls.append(state)
        if fail_inventory:
            raise RuntimeError("synthetic-sensitive-data-must-not-escape")
        return {"confirmed": True, "tasks": [], "task_count": 0}

    monkeypatch.setattr(live, "inventory", inspect)
    code, report = live.run_gate(tmp_path)
    assert code == 1 and report["status"] == "failed"
    assert report["failure_code"] == "isolated_port_busy"
    assert len(stopped) == 2 and inventory_calls == [tmp_path / "state"]
    assert "synthetic-sensitive" not in (tmp_path / "report.json").read_text()
    assert json.loads((tmp_path / "report.json").read_text()) == report
    assert (tmp_path / "report.json").stat().st_mode & 0o077 == 0


def test_build_failure_never_starts_model_and_retains_independent_state(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "check_ports", lambda: None)
    monkeypatch.setattr(live, "read_environment", lambda p: {"AWB_MODEL_API_KEY": "never-export"})
    monkeypatch.setattr(live.shutil, "which", lambda p: "/safe/bin/" + p)
    calls = []

    def fail_build(argv, **kwargs):
        calls.append((argv, kwargs))
        assert "AWB_MODEL_API_KEY" not in kwargs["env"]
        return 1

    monkeypatch.setattr(live, "run_bounded", fail_build)
    monkeypatch.setattr(live.subprocess, "Popen", lambda *a, **k: pytest.fail("no model process"))

    async def inspect(state):
        return {"confirmed": True, "tasks": [], "task_count": 0}

    monkeypatch.setattr(live, "inventory", inspect)
    code, report = live.run_gate(tmp_path)
    assert code == 1
    assert report["last_stage"] == "build"
    assert report["failure_code"] == "production_build_failed"
    assert "never-export" not in (tmp_path / "report.json").read_text()
    assert len(calls) == 1
    assert (tmp_path / "state").is_dir()


def test_next_dist_dir_rejects_paths_and_preserves_production_default():
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const ts = require('typescript');
const input = fs.readFileSync('next.config.ts', 'utf8');
const code = ts.transpileModule(input, {compilerOptions: {module: ts.ModuleKind.CommonJS,
    esModuleInterop: true}}).outputText;
function load(value) {
  const module = {exports: {}};
  const env = value === undefined ? {} : {AWB_NEXT_DIST_DIR: value};
  vm.runInNewContext(code, {module, exports: module.exports, require,
    process: {env, cwd: () => process.cwd()}});
  return module.exports.default;
}
assert.equal(load(undefined).distDir, undefined);
const safe = '.next-b5-10000000-0000-4000-8000-000000000001';
assert.equal(load(safe).distDir, safe);
for (const input of ['', '.next', '../.next', '/tmp/build', '.next-b5-test',
    safe + '/../../.next', safe + '\n', safe + '/x']) {
  assert.throws(() => load(input), /invalid_isolated_build_directory/);
}
"""
    result = subprocess.run(["node", "-e", script], cwd=ROOT / "frontend", capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
