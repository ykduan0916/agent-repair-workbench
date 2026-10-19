"""M1-R additive authority and event ledger. Frozen SQL; no domain imports."""

from alembic import op

revision = "0011_memory_authority"
down_revision = "0010_decision_events"

TABLES = (
    "memory_projects",
    "memory_connections",
    "memory_events",
    "memory_heads",
    "memory_revisions",
    "memory_receipts",
    "memory_jobs",
    "memory_projections",
    "memory_calls",
)
DDL = (
    """CREATE TABLE memory_projects (
        project_id TEXT PRIMARY KEY NOT NULL, created_at REAL NOT NULL)""",
    """CREATE TABLE memory_connections (
        connection_id TEXT PRIMARY KEY NOT NULL,
        project_id TEXT NOT NULL REFERENCES memory_projects(project_id),
        origin TEXT NOT NULL CHECK(origin IN ('external_report','local_user','synthetic')),
        enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
        version INTEGER NOT NULL CHECK(version >= 1),
        UNIQUE(project_id,connection_id))""",
    """CREATE TABLE memory_events (
        connection_id TEXT NOT NULL REFERENCES memory_connections(connection_id),
        event_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
        payload BLOB NOT NULL CHECK(length(payload)<=16384), received_at REAL NOT NULL,
        PRIMARY KEY(connection_id,event_id))""",
    """CREATE TABLE memory_heads (
        memory_id TEXT PRIMARY KEY NOT NULL,
        project_id TEXT NOT NULL REFERENCES memory_projects(project_id),
        kind TEXT NOT NULL CHECK(kind IN ('episode','repair_experience','project_constraint')),
        revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 64),
        version INTEGER NOT NULL CHECK(version BETWEEN 1 AND 1000000),
        state TEXT NOT NULL CHECK(state IN ('draft','confirmed','revoked','deleted')),
        updated_at REAL NOT NULL, UNIQUE(project_id,memory_id))""",
    """CREATE TABLE memory_revisions (
        memory_id TEXT NOT NULL REFERENCES memory_heads(memory_id),
        revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 64),
        connection_id TEXT NOT NULL, event_id TEXT NOT NULL,
        digest TEXT NOT NULL CHECK(length(digest)=64),
        payload BLOB CHECK(length(payload)<=32768),
        PRIMARY KEY(memory_id,revision),
        FOREIGN KEY(connection_id,event_id) REFERENCES memory_events(connection_id,event_id))""",
    """CREATE TABLE memory_receipts (
        project_id TEXT NOT NULL REFERENCES memory_projects(project_id),
        request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
        payload BLOB NOT NULL CHECK(length(payload)<=1024),
        PRIMARY KEY(project_id,request_id))""",
    """CREATE TABLE memory_jobs (
        job_id TEXT PRIMARY KEY NOT NULL,
        project_id TEXT NOT NULL REFERENCES memory_projects(project_id),
        kind TEXT NOT NULL CHECK(kind IN ('event','project','cleanup')),
        memory_id TEXT REFERENCES memory_heads(memory_id),
        connection_id TEXT NOT NULL REFERENCES memory_connections(connection_id),
        event_id TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>=0),
        state TEXT NOT NULL
            CHECK(state IN ('pending','leased','done','failed','cancelled','unknown')),
        attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt BETWEEN 0 AND 3),
        lease_hash TEXT, lease_until REAL, created_at REAL NOT NULL,
        tokens INTEGER NOT NULL DEFAULT 0 CHECK(tokens BETWEEN 0 AND 100000),
        FOREIGN KEY(connection_id,event_id) REFERENCES memory_events(connection_id,event_id))""",
    """CREATE TABLE memory_projections (
        project_id TEXT NOT NULL, memory_id TEXT NOT NULL, revision INTEGER NOT NULL,
        generation INTEGER NOT NULL, digest TEXT NOT NULL,
        dataset_id TEXT NOT NULL, data_id TEXT NOT NULL,
        PRIMARY KEY(dataset_id,data_id),
        FOREIGN KEY(project_id,memory_id) REFERENCES memory_heads(project_id,memory_id),
        FOREIGN KEY(memory_id,revision) REFERENCES memory_revisions(memory_id,revision))""",
    """CREATE TABLE memory_calls (
        job_id TEXT NOT NULL REFERENCES memory_jobs(job_id), call_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('llm','embedding')),
        reserved_tokens INTEGER NOT NULL CHECK(reserved_tokens BETWEEN 1 AND 65536),
        state TEXT NOT NULL CHECK(state IN ('reserved','received','unknown')),
        actual_tokens INTEGER CHECK(actual_tokens>=0),
        PRIMARY KEY(job_id,call_id))""",
    "CREATE INDEX memory_job_ready ON memory_jobs(project_id,state,created_at)",
    "CREATE INDEX memory_head_scope ON memory_heads(project_id,state,updated_at)",
)


def upgrade() -> None:
    for sql in DDL:
        op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    connection = op.get_bind()
    if any(connection.exec_driver_sql(f"SELECT 1 FROM {name} LIMIT 1").first() for name in TABLES):
        raise ValueError("migration_not_empty")
    for name in reversed(TABLES):
        connection.exec_driver_sql(f"DROP TABLE {name}")
