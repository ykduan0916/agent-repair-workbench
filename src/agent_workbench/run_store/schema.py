"""SQLAlchemy Core schema; runtime queries never expose private attempt rows."""

from sqlalchemy import Column, Float, ForeignKey, Integer, LargeBinary, MetaData, String, Table

metadata = MetaData()
runs = Table(
    "runs",
    metadata,
    Column("run_id", String, primary_key=True),
    Column("status", String, nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    Column("profile", String, nullable=False),
    Column("scenario", String, nullable=False),
    Column("config_sha256", String, nullable=False),
    Column("case_sha256", String, nullable=False),
    Column("attempt_id", String),
    Column("lease_until", Float),
    Column("report_sha256", String),
    Column("executor_state", String),
)
attempts = Table(
    "attempts",
    metadata,
    Column("attempt_id", String, primary_key=True),
    Column("run_id", ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False, unique=True),
    Column("lease_token", String, nullable=False),
    Column("started_at", Float, nullable=False),
    Column("finished_at", Float),
    Column("status", String, nullable=False),
)
events = Table(
    "events",
    metadata,
    Column("event_id", String, primary_key=True),
    Column("run_id", ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("kind", String, nullable=False),
    Column("created_at", Float, nullable=False),
    Column("request_sha256", String, nullable=False),
)
artifacts = Table(
    "artifacts",
    metadata,
    Column("run_id", ForeignKey("runs.run_id", ondelete="CASCADE"), primary_key=True),
    Column("attempt_id", ForeignKey("attempts.attempt_id", ondelete="CASCADE"), nullable=False),
    Column("sha256", String, nullable=False),
    Column("size", Integer, nullable=False),
    Column("payload", LargeBinary, nullable=False),
)

# Deliberately no FK: deduplication tombstones survive run pruning.
web_requests = Table(
    "web_requests",
    metadata,
    Column("request_id", String, primary_key=True),
    Column("payload_sha256", String, nullable=False),
    Column("active", Integer, nullable=False),
    Column("cancel_requested", Integer, nullable=False),
)

analysis_summaries = Table(
    "analysis_summaries",
    metadata,
    Column("task_id", String, primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("summary_json", LargeBinary, nullable=False),
    Column("sha256", String, nullable=False),
    Column("updated_at", Float, nullable=False),
)
analysis_events = Table(
    "analysis_events",
    metadata,
    Column("task_id", ForeignKey("analysis_summaries.task_id"), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("event_json", LargeBinary, nullable=False),
    Column("sha256", String, nullable=False),
    Column("created_at", Float, nullable=False),
)
analysis_execution_links = Table(
    "analysis_execution_links",
    metadata,
    Column("record_id", ForeignKey("runs.run_id"), primary_key=True),
    Column("task_id", ForeignKey("analysis_summaries.task_id"), nullable=False),
    Column("request_id", String, nullable=False),
    Column("payload_sha256", String, nullable=False),
)

# P0-06 independent ledger. Old analysis rows and terminal meanings remain unchanged.
repair_tasks = Table(
    "repair_tasks",
    metadata,
    Column("task_id", String, primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("payload", LargeBinary, nullable=False),
    Column("sha256", String, nullable=False),
)
repair_requests = Table(
    "repair_requests",
    metadata,
    Column("task_id", ForeignKey("repair_tasks.task_id"), primary_key=True),
    Column("request_id", String, primary_key=True),
    Column("payload", LargeBinary, nullable=False),
    Column("sha256", String, nullable=False),
)
repair_candidates = Table(
    "repair_candidates",
    metadata,
    Column("candidate_id", String, primary_key=True),
    Column("task_id", ForeignKey("repair_tasks.task_id"), nullable=False),
    Column("request_id", String, nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("payload", LargeBinary, nullable=False),
    Column("sha256", String, nullable=False),
)
repair_events = Table(
    "repair_events",
    metadata,
    Column("task_id", ForeignKey("repair_tasks.task_id"), primary_key=True),
    Column("revision", Integer, primary_key=True),
    Column("payload", LargeBinary, nullable=False),
    Column("sha256", String, nullable=False),
)

repair_comparisons = Table(
    "repair_comparisons",
    metadata,
    Column("comparison_id", String, primary_key=True),
    Column("task_id", ForeignKey("repair_tasks.task_id"), nullable=False),
    Column("request_id", String, nullable=False),
    Column(
        "candidate_id", ForeignKey("repair_candidates.candidate_id"), nullable=False, unique=True
    ),
    Column("payload", LargeBinary, nullable=False),
    Column("sha256", String, nullable=False),
)
repair_comparison_attempts = Table(
    "repair_comparison_attempts",
    metadata,
    Column("attempt_id", String, primary_key=True),
    Column("comparison_id", ForeignKey("repair_comparisons.comparison_id"), nullable=False),
    Column("slot_index", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("report_sha256", String),
)

repair_candidate_decisions = Table(
    "repair_candidate_decisions",
    metadata,
    Column("decision_id", String, primary_key=True),
    Column("task_id", ForeignKey("repair_tasks.task_id"), nullable=False),
    Column("candidate_id", ForeignKey("repair_candidates.candidate_id"), nullable=False),
    Column("request_id", String, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("payload", LargeBinary, nullable=False),
    Column("sha256", String, nullable=False),
)

# Append-only receipt ledger. The decision table is the current head; this table
# keeps every request receipt so retries remain idempotent after later revisions.
repair_candidate_decision_events = Table(
    "repair_candidate_decision_events",
    metadata,
    Column("event_id", String, primary_key=True),
    Column("task_id", ForeignKey("repair_tasks.task_id"), nullable=False),
    Column("candidate_id", ForeignKey("repair_candidates.candidate_id"), nullable=False),
    Column("request_id", String, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("payload", LargeBinary, nullable=False),
    Column("sha256", String, nullable=False),
)

repair_execution_identities = Table(
    "repair_execution_identities",
    metadata,
    Column("attempt_id", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("owner_id", String, nullable=False),
)
repair_reproductions = Table(
    "repair_reproductions",
    metadata,
    Column("task_id", String, ForeignKey("repair_tasks.task_id"), primary_key=True),
    Column("request_id", String, primary_key=True),
    Column("attempt_id", String, nullable=False, unique=True),
    Column("payload", LargeBinary, nullable=False),
    Column("sha256", String, nullable=False),
)

# No FK: browser request tombstones remain when analysis history is pruned.
web_analysis_requests = Table(
    "web_analysis_requests",
    metadata,
    Column("request_id", String, primary_key=True),
    Column("payload_sha256", String, nullable=False),
    Column("case_name", String, nullable=False),
    Column("intent", String, nullable=False),
    Column("policy_sha256", String, nullable=False),
    Column("initial_sha256", String, nullable=False),
    Column("claim_sha256", String, nullable=False),
    Column("consumed", Integer, nullable=False),
    Column("active", Integer, nullable=False),
    Column("cancel_requested", Integer, nullable=False),
)


# No FK: browser request tombstones remain when repair history is pruned.
web_repair_requests = Table(
    "web_repair_requests",
    metadata,
    Column("request_id", String, primary_key=True),
    Column("payload_sha256", String, nullable=False),
    Column("case_id", String, nullable=False),
    Column("mode", String, nullable=False),
    Column("policy_sha256", String, nullable=False),
    Column("initial_sha256", String, nullable=False),
    Column("claim_sha256", String, nullable=False),
    Column("consumed", Integer, nullable=False),
    Column("active", Integer, nullable=False),
    Column("cancel_requested", Integer, nullable=False),
)
