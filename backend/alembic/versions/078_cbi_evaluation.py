"""Persist fixed evaluation suites, batches, attempts and command receipts."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "f748d3a190e2"
down_revision = "a91c8b6f25d4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cbi_evaluation_suite",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "theme_id", sa.BigInteger(), sa.ForeignKey("cbi_theme.id"), nullable=False
        ),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.String(100), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column(
            "previous_id", sa.String(36), sa.ForeignKey("cbi_evaluation_suite.id")
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("cases", JSONB(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "theme_id", "actor_id", "request_id", name="uq_cbi_eval_suite_request"
        ),
    )
    op.create_index(
        "ix_cbi_eval_suite_scope",
        "cbi_evaluation_suite",
        ["theme_id", "workspace_id", "created_at"],
    )
    op.create_table(
        "cbi_evaluation_batch",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "theme_id", sa.BigInteger(), sa.ForeignKey("cbi_theme.id"), nullable=False
        ),
        sa.Column("workspace_id", sa.BigInteger(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.String(100), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column(
            "suite_id",
            sa.String(36),
            sa.ForeignKey("cbi_evaluation_suite.id"),
            nullable=False,
        ),
        sa.Column(
            "parent_batch_id", sa.String(36), sa.ForeignKey("cbi_evaluation_batch.id")
        ),
        sa.Column("label", sa.String(160), nullable=False),
        sa.Column("budget", sa.Integer(), nullable=False),
        sa.Column("declared_variable", sa.String(32), nullable=False),
        sa.Column("context", JSONB(), nullable=False),
        sa.Column("case_ids", JSONB(), nullable=False),
        sa.Column("remaining", JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stop_reason", sa.String(100)),
        sa.Column("worker_epoch", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "theme_id", "actor_id", "request_id", name="uq_cbi_eval_batch_request"
        ),
    )
    op.create_index(
        "ix_cbi_eval_batch_scope",
        "cbi_evaluation_batch",
        ["theme_id", "workspace_id", "created_at"],
    )
    op.create_table(
        "cbi_evaluation_attempt",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "batch_id",
            sa.String(36),
            sa.ForeignKey("cbi_evaluation_batch.id"),
            nullable=False,
        ),
        sa.Column("case_id", sa.String(100), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("observation", JSONB()),
        sa.Column("judgement", JSONB()),
        sa.Column(
            "model_calls", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "batch_id", "case_id", "number", name="uq_cbi_eval_attempt_number"
        ),
    )
    op.create_table(
        "cbi_evaluation_command",
        sa.Column(
            "batch_id",
            sa.String(36),
            sa.ForeignKey("cbi_evaluation_batch.id"),
            primary_key=True,
        ),
        sa.Column("request_id", sa.String(100), primary_key=True),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    """Drop the evaluation history. Export or back up the records before downgrade."""
    for table in (
        "cbi_evaluation_command",
        "cbi_evaluation_attempt",
        "cbi_evaluation_batch",
        "cbi_evaluation_suite",
    ):
        op.drop_table(table)
