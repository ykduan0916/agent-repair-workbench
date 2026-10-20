"""M4-R relay privacy, retry identity and network failure behavior."""

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "memory_relay", Path(__file__).parents[1] / "integrations/codex/hooks/relay.py"
)
assert spec and spec.loader
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


@pytest.fixture
def config(tmp_path):
    tmp_path.chmod(0o700)
    return {
        "endpoint": "http://127.0.0.1:8030",
        "connection_id": "tests",
        "token": "x" * 43,
        "workspace": "/approved",
        "test_commands": ["uv run pytest"],
    }


def sample(config):
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "cwd": config["workspace"],
        "tool_input": {"command": "uv run pytest"},
        "session_id": "s1",
        "tool_use_id": "t1",
        "tool_response": {"exit_code": 1, "output": "PRIVATE_OUTPUT"},
        "transcript_path": "/PRIVATE_TRANSCRIPT",
    }


def test_normalization_excludes_input_output_paths(config):
    event = relay.normalize(json.dumps(sample(config)).encode(), config)
    assert event["outcome"] == "failed"
    assert "PRIVATE" not in json.dumps(event)
    assert "/approved" not in json.dumps(event)
    assert "uv run" not in json.dumps(event)


@pytest.mark.parametrize(
    "field,value", [("cwd", "/other"), ("hook_event_name", "Stop"), ("tool_name", "Read")]
)
def test_scope_and_noncompletion_events_ignored(config, field, value):
    data = sample(config)
    data[field] = value
    assert relay.normalize(json.dumps(data).encode(), config) is None


def test_unrecognized_commands_and_prose_never_imply_success(config):
    data = sample(config)
    data["tool_input"]["command"] = "echo private"
    assert relay.normalize(json.dumps(data).encode(), config) is None
    data = sample(config)
    data["tool_response"] = "all tests passed"
    assert relay.normalize(json.dumps(data).encode(), config)["outcome"] == "unknown"


def test_redelivery_retains_initial_event_timestamp(tmp_path, config):
    q = relay.Queue(tmp_path, "tests")
    event = relay.normalize(json.dumps(sample(config)).encode(), config)
    try:
        q.add(event)
        q.add({**event, "occurred_at": event["occurred_at"] + 10})
        assert q.counts() == {"pending": 1}
        saved = json.loads(q.db.execute("SELECT body FROM events").fetchone()[0])
        assert saved == event
        with pytest.raises(relay.RelayError, match="conflict"):
            q.add({**event, "outcome": "passed"})
    finally:
        q.close()


def test_offline_preserves_pending_and_private_mode(tmp_path, config):
    q = relay.Queue(tmp_path, "tests")
    try:
        q.add(relay.normalize(json.dumps(sample(config)).encode(), config))
        q.flush({**config, "endpoint": "http://127.0.0.1:1"})
        assert q.counts() == {"pending": 1}
        assert (tmp_path / "outbox.sqlite3").stat().st_mode & 0o077 == 0
    finally:
        q.close()


def test_http_receipt_and_no_redirect(tmp_path, config):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(value)
            self.send_response(202)
            self.end_headers()
            self.wfile.write(json.dumps({"event_id": value["event_id"], "accepted": True}).encode())

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    q = relay.Queue(tmp_path, "tests")
    try:
        q.add(relay.normalize(json.dumps(sample(config)).encode(), config))
        q.flush({**config, "endpoint": f"http://127.0.0.1:{server.server_port}"})
        assert q.counts() == {"delivered": 1}
        q.flush(config)
        assert len(requests) == 1
    finally:
        q.close()
        server.shutdown()
        server.server_close()
        thread.join()


def test_symlink_and_nonprivate_config_rejected(tmp_path, config):
    path = tmp_path / "connection.json"
    path.write_text(json.dumps(config))
    path.chmod(0o644)
    with pytest.raises(relay.RelayError, match="unsafe"):
        relay.load_config(tmp_path)
    path.chmod(0o600)
    assert relay.load_config(tmp_path) == config
    path.unlink()
    path.symlink_to(tmp_path / "other")
    with pytest.raises(relay.RelayError, match="unsafe"):
        relay.load_config(tmp_path)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.com",
        "http://localhost:8030",
        "http://127.0.0.1:8030@evil.test",
        "http://127.0.0.1:8030/path",
    ],
)
def test_endpoint_restricts_loopback(tmp_path, config, endpoint):
    path = tmp_path / "connection.json"
    path.write_text(json.dumps({**config, "endpoint": endpoint}))
    path.chmod(0o600)
    with pytest.raises(relay.RelayError):
        relay.load_config(tmp_path)
