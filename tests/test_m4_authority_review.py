"""Independent M4 authority regressions; no model or production database."""

from agent_workbench.experience_memory.models import EventInput
from agent_workbench.web_memory.store import WebMemoryStore


def test_full_project_cannot_starve_other_project_events(tmp_path):
    store = WebMemoryStore(tmp_path / "state")
    store.initialize()
    try:
        for project, connection in (("full", "full-source"), ("healthy", "healthy-source")):
            store.register_project(project)
            store.register_connection(project, connection, "external_report")
        # Authority retains deleted heads; hitting capacity is an allowed terminal history.
        with store._transaction(write=True) as db:
            db.exec_driver_sql(
                "INSERT INTO memory_heads VALUES(?, 'full', 'episode',1,1,'deleted',0)",
                [(f"memory-{i}",) for i in range(10000)],
            )
        for i in range(21):
            full = i < 20
            store.ingest(
                "full-source" if full else "healthy-source",
                EventInput(
                    event_id=f"event-{i}",
                    event_type="test_completed",
                    source_run_id="run",
                    occurred_at=1.0,
                    outcome="failed",
                ),
                project_id="full" if full else "healthy",
            )
        store.process_pending_events()
        store.process_pending_events()
        healthy = store.overview("healthy")
        assert healthy["events"][0]["processing"] == "done"
        assert len(healthy["memories"]) == 1
        token = store.credential("full", "full-source", 0, rotate=True)["token"]
        assert store.event_receipt("full-source", "event-0", token)["processing"] == (
            "capacity_limited"
        )
        assert (
            store.ingest(
                "full-source",
                EventInput(
                    event_id="event-0",
                    event_type="test_completed",
                    source_run_id="run",
                    occurred_at=1.0,
                    outcome="failed",
                ),
                token=token,
            )["processing"]
            == "capacity_limited"
        )
    finally:
        store.close()


def test_memory_beyond_first_page_remains_mutable(tmp_path):
    store = WebMemoryStore(tmp_path / "state")
    store.initialize()
    try:
        store.register_project("p")
        store.register_connection("p", "c", "external_report")
        for i in range(101):
            store.ingest(
                "c",
                EventInput(
                    event_id=f"event-{i}",
                    event_type="user_correction",
                    source_run_id="run",
                    occurred_at=1.0,
                    outcome="unknown",
                ),
                project_id="p",
            )
        while store.process_pending_events():
            pass
        first = store.overview("p")
        second = store.overview("p", pages={"memories": 1})
        assert first["pagination"]["memories"]["next_page"] == 1
        assert len(second["memories"]) == 1
        item = second["memories"][0]
        mid = item["receipt"]["resource_id"]
        assert mid not in {row["receipt"]["resource_id"] for row in first["memories"]}
        changed = store.change_memory("p", mid, "confirm", 1, state="confirmed")
        assert changed.state == "confirmed" and changed.version == 2
        assert store.memory("p", mid).receipt == changed
    finally:
        store.close()
