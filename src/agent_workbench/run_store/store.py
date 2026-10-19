"""P0-04: explicit SQLite snapshots, compare-and-swap leases and atomic small reports."""

from __future__ import annotations

import math
import os
import re
import secrets
import sqlite3
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import Connection, Engine, create_engine, delete, exists, inspect, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from agent_workbench.mcp_config import canonical, digest
from agent_workbench.runner.models import SandboxConfig, SandboxReport

from .models import Claim, EventView, RunDetail, RunView, StoreError
from .schema import (
    analysis_events,
    analysis_execution_links,
    analysis_summaries,
    artifacts,
    attempts,
    events,
    repair_candidate_decision_events,
    repair_candidate_decisions,
    repair_candidates,
    repair_comparison_attempts,
    repair_comparisons,
    repair_events,
    repair_requests,
    repair_tasks,
    runs,
    web_analysis_requests,
    web_repair_requests,
    web_requests,
)

REVISION = "0012_memory_engine"
MAX_REPORT = 1_048_576
TERMINAL = ("succeeded", "failed", "cancelled", "outcome_unknown")
EXECUTORS = ("absent", "running", "stopped", "unavailable", "mismatch")


def _uuid(value: str) -> str:
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError, TypeError):
        raise StoreError("invalid_id") from None
    return value


def _version(value: int) -> None:
    if type(value) is not int or value < 1:
        raise StoreError("invalid_version")


def _event_id(value: str | None) -> str:
    return _uuid(value) if value is not None else str(uuid4())


def _translate(error: SQLAlchemyError) -> StoreError:
    original = getattr(error, "orig", None)
    code = getattr(original, "sqlite_errorcode", 0) & 255
    if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        return StoreError("store_busy")
    if code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
        return StoreError("store_corrupt")
    return StoreError("store_unavailable")


class RunStore:
    def __init__(self, directory: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.directory = directory.absolute()
        self.path = self.directory / "state.sqlite3"
        self.clock = clock
        self._engine: Engine | None = None

    def _now(self) -> float:
        now = float(self.clock())
        if not math.isfinite(now) or now < 0:
            raise StoreError("invalid_clock")
        return now

    def _present(self) -> bool:
        try:
            if self.directory.is_symlink() or self.path.is_symlink():
                raise StoreError("store_unavailable")
            if not self.path.exists():
                return False
            if not stat.S_ISREG(self.path.stat().st_mode):
                raise StoreError("store_unavailable")
            return True
        except OSError:
            raise StoreError("store_unavailable") from None

    def _database(self) -> Engine:
        if self._engine is None:
            path = self.path

            def connect() -> sqlite3.Connection:
                # mode=rw prevents reads or races with deletion from silently creating a database.
                db = sqlite3.connect(
                    path.as_uri() + "?mode=rw", uri=True, timeout=1, isolation_level=None
                )
                try:
                    db.execute("PRAGMA busy_timeout=1000")
                    db.execute("PRAGMA foreign_keys=ON")
                    db.execute("PRAGMA synchronous=FULL")
                except Exception:
                    db.close()
                    raise
                return db

            self._engine = create_engine(
                "sqlite://", creator=connect, poolclass=NullPool, hide_parameters=True
            )
        return self._engine

    @contextmanager
    def _transaction(
        self, *, write: bool = False, schema_check: bool = True
    ) -> Iterator[Connection]:
        if not self._present():
            raise StoreError("not_found")
        try:
            with self._database().connect() as connection:
                # sqlite3 legacy transactions do not BEGIN on SELECT; do so explicitly.
                connection.exec_driver_sql("BEGIN IMMEDIATE" if write else "BEGIN")
                if schema_check:
                    self._check_schema(connection)
                yield connection
                connection.commit()
        except SQLAlchemyError as error:
            raise _translate(error) from None
        except (OSError, sqlite3.Error):
            raise StoreError("store_unavailable") from None

    @staticmethod
    def _check_schema(connection: Connection, *, allow_empty: bool = False) -> None:
        names = set(inspect(connection).get_table_names())
        if "alembic_version" not in names:
            if allow_empty and not names:
                return
            raise StoreError("schema_incompatible")
        revisions = list(connection.exec_driver_sql("SELECT version_num FROM alembic_version"))
        if allow_empty and not revisions and names == {"alembic_version"}:
            return
        supported = revisions == [(REVISION,)] or (
            allow_empty
            and revisions
            in (
                [("0001_run_store",)],
                [("0002_web_requests",)],
                [("0003_analyses",)],
                [("0004_repairs",)],
                [("0005_comparisons",)],
                [("0006_reproductions",)],
                [("0007_web_analyses",)],
                [("0008_web_repairs",)],
                [("0009_candidate_decisions",)],
                [("0010_decision_events",)],
                [("0011_memory_authority",)],
            )
        )
        if not supported or not {"runs", "attempts", "events", "artifacts"} <= names:
            raise StoreError("schema_incompatible")
        if (
            revisions
            in (
                [("0002_web_requests",)],
                [("0003_analyses",)],
                [("0004_repairs",)],
                [("0005_comparisons",)],
                [("0006_reproductions",)],
                [("0007_web_analyses",)],
                [("0009_candidate_decisions",)],
                [("0010_decision_events",)],
                [("0011_memory_authority",)],
                [(REVISION,)],
            )
            and "web_requests" not in names
        ):
            raise StoreError("schema_incompatible")

        if (
            revisions
            in (
                [("0003_analyses",)],
                [("0004_repairs",)],
                [("0005_comparisons",)],
                [("0006_reproductions",)],
                [("0007_web_analyses",)],
                [("0009_candidate_decisions",)],
                [("0010_decision_events",)],
                [("0011_memory_authority",)],
                [(REVISION,)],
            )
            and not {"analysis_summaries", "analysis_events", "analysis_execution_links"} <= names
        ):
            raise StoreError("schema_incompatible")
        if (
            revisions == [("0009_candidate_decisions",)]
            and "repair_candidate_decisions" not in names
        ):
            raise StoreError("schema_incompatible")
        if (
            revisions in ([("0010_decision_events",)], [("0011_memory_authority",)], [(REVISION,)])
            and not {"repair_candidate_decisions", "repair_candidate_decision_events"} <= names
        ):
            raise StoreError("schema_incompatible")

        if (
            revisions
            in (
                [("0004_repairs",)],
                [("0005_comparisons",)],
                [("0006_reproductions",)],
                [("0007_web_analyses",)],
                [("0009_candidate_decisions",)],
                [("0010_decision_events",)],
                [("0011_memory_authority",)],
                [(REVISION,)],
            )
            and not {"repair_tasks", "repair_requests", "repair_candidates", "repair_events"}
            <= names
        ):
            raise StoreError("schema_incompatible")

        if (
            revisions
            in (
                [("0005_comparisons",)],
                [("0006_reproductions",)],
                [("0007_web_analyses",)],
                [("0009_candidate_decisions",)],
                [("0010_decision_events",)],
                [("0011_memory_authority",)],
                [(REVISION,)],
            )
            and not {"repair_comparisons", "repair_comparison_attempts"} <= names
        ):
            raise StoreError("schema_incompatible")

        if revisions in (
            [("0006_reproductions",)],
            [("0007_web_analyses",)],
            [("0009_candidate_decisions",)],
            [("0010_decision_events",)],
            [("0011_memory_authority",)],
            [(REVISION,)],
        ):
            if not {"repair_reproductions", "repair_execution_identities"} <= names:
                raise StoreError("schema_incompatible")
            from .migrations.versions.v0006 import TRIGGERS

            actual = {
                row[0]: row[1]
                for row in connection.exec_driver_sql(
                    "SELECT name, sql FROM sqlite_master WHERE type='trigger'"
                )
            }
            if any(actual.get(name) != sql for name, sql in TRIGGERS.items()):
                raise StoreError("schema_incompatible")

        if revisions in (
            [("0007_web_analyses",)],
            [("0009_candidate_decisions",)],
            [("0010_decision_events",)],
            [("0011_memory_authority",)],
            [(REVISION,)],
        ):
            if "web_analysis_requests" not in names:
                raise StoreError("schema_incompatible")
            from .migrations.versions.v0007 import INDEX_SQL

            index = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name='ux_web_analysis_active'"
            ).scalar()
            if index != INDEX_SQL:
                raise StoreError("schema_incompatible")

        if revisions in ([("0011_memory_authority",)], [(REVISION,)]):
            from .migrations.versions.v0011_memory_authority import TABLES as AUTHORITY_TABLES

            if not set(AUTHORITY_TABLES) <= names:
                raise StoreError("schema_incompatible")
        if revisions == [(REVISION,)]:
            from .migrations.versions.v0012_memory_engine import TABLES as ENGINE_TABLES

            if not set(ENGINE_TABLES) <= names:
                raise StoreError("schema_incompatible")

        if revisions in ([("0010_decision_events",)], [("0011_memory_authority",)], [(REVISION,)]):
            if "web_repair_requests" not in names:
                raise StoreError("schema_incompatible")
            from .migrations.versions.v0008 import INDEX_SQL as REPAIR_INDEX_SQL

            index = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name='ux_web_repair_active'"
            ).scalar()
            if index != REPAIR_INDEX_SQL:
                raise StoreError("schema_incompatible")
            from .migrations.versions.v0008 import TRIGGERS as REPAIR_TRIGGERS

            actual = {
                row[0]: row[1]
                for row in connection.exec_driver_sql(
                    "SELECT name, sql FROM sqlite_master WHERE type='trigger'"
                )
            }
            if any(actual.get(name) != sql for name, sql in REPAIR_TRIGGERS.items()):
                raise StoreError("schema_incompatible")

    @staticmethod
    def _migration(connection: Connection) -> Config:
        config = Config()
        config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        config.attributes["connection"] = connection
        return config

    def initialize(self) -> None:
        try:
            if self.directory.is_symlink() or self.path.is_symlink():
                raise StoreError("store_unavailable")
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.directory.chmod(0o700)
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            except FileExistsError:
                if not self._present():
                    raise StoreError("store_unavailable") from None
            else:
                os.close(fd)
            self.path.chmod(0o600)
            with self._database().connect() as connection:
                # journal_mode cannot change inside an active transaction.
                connection.exec_driver_sql("PRAGMA journal_mode=WAL")
                connection.commit()
            with self._transaction(write=True, schema_check=False) as connection:
                self._check_schema(connection, allow_empty=True)
                command.upgrade(self._migration(connection), "head")
        except SQLAlchemyError as error:
            raise _translate(error) from None
        except OSError:
            raise StoreError("store_unavailable") from None

    def downgrade_empty(self) -> None:
        with self._transaction(write=True) as connection:
            from .migrations.versions.v0011_memory_authority import TABLES as AUTHORITY_TABLES
            from .migrations.versions.v0012_memory_engine import TABLES as ENGINE_TABLES

            TABLES = AUTHORITY_TABLES + ENGINE_TABLES

            if any(
                connection.exec_driver_sql(f"SELECT 1 FROM {name} LIMIT 1").first()
                for name in TABLES
            ):
                raise StoreError("migration_not_empty")
            for table in (
                runs,
                attempts,
                events,
                artifacts,
                web_requests,
                web_analysis_requests,
                web_repair_requests,
                analysis_summaries,
                analysis_events,
                analysis_execution_links,
                repair_tasks,
                repair_requests,
                repair_candidates,
                repair_events,
                repair_comparisons,
                repair_comparison_attempts,
                repair_candidate_decisions,
                repair_candidate_decision_events,
            ):
                if connection.execute(select(table).limit(1)).first() is not None:
                    raise StoreError("migration_not_empty")
            command.downgrade(self._migration(connection), "base")

    @staticmethod
    def _view(connection: Connection, run_id: str) -> RunView:
        row = connection.execute(select(runs).where(runs.c.run_id == run_id)).mappings().first()
        if row is None:
            raise StoreError("not_found")
        try:
            return RunView.model_validate(dict(row))
        except ValidationError:
            raise StoreError("store_corrupt") from None

    @staticmethod
    def _add_event(
        connection: Connection, run: RunView, kind: str, event_id: str, request_hash: str
    ) -> None:
        connection.execute(
            events.insert().values(
                event_id=event_id,
                run_id=run.run_id,
                version=run.version,
                kind=kind,
                created_at=run.updated_at,
                request_sha256=request_hash,
            )
        )

    @staticmethod
    def _duplicate(connection: Connection, run_id: str, event_id: str, request_hash: str) -> bool:
        row = (
            connection.execute(select(events).where(events.c.event_id == event_id))
            .mappings()
            .first()
        )
        if row is None:
            return False
        if row["run_id"] != run_id or row["request_sha256"] != request_hash:
            raise StoreError("event_conflict")
        return True

    def create(self, profile: str, scenario: str, config_sha256: str, case_sha256: str) -> RunView:
        try:
            SandboxConfig.model_validate({"profile": profile, "scenario": scenario})
            if any(
                re.fullmatch(r"[a-f0-9]{64}", value) is None
                for value in (config_sha256, case_sha256)
            ):
                raise ValueError
        except (ValidationError, ValueError, TypeError):
            raise StoreError("invalid_configuration") from None
        with self._transaction(write=True) as connection:
            return self._create(
                connection, str(uuid4()), profile, scenario, config_sha256, case_sha256
            )

    def _create(
        self,
        connection: Connection,
        run_id: str,
        profile: str,
        scenario: str,
        config_sha256: str,
        case_sha256: str,
    ) -> RunView:
        now = self._now()
        connection.execute(
            runs.insert().values(
                run_id=run_id,
                status="queued",
                version=1,
                created_at=now,
                updated_at=now,
                profile=profile,
                scenario=scenario,
                config_sha256=config_sha256,
                case_sha256=case_sha256,
            )
        )
        view = self._view(connection, run_id)
        self._add_event(
            connection, view, "created", str(uuid4()), digest(canonical(view.model_dump()))
        )
        return view

    def claim(self, run_id: str, expected_version: int, lease_seconds: int = 120) -> Claim:
        _uuid(run_id)
        _version(expected_version)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 120:
            raise StoreError("invalid_lease")
        with self._transaction(write=True) as connection:
            return self._claim(connection, run_id, expected_version, lease_seconds)

    def _claim(
        self, connection: Connection, run_id: str, expected_version: int, lease_seconds: int = 120
    ) -> Claim:
        now, attempt_id, token = self._now(), str(uuid4()), secrets.token_hex(32)
        result = connection.execute(
            update(runs)
            .where(
                runs.c.run_id == run_id,
                runs.c.version == expected_version,
                runs.c.status == "queued",
            )
            .values(
                status="running",
                version=expected_version + 1,
                updated_at=now,
                attempt_id=attempt_id,
                lease_until=now + lease_seconds,
            )
        )
        if result.rowcount != 1:
            raise StoreError("version_conflict")
        connection.execute(
            attempts.insert().values(
                attempt_id=attempt_id,
                run_id=run_id,
                lease_token=token,
                started_at=now,
                status="running",
            )
        )
        view = self._view(connection, run_id)
        self._add_event(
            connection,
            view,
            "claimed",
            str(uuid4()),
            digest(
                canonical(
                    {
                        "run_id": run_id,
                        "attempt_id": attempt_id,
                        "version": view.version,
                    }
                )
            ),
        )
        return Claim(run=view, token=token)

    def finish(
        self,
        run_id: str,
        attempt_id: str,
        token: str,
        expected_version: int,
        status: str,
        report: SandboxReport,
        executor_state: str,
        *,
        event_id: str | None = None,
    ) -> RunView:
        _uuid(run_id)
        _uuid(attempt_id)
        _version(expected_version)
        event_id = _event_id(event_id)
        if status not in TERMINAL or executor_state not in EXECUTORS:
            raise StoreError("invalid_status")
        try:
            report = SandboxReport.model_validate(report.model_dump(mode="python"))
            payload = canonical(report.model_dump(mode="json"))
        except (ValidationError, ValueError, TypeError):
            raise StoreError("invalid_report") from None
        if len(payload) > MAX_REPORT or report.run_id != attempt_id or report.record_id != run_id:
            raise StoreError("invalid_report")
        if (
            (status == "succeeded" and report.exit_code() != 0)
            or (status == "failed" and report.exit_code() != 1)
            or (status == "cancelled" and report.mcp.adapter_outcome == "outcome_unknown")
            or (
                executor_state == "absent"
                and (
                    report.execution.cleanup == "unconfirmed"
                    or report.execution.error == "create_unconfirmed"
                )
            )
            or (status != "outcome_unknown" and executor_state != "absent")
        ):
            raise StoreError("invalid_status")
        report_hash = digest(payload)
        request_hash = digest(
            canonical(
                {
                    "kind": "finished",
                    "run_id": run_id,
                    "attempt_id": attempt_id,
                    "token_sha256": digest(token.encode()),
                    "version": expected_version,
                    "status": status,
                    "report_sha256": report_hash,
                    "executor_state": executor_state,
                }
            )
        )
        with self._transaction(write=True) as connection:
            if self._duplicate(connection, run_id, event_id, request_hash):
                return self._view(connection, run_id)
            now = self._now()
            authorized = exists(
                select(attempts.c.attempt_id).where(
                    attempts.c.attempt_id == attempt_id,
                    attempts.c.run_id == run_id,
                    attempts.c.lease_token == token,
                    attempts.c.status == "running",
                )
            )
            result = connection.execute(
                update(runs)
                .where(
                    runs.c.run_id == run_id,
                    runs.c.status == "running",
                    runs.c.version == expected_version,
                    runs.c.attempt_id == attempt_id,
                    runs.c.lease_until > now,
                    authorized,
                )
                .values(
                    status=status,
                    version=expected_version + 1,
                    updated_at=now,
                    lease_until=None,
                    report_sha256=report_hash,
                    executor_state=executor_state,
                )
            )
            if result.rowcount != 1:
                raise StoreError("version_conflict")
            connection.execute(
                attempts.update()
                .where(attempts.c.attempt_id == attempt_id)
                .values(status=status, finished_at=now)
            )
            connection.execute(
                artifacts.insert().values(
                    run_id=run_id,
                    attempt_id=attempt_id,
                    sha256=report_hash,
                    size=len(payload),
                    payload=payload,
                )
            )
            view = self._view(connection, run_id)
            self._add_event(connection, view, "finished", event_id, request_hash)
            return view

    def reconcile(
        self,
        run_id: str,
        expected_version: int,
        executor_state: str,
        *,
        event_id: str | None = None,
    ) -> RunView:
        _uuid(run_id)
        _version(expected_version)
        if executor_state not in EXECUTORS:
            raise StoreError("invalid_status")
        event_id = _event_id(event_id)
        request_hash = digest(
            canonical(
                {
                    "kind": "reconciled",
                    "run_id": run_id,
                    "version": expected_version,
                    "executor_state": executor_state,
                }
            )
        )
        with self._transaction(write=True) as connection:
            if self._duplicate(connection, run_id, event_id, request_hash):
                return self._view(connection, run_id)
            now = self._now()
            result = connection.execute(
                update(runs)
                .where(
                    runs.c.run_id == run_id,
                    runs.c.version == expected_version,
                    ((runs.c.status == "running") & (runs.c.lease_until <= now))
                    | (runs.c.status == "outcome_unknown"),
                )
                .values(
                    status="outcome_unknown",
                    executor_state=executor_state,
                    version=expected_version + 1,
                    updated_at=now,
                    lease_until=None,
                )
            )
            if result.rowcount != 1:
                raise StoreError("version_conflict")
            view = self._view(connection, run_id)
            connection.execute(
                attempts.update()
                .where(attempts.c.run_id == run_id)
                .values(status="outcome_unknown", finished_at=now)
            )
            self._add_event(connection, view, "reconciled", event_id, request_hash)
            return view

    def list_runs(self, limit: int = 50) -> list[RunView]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise StoreError("invalid_limit")
        if not self._present():
            return []
        with self._transaction() as connection:
            ids = connection.execute(
                select(runs.c.run_id).order_by(runs.c.created_at.desc(), runs.c.run_id).limit(limit)
            ).scalars()
            return [self._view(connection, run_id) for run_id in ids]

    def detail(self, run_id: str) -> RunDetail:
        _uuid(run_id)
        with self._transaction() as connection:
            view = self._view(connection, run_id)
            rows = (
                connection.execute(
                    select(events.c.event_id, events.c.version, events.c.kind, events.c.created_at)
                    .where(events.c.run_id == run_id)
                    .order_by(events.c.version.desc())
                    .limit(100)
                )
                .mappings()
                .all()
            )
            try:
                return RunDetail(
                    run=view, events=[EventView.model_validate(dict(row)) for row in reversed(rows)]
                )
            except ValidationError:
                raise StoreError("store_corrupt") from None

    @staticmethod
    def _report(connection: Connection, view: RunView) -> SandboxReport:
        row = connection.execute(select(artifacts).where(artifacts.c.run_id == view.run_id))
        artifact = row.mappings().first()
        if artifact is None:
            raise StoreError("report_not_found")
        raw = artifact["payload"]
        if (
            not isinstance(raw, bytes)
            or len(raw) > MAX_REPORT
            or len(raw) != artifact["size"]
            or digest(raw) != artifact["sha256"]
            or artifact["sha256"] != view.report_sha256
            or artifact["attempt_id"] != view.attempt_id
        ):
            raise StoreError("report_corrupt")
        try:
            report = SandboxReport.model_validate_json(raw)
            if (
                report.run_id != view.attempt_id
                or report.record_id != view.run_id
                or canonical(report.model_dump(mode="json")) != raw
            ):
                raise ValueError
            return report
        except (ValidationError, ValueError):
            raise StoreError("report_corrupt") from None

    def report(self, run_id: str) -> SandboxReport:
        _uuid(run_id)
        with self._transaction() as connection:
            return self._report(connection, self._view(connection, run_id))

    def expired(self) -> list[RunView]:
        if not self._present():
            return []
        with self._transaction() as connection:
            now = self._now()
            ids = connection.execute(
                select(runs.c.run_id)
                .where(
                    ((runs.c.status == "running") & (runs.c.lease_until <= now))
                    | ((runs.c.status == "outcome_unknown") & (runs.c.executor_state != "absent"))
                )
                .order_by(runs.c.updated_at, runs.c.run_id)
                .limit(100)
            ).scalars()
            return [self._view(connection, run_id) for run_id in ids]

    def prune(self, days: int = 30, apply: bool = False) -> list[str]:
        if type(days) is not int or not 1 <= days <= 3650 or type(apply) is not bool:
            raise StoreError("invalid_retention")
        if not self._present():
            return []
        with self._transaction(write=apply) as connection:
            cutoff = self._now() - days * 86400
            ids = list(
                connection.execute(
                    select(runs.c.run_id)
                    .where(
                        runs.c.status.in_(TERMINAL),
                        runs.c.executor_state == "absent",
                        runs.c.updated_at < cutoff,
                        ~exists(
                            select(analysis_execution_links.c.record_id).where(
                                analysis_execution_links.c.record_id == runs.c.run_id
                            )
                        ),
                        ~exists(
                            select(web_requests.c.request_id).where(
                                web_requests.c.request_id == runs.c.run_id,
                                web_requests.c.active == 1,
                            )
                        ),
                        exists(
                            select(artifacts.c.run_id).where(artifacts.c.run_id == runs.c.run_id)
                        ),
                    )
                    .order_by(runs.c.updated_at, runs.c.run_id)
                ).scalars()
            )
            eligible = []
            for run_id in ids:
                report = self._report(connection, self._view(connection, run_id))
                if (
                    report.execution.cleanup in ("removed", "not_created")
                    and report.execution.error != "create_unconfirmed"
                ):
                    eligible.append(run_id)
            if apply and eligible:
                connection.execute(delete(runs).where(runs.c.run_id.in_(eligible)))
            return eligible

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
