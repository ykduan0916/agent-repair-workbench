"""M2-R explicit authorization, eligibility policy and recoverable engine roots."""

from alembic import op

revision = "0012_memory_engine"
down_revision = "0011_memory_authority"
TABLES = (
    "memory_grants",
    "memory_policies",
    "memory_operations",
    "memory_engine_roots",
    "memory_engine_config",
)
DDL = (
    """CREATE TABLE memory_engine_config (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        root_digest TEXT NOT NULL CHECK(length(root_digest)=64))""",
    """CREATE TABLE memory_grants (
        project_id TEXT NOT NULL, connection_id TEXT NOT NULL,
        content_type TEXT NOT NULL CHECK(content_type IN
            ('query','episode','repair_experience','project_constraint')),
        purpose TEXT NOT NULL CHECK(purpose IN ('index','retrieve')),
        version INTEGER NOT NULL CHECK(version>=1),
        enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
        expires_at REAL NOT NULL CHECK(expires_at>=0),
        PRIMARY KEY(project_id,connection_id,content_type,purpose),
        FOREIGN KEY(project_id,connection_id)
            REFERENCES memory_connections(project_id,connection_id))""",
    """CREATE TABLE memory_policies (
        memory_id TEXT PRIMARY KEY REFERENCES memory_heads(memory_id),
        version INTEGER NOT NULL CHECK(version>=1), generation INTEGER NOT NULL,
        payload BLOB NOT NULL CHECK(length(payload)<=2048))""",
    """CREATE TABLE memory_operations (
        job_id TEXT PRIMARY KEY REFERENCES memory_jobs(job_id),
        operation TEXT NOT NULL CHECK(operation IN ('index','search')),
        snapshot BLOB NOT NULL CHECK(length(snapshot)<=65536), failure_code TEXT)""",
    """CREATE TABLE memory_engine_roots (
        root_id TEXT PRIMARY KEY REFERENCES memory_operations(job_id),
        project_id TEXT NOT NULL, memory_id TEXT NOT NULL, generation INTEGER NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('staged','ready','removing','removed')),
        dataset_id TEXT, data_id TEXT,
        profile_digest TEXT NOT NULL CHECK(length(profile_digest)=64),
        FOREIGN KEY(project_id,memory_id) REFERENCES memory_heads(project_id,memory_id))""",
)


def upgrade() -> None:
    for sql in DDL:
        op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    c = op.get_bind()
    if any(c.exec_driver_sql(f"SELECT 1 FROM {t} LIMIT 1").first() for t in TABLES):
        raise ValueError("migration_not_empty")
    for table in reversed(TABLES):
        c.exec_driver_sql(f"DROP TABLE {table}")
