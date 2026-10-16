"""B4 immutable candidate review decisions; no project write-back."""

import sqlalchemy as sa
from alembic import op

revision = "0009_candidate_decisions"
down_revision = "0008_web_repairs"


def upgrade() -> None:
    op.create_table(
        "repair_candidate_decisions",
        sa.Column("decision_id", sa.String(), primary_key=True),
        sa.Column("task_id", sa.String(), sa.ForeignKey("repair_tasks.task_id"), nullable=False),
        sa.Column(
            "candidate_id",
            sa.String(),
            sa.ForeignKey("repair_candidates.candidate_id"),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("sha256", sa.String(), nullable=False),
        sa.UniqueConstraint("task_id", "request_id"),
        sa.UniqueConstraint("task_id", "candidate_id", "revision"),
        sa.CheckConstraint("revision >= 1 AND revision < 256"),
        sa.CheckConstraint("length(payload) <= 32768"),
    )


def downgrade() -> None:
    if op.get_bind().exec_driver_sql("SELECT 1 FROM repair_candidate_decisions LIMIT 1").first():
        raise ValueError("migration_not_empty")
    op.drop_table("repair_candidate_decisions")
