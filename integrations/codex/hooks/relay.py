"""M4-R bounded, metadata-only event relay; stdlib, no transcript reads or commands."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import socket
import sqlite3
import stat
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

KEY = re.compile(r"^[A-Za-z0-9_-]{1,96}$")
KINDS = {"test_completed", "contract_changed", "user_correction"}


class RelayError(ValueError):
    """Fixed local failure codes only."""


def private(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RelayError("unsafe_path")


def load_config(directory: Path) -> dict:
    private(directory, directory=True)
    path = directory / "connection.json"
    private(path)
    if path.stat().st_size > 16384:
        raise RelayError("config_limit")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict) or set(value) != {
        "endpoint",
        "connection_id",
        "token",
        "workspace",
        "test_commands",
    }:
        raise RelayError("config_invalid")
    if not isinstance(value["endpoint"], str):
        raise RelayError("endpoint_invalid")
    address = urlsplit(value["endpoint"])
    if (
        address.scheme != "http"
        or address.hostname != "127.0.0.1"
        or address.port is None
        or address.path not in ("", "/")
        or address.username
        or address.password
        or address.query
        or address.fragment
    ):
        raise RelayError("endpoint_invalid")
    if not isinstance(value["connection_id"], str) or not KEY.fullmatch(value["connection_id"]):
        raise RelayError("connection_invalid")
    token = value["token"]
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
        raise RelayError("credential_invalid")
    workspace = value["workspace"]
    if not isinstance(workspace, str) or not Path(workspace).is_absolute():
        raise RelayError("workspace_invalid")
    commands = value["test_commands"]
    if (
        not isinstance(commands, list)
        or len(commands) > 20
        or any(
            not isinstance(command, str) or not 1 <= len(command) <= 1024 for command in commands
        )
    ):
        raise RelayError("commands_invalid")
    return value


def event_valid(event: object) -> dict:
    fields = {"schema_version", "event_id", "event_type", "source_run_id", "occurred_at", "outcome"}
    if not isinstance(event, dict) or set(event) != fields:
        raise RelayError("event_invalid")
    if type(event["schema_version"]) is not int or event["schema_version"] != 1:
        raise RelayError("event_invalid")
    if any(
        not isinstance(event[k], str) or not KEY.fullmatch(event[k])
        for k in ("event_id", "source_run_id")
    ):
        raise RelayError("event_invalid")
    if event["event_type"] not in KINDS or event["outcome"] not in {"passed", "failed", "unknown"}:
        raise RelayError("event_invalid")
    value = event["occurred_at"]
    if type(value) not in (int, float) or not 0 <= value <= 1e12:
        raise RelayError("event_invalid")
    return event


def normalize(raw: bytes, config: dict) -> dict | None:
    if len(raw) > 262144:
        raise RelayError("input_limit")
    hook = json.loads(raw)
    if (
        not isinstance(hook, dict)
        or hook.get("hook_event_name") != "PostToolUse"
        or hook.get("tool_name") != "Bash"
    ):
        return None
    if hook.get("cwd") != config["workspace"]:
        return None
    request = hook.get("tool_input")
    if not isinstance(request, dict) or request.get("command") not in config["test_commands"]:
        return None
    session, call = hook.get("session_id"), hook.get("tool_use_id")
    if any(not isinstance(x, str) or not 1 <= len(x) <= 256 for x in (session, call)):
        raise RelayError("identity_missing")
    response = hook.get("tool_response")
    code = response.get("exit_code") if isinstance(response, dict) else None
    # Output prose isn't a stable exit-code schema. Never infer success from text or Stop.
    outcome = "unknown" if type(code) is not int else ("passed" if code == 0 else "failed")
    return event_valid(
        {
            "schema_version": 1,
            "event_id": hashlib.sha256(json.dumps([session, call]).encode()).hexdigest(),
            "event_type": "test_completed",
            "source_run_id": hashlib.sha256(session.encode()).hexdigest(),
            "occurred_at": 0.0,  # Source time unknown; server records receipt time.
            "outcome": outcome,
        }
    )


class Queue:
    def __init__(self, directory: Path, connection_id: str):
        private(directory, directory=True)
        path = directory / "outbox.sqlite3"
        if not path.exists():
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass  # Another hook created the same private queue. Validate it below.
            else:
                os.close(fd)
        private(path)
        self.db = sqlite3.connect(path, timeout=0.1, isolation_level=None)
        self.connection_id = connection_id
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS events (connection_id TEXT, id TEXT, body TEXT NOT NULL, "
            "state TEXT NOT NULL, attempts INTEGER NOT NULL, next_at REAL NOT NULL, "
            "PRIMARY KEY(connection_id,id))"
        )

    def close(self) -> None:
        self.db.close()

    def add(self, event: dict) -> None:
        event_valid(event)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            prior = self.db.execute(
                "SELECT body FROM events WHERE connection_id=? AND id=?",
                (self.connection_id, event["event_id"]),
            ).fetchone()
            if prior:
                old = json.loads(prior[0])
                # Capture time changes on platform redelivery, semantic content must not.
                if any(old[k] != event[k] for k in event if k != "occurred_at"):
                    raise RelayError("event_conflict")
                self.db.execute("COMMIT")
                return
            self.db.execute(
                "DELETE FROM events WHERE rowid IN (SELECT rowid FROM events "
                "WHERE state='delivered' ORDER BY rowid DESC LIMIT -1 OFFSET 100)"
            )
            if self.db.execute("SELECT count(*) FROM events").fetchone()[0] >= 1000:
                raise RelayError("queue_full")
            self.db.execute(
                "INSERT INTO events VALUES(?,?,?,'pending',0,0)",
                (self.connection_id, event["event_id"], json.dumps(event, allow_nan=False)),
            )
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def flush(self, config: dict) -> None:
        deadline = time.monotonic() + 1.5
        rows = self.db.execute(
            "SELECT id,body,attempts FROM events WHERE connection_id=? "
            "AND state IN ('pending','paused','credential_required') "
            "AND next_at<=? ORDER BY rowid LIMIT 3",
            (self.connection_id, time.time()),
        ).fetchall()
        for ident, body, attempts in rows:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            address = urlsplit(config["endpoint"])
            client = http.client.HTTPConnection(
                "127.0.0.1", address.port, timeout=min(0.4, remaining)
            )
            state = "pending"
            timer = None
            try:
                client.connect()
                sock = client.sock

                def interrupt(owned=sock):
                    if owned is not None:
                        try:
                            owned.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass

                timer = threading.Timer(max(0.001, deadline - time.monotonic()), interrupt)
                timer.daemon = True
                timer.start()
                client.request(
                    "POST",
                    f"/api/integrations/{self.connection_id}/events",
                    body=body.encode(),
                    headers={
                        "Authorization": "Bearer " + config["token"],
                        "Content-Type": "application/json",
                    },
                )
                response = client.getresponse()
                data = response.read(16385)
                if len(data) <= 16384 and response.status in (200, 202):
                    receipt = json.loads(data)
                    if (
                        isinstance(receipt, dict)
                        and receipt.get("event_id") == ident
                        and receipt.get("accepted") is True
                    ):
                        state = "delivered"
                elif response.status == 401:
                    state = "credential_required"
                elif response.status == 409:
                    failure = json.loads(data) if len(data) <= 16384 else None
                    reason = failure.get("error") if isinstance(failure, dict) else None
                    state = "rejected" if reason == "idempotency_conflict" else "paused"
                elif 400 <= response.status < 500 and response.status not in (408, 429):
                    state = "rejected"
            except (OSError, http.client.HTTPException, ValueError):
                pass  # Explicit persistent pending state; no payload or exception logging.
            finally:
                if timer is not None:
                    timer.cancel()
                client.close()
            self.db.execute(
                "UPDATE events SET state=?,attempts=attempts+1,next_at=? "
                "WHERE connection_id=? AND id=? AND state!='delivered'",
                (
                    state,
                    time.time() + min(300, 2 ** min(attempts + 1, 8)),
                    self.connection_id,
                    ident,
                ),
            )

    def counts(self) -> dict:
        return dict(
            self.db.execute(
                "SELECT state,count(*) FROM events WHERE connection_id=? GROUP BY state",
                (self.connection_id,),
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Private local event relay")
    parser.add_argument("action", choices=("capture", "emit", "flush", "status"))
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    queue = None
    os.umask(0o077)
    raw_directory = args.directory or os.environ.get("PLUGIN_DATA")
    try:
        if not raw_directory:
            raise RelayError("config_missing")
        config = load_config(Path(raw_directory))
        queue = Queue(Path(raw_directory), config["connection_id"])
        if args.action in ("capture", "emit"):
            raw = sys.stdin.buffer.read(262145)
            event = (
                normalize(raw, config) if args.action == "capture" else event_valid(json.loads(raw))
            )
            if event is not None:
                queue.add(event)
        if args.action != "status":
            queue.flush(config)
        if args.action in ("emit", "status"):
            print(json.dumps({"queue": queue.counts()}))
        return 0
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, RecursionError) as error:
        # Safe local health marker makes even observational hook failures inspectable.
        if raw_directory:
            try:
                directory = Path(raw_directory)
                private(directory, directory=True)
                target = directory / "health.json"
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "w") as health:
                    json.dump(
                        {
                            "last_error": str(error)
                            if isinstance(error, RelayError)
                            else "relay_unavailable",
                            "at": time.time(),
                        },
                        health,
                    )
            except (OSError, ValueError):
                pass
        if args.action in ("emit", "status"):
            print('{"error":"relay_unavailable"}')
            return 1
        # Observing a development session must not block its tool or inject instructions.
        return 0
    finally:
        if queue is not None:
            queue.close()


if __name__ == "__main__":
    raise SystemExit(main())
