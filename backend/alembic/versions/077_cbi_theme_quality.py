"""Persist fixed quality reports and their confirmation and publication references."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "a91c8b6f25d4"
down_revision = "c26402f9243d"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cbi_theme_quality_report",
        sa.Column(
            "theme_id", sa.BigInteger(), sa.ForeignKey("cbi_theme.id"), primary_key=True
        ),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.String(128)),
        sa.Column("draft_id", sa.String(36)),
        sa.Column("revision", sa.Integer()),
        sa.Column("config_digest", sa.String(64), nullable=False),
        sa.Column("scope", JSONB(), nullable=False),
        sa.Column("receipt", JSONB(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["theme_id", "draft_id", "revision"],
            [
                "cbi_theme_draft.theme_id",
                "cbi_theme_draft.id",
                "cbi_theme_draft.revision",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["theme_id", "version"],
            ["cbi_theme_version.theme_id", "cbi_theme_version.version"],
        ),
        sa.CheckConstraint(
            "(version IS NOT NULL AND draft_id IS NULL AND revision IS NULL) OR "
            "(version IS NULL AND draft_id IS NOT NULL AND revision IS NOT NULL)",
            name="ck_cbi_theme_quality_target",
        ),
        sa.UniqueConstraint(
            "theme_id", "request_id", name="uq_cbi_theme_quality_request"
        ),
    )
    op.create_index(
        "ix_cbi_theme_quality_draft",
        "cbi_theme_quality_report",
        ["theme_id", "draft_id", "completed_at"],
    )
    op.create_index(
        "ix_cbi_theme_quality_version",
        "cbi_theme_quality_report",
        ["theme_id", "version", "completed_at"],
    )
    for table in ("cbi_theme_preview", "cbi_theme_publication"):
        op.add_column(table, sa.Column("quality_report_id", sa.String(36)))
        op.create_foreign_key(
            f"fk_{table}_quality_report",
            table,
            "cbi_theme_quality_report",
            ["theme_id", "quality_report_id"],
            ["theme_id", "id"],
        )


def downgrade():
    """Drop fixed reports and their references. A backup is needed to recover them."""
    for table in ("cbi_theme_publication", "cbi_theme_preview"):
        op.drop_constraint(f"fk_{table}_quality_report", table, type_="foreignkey")
        op.drop_column(table, "quality_report_id")
    op.drop_table("cbi_theme_quality_report")
