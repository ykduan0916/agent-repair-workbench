"""Independent M3 authority race regressions without external providers."""

from uuid import uuid4

import pytest
from alembic import command

from agent_workbench.experience_memory.agent_store import AgentMemoryStore
from agent_workbench.experience_memory.engine_models import Grant
from agent_workbench.run_store import StoreError
from tests.test_agent_memory import prepared_context
from tests.test_agent_memory import state as state_fixture
from tests.test_memory_engine import index, memory


@pytest.fixture
def state(tmp_path):
    yield from state_fixture.__wrapped__(tmp_path)


def test_new_conflict_outside_selected_refs_blocks_call_admission(state):
    store, _ = state
    task, _ = prepared_context(store)
    selected = store.prepare_context("analysis", task, "diagnosis")
    for suffix in ("one", "two"):
        item = memory(store, suffix=suffix, kind="project_constraint", conflict=suffix)
        index(store, item)
    with pytest.raises(StoreError), store._transaction(write=True) as connection:
        store.admit_context(connection, selected, str(uuid4()), "diagnosis", "a" * 64)
    assert store.task_usage("analysis", task)["uses"] == []


def test_0014_preserves_old_grants_and_rejects_lossy_downgrade(tmp_path):
    store = AgentMemoryStore(tmp_path / "state")
    store.initialize()
    try:
        store.register_project("p")
        store.register_connection("p", "c", "local_user")
        for purpose in ("index", "retrieve"):
            store.set_grant(
                "p",
                Grant(
                    connection_id="c",
                    content_type="episode",
                    purpose=purpose,
                    enabled=True,
                    expires_at=9999999999.0,
                ),
                expected_version=0,
            )
        with store._transaction(write=True) as connection:
            before = connection.exec_driver_sql(
                "SELECT * FROM memory_grants ORDER BY purpose"
            ).all()
            command.downgrade(store._migration(connection), "0013_web_memory")
        store.initialize()
        with store._transaction() as connection:
            assert (
                connection.exec_driver_sql("SELECT * FROM memory_grants ORDER BY purpose").all()
                == before
            )
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        store.set_grant(
            "p",
            Grant(
                connection_id="c",
                content_type="episode",
                purpose="agent_context",
                enabled=True,
                expires_at=9999999999.0,
            ),
            expected_version=0,
        )
        with pytest.raises(ValueError, match="migration_not_empty"):
            with store._transaction(write=True) as connection:
                command.downgrade(store._migration(connection), "0013_web_memory")
        with store._transaction() as connection:
            assert (
                connection.exec_driver_sql("SELECT count(*) FROM memory_grants").scalar_one() == 3
            )
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        store.close()
