"""0007 -> 0008 conservation, transactional DDL and conservative downgrade."""

import sqlite3

import pytest
from alembic import command
from sqlalchemy import event

from agent_workbench.mcp_config import canonical, digest
from agent_workbench.run_store.models import StoreError
from agent_workbench.run_store.store import REVISION, RunStore
from tests.test_web_repair_store import admit, host

__all__ = ["host"]


def snapshot(base, tables, columns=None):
    with sqlite3.connect(base.path) as conn:
        return {
            name: conn.execute(
                f"SELECT rowid,{','.join(columns[name]) if columns else '*'} "
                f'FROM "{name}" ORDER BY rowid'
            ).fetchall()
            for name in tables
        }


def old_database(tmp_path):
    base = RunStore(tmp_path / "migration")
    base.initialize()
    with base._transaction(write=True) as conn:
        command.downgrade(base._migration(conn), "0007_web_analyses")
    return base


def seed_sentinels(base):
    # Storage-level preservation fixture: canonical JSON BLOBs (not executable
    # reports) and every old SQL table populated. Domain validity is covered by
    # the legacy store tests, not claimed for these migration sentinels.
    with sqlite3.connect(base.path) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name!='alembic_version' ORDER BY name"
            )
        ]
        for table in tables:
            if table == "repair_execution_identities":
                continue  # old triggers populate this table below
            columns = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            for number in range(2):
                identity = f"00000000-0000-0000-0000-{number:012d}"
                raw = canonical({"sentinel": table, "ordinal": number, "unicode": "保真"})
                values = []
                for _, name, kind, *_ in columns:
                    value = identity
                    if kind in ("INTEGER", "FLOAT"):
                        value = number + 1
                    if name in ("active", "cancel_requested", "consumed"):
                        value = 0
                    if name == "slot_index":
                        value = number
                    if name == "status":
                        value = "admitted" if table == "repair_comparison_attempts" else "queued"
                    if name == "case_name":
                        value = "output_mismatch"
                    if name == "intent":
                        value = "diagnose"
                    if "sha256" in name:
                        value = digest(raw)
                    if name == "attempt_id" and table == "repair_reproductions":
                        value = f"10000000-0000-0000-0000-{number:012d}"
                    if kind == "BLOB":
                        value = raw
                    values.append(value)
                marks = ",".join("?" for _ in values)
                conn.execute(f'INSERT INTO "{table}" VALUES ({marks})', values)
        return tables


def test_all_old_tables_bytes_order_and_count_preserved(tmp_path):
    base = old_database(tmp_path)
    try:
        tables = seed_sentinels(base)
        with sqlite3.connect(base.path) as conn:
            columns = {
                name: [f'"{r[1]}"' for r in conn.execute(f'PRAGMA table_info("{name}")')]
                for name in tables
            }
        before = snapshot(base, tables, columns)
        assert all(before.values())
        base.initialize()
        assert snapshot(base, tables, columns) == before
        with sqlite3.connect(base.path) as conn:
            assert conn.execute("SELECT memory_binding FROM web_analysis_requests").fetchall() == [
                (None,),
                (None,),
            ]
        with base._transaction(write=True) as conn:
            command.downgrade(base._migration(conn), "0007_web_analyses")
        assert snapshot(base, tables, columns) == before
        with pytest.raises(StoreError, match="schema_incompatible"):
            with base._transaction():
                pass
    finally:
        base.close()


def test_failed_migration_rolls_back_table_index_and_revision(tmp_path):
    base = old_database(tmp_path)
    engine = base._database()

    def fail(conn, cursor, statement, parameters, context, many):
        if statement.startswith("CREATE UNIQUE INDEX ux_web_repair_active"):
            raise RuntimeError("injected")

    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            base.initialize()
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    with engine.connect() as conn:
        assert (
            conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
            == "0007_web_analyses"
        )
        assert not conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE name='web_repair_requests'"
        ).first()
    base.initialize()
    base.close()


def test_populated_receipt_blocks_downgrade(host):
    store = host[0]
    admit(host)
    with pytest.raises(ValueError, match="migration_not_empty"):
        with store._base._transaction(write=True) as conn:
            command.downgrade(store._base._migration(conn), "0007_web_analyses")
    assert store.receipt(host[1].request_id).active


def test_get_old_schema_does_not_migrate_and_old_reader_rejects_new(tmp_path, monkeypatch):
    base = old_database(tmp_path)
    try:
        with pytest.raises(StoreError, match="schema_incompatible"):
            base.list_runs()
        with sqlite3.connect(base.path) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "0007_web_analyses",
            )
        base.initialize()
        # Emulate a reader pinned to the prior schema version; never guess
        # compatibility solely from the presence of the old tables.
        monkeypatch.setattr("agent_workbench.run_store.store.REVISION", "0007_web_analyses")
        with base._database().connect() as conn:
            assert (
                conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == REVISION
            )
            with pytest.raises(StoreError, match="schema_incompatible"):
                base._check_schema(conn)
    finally:
        base.close()
