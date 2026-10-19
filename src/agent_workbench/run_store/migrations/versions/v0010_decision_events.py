"""B4 append-only decision receipt ledger for durable idempotency."""

import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision = "0010_decision_events"
down_revision = "0009_candidate_decisions"


def upgrade() -> None:
    op.create_table(
        "repair_candidate_decision_events",
        sa.Column("event_id", sa.String(), primary_key=True),
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
        sa.UniqueConstraint("task_id", "request_id", name="ux_repair_decision_event_request"),
        sa.UniqueConstraint("task_id", "revision", name="ux_repair_decision_event_revision"),
        sa.CheckConstraint("revision >= 1 AND revision < 256"),
        sa.CheckConstraint("length(payload) <= 32768"),
    )
    op.create_index(
        "ux_repair_candidate_decision_head",
        "repair_candidate_decisions",
        ["task_id", "candidate_id"],
        unique=True,
    )
    # 0009 kept only the current head. Preserve those heads as the first
    # replayable receipts; later writes append one row per request.
    connection = op.get_bind()
    rows = connection.exec_driver_sql(
        "SELECT decision_id, task_id, candidate_id, request_id, revision, payload, sha256 "
        "FROM repair_candidate_decisions ORDER BY task_id, revision, candidate_id"
    ).mappings()
    counters: dict[str, int] = {}
    for row in rows:
        payload = json.loads(bytes(row["payload"]).decode("utf-8"))
        payload["history"] = [
            {**item, "candidate_id": row["candidate_id"]} for item in payload.get("history", [])
        ]
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        sha256 = hashlib.sha256(encoded).hexdigest()
        connection.exec_driver_sql(
            "UPDATE repair_candidate_decisions SET payload=?, sha256=? WHERE decision_id=?",
            (encoded, sha256, row["decision_id"]),
        )
        revision = counters.get(row["task_id"], 0) + 1
        counters[row["task_id"]] = revision
        connection.exec_driver_sql(
            "INSERT INTO repair_candidate_decision_events "
            "(event_id, task_id, candidate_id, request_id, revision, payload, sha256) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                row["decision_id"],
                row["task_id"],
                row["candidate_id"],
                row["request_id"],
                revision,
                encoded,
                sha256,
            ),
        )


def downgrade() -> None:
    if (
        op.get_bind()
        .exec_driver_sql("SELECT 1 FROM repair_candidate_decision_events LIMIT 1")
        .first()
    ):
        raise ValueError("migration_not_empty")
    op.drop_index("ux_repair_candidate_decision_head", table_name="repair_candidate_decisions")
    op.drop_table("repair_candidate_decision_events")
