"""M4-R source credentials and durable external-event processing receipts."""

from alembic import op

revision = "0013_web_memory"
down_revision = "0012_memory_engine"
TABLES = ("memory_credentials", "memory_event_processing", "memory_web_operations")


def upgrade() -> None:
    c = op.get_bind()
    c.exec_driver_sql("""CREATE TABLE memory_credentials (
      connection_id TEXT PRIMARY KEY REFERENCES memory_connections(connection_id),
      version INTEGER NOT NULL CHECK(version>=1), token_hash TEXT,
      CHECK(token_hash IS NULL OR length(token_hash)=64))""")
    c.exec_driver_sql("""CREATE TABLE memory_event_processing (
      connection_id TEXT NOT NULL, event_id TEXT NOT NULL,
      job_id TEXT NOT NULL UNIQUE REFERENCES memory_jobs(job_id),
      memory_id TEXT REFERENCES memory_heads(memory_id),
      state TEXT NOT NULL CHECK(state IN ('pending','done')),
      PRIMARY KEY(connection_id,event_id), FOREIGN KEY(connection_id,event_id)
      REFERENCES memory_events(connection_id,event_id))""")

    c.exec_driver_sql("""CREATE TABLE memory_web_operations (
      project_id TEXT NOT NULL REFERENCES memory_projects(project_id),
      request_id TEXT NOT NULL, fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64),
      operation TEXT NOT NULL CHECK(operation IN ('index','search','cleanup')),
      state TEXT NOT NULL CHECK(state IN ('running','done','unknown')),
      job_id TEXT REFERENCES memory_jobs(job_id),
      result BLOB CHECK(result IS NULL OR length(result)<=65536), created_at REAL NOT NULL,
      PRIMARY KEY(project_id,request_id))""")


def downgrade() -> None:
    c = op.get_bind()
    if any(c.exec_driver_sql(f"SELECT 1 FROM {t} LIMIT 1").first() for t in TABLES):
        raise ValueError("migration_not_empty")
    for table in reversed(TABLES):
        c.exec_driver_sql(f"DROP TABLE {table}")
