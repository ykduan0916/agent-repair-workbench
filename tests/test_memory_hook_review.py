"""Independent M4-R relay fault-injection review; no installed platform hooks."""

import importlib.util
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "reviewed_memory_relay", Path(__file__).parents[1] / "integrations/codex/hooks/relay.py"
)
assert spec and spec.loader
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


@pytest.fixture
def event():
    return {
        "schema_version": 1,
        "event_id": "review_event",
        "event_type": "test_completed",
        "source_run_id": "review_run",
        "occurred_at": 0.0,
        "outcome": "failed",
    }


@pytest.fixture
def queue(tmp_path, event):
    tmp_path.chmod(0o700)
    value = relay.Queue(tmp_path, "review_connection")
    value.add(event)
    try:
        yield value
    finally:
        value.close()


@contextmanager
def receiver(reply):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            event = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((event, self.headers.get("Authorization")))
            reply(self, event)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield {"endpoint": f"http://127.0.0.1:{server.server_port}", "token": "x" * 43}, calls
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        assert not worker.is_alive()


def respond(handler, status, payload):
    data = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


@pytest.mark.parametrize(
    "initial_status,payload,expected",
    [
        (409, {"error": "connection_paused"}, "paused"),
        (401, {"error": "unauthorized"}, "credential_required"),
    ],
)
def test_recoverable_rejection_retries_identical_event(
    queue, event, initial_status, payload, expected
):
    resumed = False

    def reply(handler, received):
        respond(
            handler,
            200 if resumed else initial_status,
            {"event_id": received["event_id"], "accepted": True} if resumed else payload,
        )

    with receiver(reply) as (config, calls):
        queue.flush(config)
        assert queue.counts() == {expected: 1}
        resumed = True
        queue.db.execute("UPDATE events SET next_at=0")
        queue.flush({**config, "token": "y" * 43})
        assert queue.counts() == {"delivered": 1}
        assert [body for body, _ in calls] == [event, event]
        assert calls[1][1] == "Bearer " + "y" * 43


def test_redirect_does_not_forward_credentials(queue):
    def destination(handler, received):
        respond(handler, 200, {"event_id": received["event_id"], "accepted": True})

    with receiver(destination) as (target, forwarded):

        def redirect(handler, _event):
            handler.send_response(302)
            handler.send_header("Location", target["endpoint"] + "/capture")
            handler.send_header("Content-Length", "0")
            handler.end_headers()

        with receiver(redirect) as (config, requests):
            queue.flush(config)
            assert len(requests) == 1
            assert forwarded == []
            assert queue.counts() == {"pending": 1}


@pytest.mark.parametrize("payload", [{"event_id": "wrong", "accepted": True}, [], None])
def test_malformed_success_receipt_does_not_claim_delivery(queue, payload):
    with receiver(lambda handler, _: respond(handler, 200, payload)) as (config, _):
        queue.flush(config)
        assert queue.counts() == {"pending": 1}


def test_nonobject_conflict_body_retains_retryable_event(queue):
    with receiver(lambda handler, _: respond(handler, 409, [])) as (config, _):
        queue.flush(config)
        assert queue.counts() == {"paused": 1}


def test_conflicting_event_is_not_blindly_retried(queue):
    def reply(handler, _event):
        respond(handler, 409, {"error": "idempotency_conflict"})

    with receiver(reply) as (config, requests):
        queue.flush(config)
        queue.db.execute("UPDATE events SET next_at=0")
        queue.flush(config)
        assert queue.counts() == {"rejected": 1}
        assert len(requests) == 1


def test_trickling_response_has_total_deadline(queue):
    finished = threading.Event()

    def reply(handler, _event):
        handler.send_response(200)
        handler.send_header("Content-Length", "1000")
        handler.end_headers()
        try:
            for _ in range(40):
                handler.wfile.write(b" ")
                handler.wfile.flush()
                time.sleep(0.1)
        except OSError:
            pass
        finally:
            finished.set()

    with receiver(reply) as (config, _):
        started = time.monotonic()
        queue.flush(config)
        assert time.monotonic() - started < 2.8
        assert queue.counts() == {"pending": 1}
        assert finished.wait(2)


def test_simultaneous_initialization_retains_both_events(tmp_path, monkeypatch, event):
    barrier = threading.Barrier(2)
    original = Path.exists
    failures = []

    def exists(path):
        value = original(path)
        if path.name == "outbox.sqlite3":
            barrier.wait(timeout=2)
        return value

    def add(ident):
        q = None
        try:
            q = relay.Queue(tmp_path, "review_connection")
            q.add({**event, "event_id": ident})
        except Exception as exc:
            failures.append(type(exc).__name__)
        finally:
            if q:
                q.close()

    with monkeypatch.context() as patch:
        patch.setattr(Path, "exists", exists)
        workers = [threading.Thread(target=add, args=(str(i),)) for i in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=3)
            assert not worker.is_alive()
    assert failures == []
    q = relay.Queue(tmp_path, "review_connection")
    try:
        assert q.counts() == {"pending": 2}
    finally:
        q.close()


def test_delivered_retention_does_not_exhaust_queue_and_redelivery_is_stable(tmp_path):
    config = {"workspace": "/review", "test_commands": ["pytest"]}
    hook = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "cwd": "/review",
        "tool_input": {"command": "pytest"},
        "session_id": "session",
        "tool_use_id": "call",
        "tool_response": {"exit_code": 0},
    }
    first = relay.normalize(json.dumps(hook).encode(), config)
    q = relay.Queue(tmp_path, "review_connection")
    try:
        q.add(first)
        q.db.execute("UPDATE events SET state='delivered'")
        for number in range(1001):
            q.add({**first, "event_id": str(number)})
            q.db.execute("UPDATE events SET state='delivered'")
        assert q.counts()["delivered"] <= 101
        second = relay.normalize(json.dumps(hook).encode(), config)
        assert first == second
        q.add(second)
        assert q.counts()["pending"] == 1
    finally:
        q.close()


@pytest.mark.parametrize("endpoint", [{"private": "value"}, ["private"], 123, None])
def test_invalid_config_endpoint_has_fixed_error(tmp_path, endpoint):
    config = {
        "endpoint": endpoint,
        "connection_id": "review_connection",
        "token": "x" * 43,
        "workspace": "/review",
        "test_commands": ["pytest"],
    }
    path = tmp_path / "connection.json"
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    with pytest.raises(relay.RelayError, match="endpoint_invalid"):
        relay.load_config(tmp_path)


@pytest.mark.parametrize("kind", ["test_completed", "contract_changed", "user_correction"])
def test_explicit_business_event_types_preserve_unknown_outcome(queue, event, kind):
    queue.add({**event, "event_id": kind, "event_type": kind, "outcome": "unknown"})
    stored = json.loads(
        queue.db.execute("SELECT body FROM events WHERE id=?", (kind,)).fetchone()[0]
    )
    assert stored["event_type"] == kind
    assert stored["outcome"] == "unknown"
