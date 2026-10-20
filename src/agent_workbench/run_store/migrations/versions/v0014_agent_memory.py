"""M3-R agent-context permission, one-shot retrieval and body-free usage proofs."""

from alembic import op

from agent_workbench.run_store.migrations.versions.v0012_memory_engine import DDL

revision = "0014_agent_memory"
down_revision = "0013_web_memory"
TABLES = ("memory_task_bindings", "memory_model_uses", "memory_repair_drafts")


def _grants(forward: bool) -> None:
    c = op.get_bind()
    c.exec_driver_sql("ALTER TABLE memory_grants RENAME TO memory_grants_previous")
    sql = DDL[1]
    if forward:
        sql = sql.replace("('index','retrieve')", "('index','retrieve','agent_context')")
    c.exec_driver_sql(sql)
    c.exec_driver_sql("INSERT INTO memory_grants SELECT * FROM memory_grants_previous")
    c.exec_driver_sql("DROP TABLE memory_grants_previous")


def upgrade() -> None:
    c = op.get_bind()
    _grants(True)
    for table in ("web_analysis_requests", "web_repair_requests"):
        c.exec_driver_sql(
            f"ALTER TABLE {table} ADD COLUMN memory_binding BLOB "
            "CHECK(memory_binding IS NULL OR length(memory_binding)<=1024)"
        )
    c.exec_driver_sql("""CREATE TABLE memory_task_bindings (
      task_kind TEXT NOT NULL CHECK(task_kind IN ('analysis','repair')),
      task_id TEXT NOT NULL, project_id TEXT NOT NULL REFERENCES memory_projects(project_id),
      connection_id TEXT NOT NULL REFERENCES memory_connections(connection_id),
      binding_digest TEXT NOT NULL CHECK(length(binding_digest)=64),
      tool_id TEXT NOT NULL, source_version TEXT NOT NULL, allow_historical INTEGER NOT NULL,
      event_id TEXT NOT NULL, query_digest TEXT NOT NULL CHECK(length(query_digest)=64),
      job_id TEXT REFERENCES memory_jobs(job_id),
      state TEXT NOT NULL CHECK(state IN ('running','done','unknown')),
      reason TEXT NOT NULL, refs BLOB NOT NULL CHECK(length(refs)<=32768),
      created_at REAL NOT NULL, PRIMARY KEY(task_kind,task_id))""")
    c.exec_driver_sql("""CREATE TABLE memory_model_uses (
      task_kind TEXT NOT NULL, task_id TEXT NOT NULL, call_id TEXT NOT NULL,
      role TEXT NOT NULL CHECK(role IN ('diagnosis','repair','verification')),
      request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64),
      refs BLOB NOT NULL CHECK(length(refs)<=32768), admitted_at REAL NOT NULL,
      PRIMARY KEY(task_kind,task_id,call_id),
      FOREIGN KEY(task_kind,task_id) REFERENCES memory_task_bindings(task_kind,task_id))""")
    c.exec_driver_sql("""CREATE TABLE memory_repair_drafts (
      task_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
      project_id TEXT NOT NULL REFERENCES memory_projects(project_id),
      memory_id TEXT NOT NULL REFERENCES memory_heads(memory_id), request_id TEXT NOT NULL,
      fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64), decision_revision INTEGER NOT NULL,
      candidate_sha256 TEXT NOT NULL CHECK(length(candidate_sha256)=64),
      comparison_sha256 TEXT NOT NULL CHECK(length(comparison_sha256)=64), created_at REAL NOT NULL,
      PRIMARY KEY(task_id,candidate_id), UNIQUE(project_id,request_id))""")


def downgrade() -> None:
    c = op.get_bind()
    if (
        any(c.exec_driver_sql(f"SELECT 1 FROM {t} LIMIT 1").first() for t in TABLES)
        or c.exec_driver_sql(
            "SELECT 1 FROM memory_grants WHERE purpose='agent_context' LIMIT 1"
        ).first()
    ):
        raise ValueError("migration_not_empty")
    for table in ("web_analysis_requests", "web_repair_requests"):
        if c.exec_driver_sql(
            f"SELECT 1 FROM {table} WHERE memory_binding IS NOT NULL LIMIT 1"
        ).first():
            raise ValueError("migration_not_empty")
    for table in ("web_analysis_requests", "web_repair_requests"):
        c.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN memory_binding")
    for table in reversed(TABLES):
        c.exec_driver_sql(f"DROP TABLE {table}")
    _grants(False)
