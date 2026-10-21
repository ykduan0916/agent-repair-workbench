"""REL-03 durable, bounded embedding retries without discarding unknown usage."""

from alembic import op

revision = "0015_embedding_retries"
down_revision = "0014_agent_memory"


def upgrade() -> None:
    c = op.get_bind()
    for column, constraint in (
        ("retry_group", "length(retry_group)<=128"),
        ("request_digest", "length(request_digest)=64"),
        ("failure_code", "failure_code='provider_upstream_request_failed'"),
        ("recovered_by", "length(recovered_by)<=128"),
    ):
        c.exec_driver_sql(
            f"ALTER TABLE memory_calls ADD COLUMN {column} TEXT "
            f"CHECK({column} IS NULL OR {constraint})"
        )


def downgrade() -> None:
    c = op.get_bind()
    if c.exec_driver_sql(
        "SELECT 1 FROM memory_calls WHERE retry_group IS NOT NULL LIMIT 1"
    ).first():
        raise ValueError("migration_not_empty")
    for column in ("recovered_by", "failure_code", "request_digest", "retry_group"):
        c.exec_driver_sql(f"ALTER TABLE memory_calls DROP COLUMN {column}")
