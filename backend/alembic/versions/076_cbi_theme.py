"""Persist theme drafts, trial receipts, published definitions and publication history."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "c26402f9243d"
down_revision = "cb104b1ad437"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cbi_theme",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("oid", sa.BigInteger(), nullable=False, index=True),
        sa.Column("active_version", sa.String(128), nullable=False),
        sa.Column("source_digest", sa.String(64), nullable=False),
    )
    op.create_table(
        "cbi_theme_version",
        sa.Column(
            "theme_id", sa.BigInteger(), sa.ForeignKey("cbi_theme.id"), primary_key=True
        ),
        sa.Column("version", sa.String(128), primary_key=True),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("dependency", sa.String(64)),
        sa.Column("created_by", sa.BigInteger()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_foreign_key(
        "fk_cbi_theme_active_version",
        "cbi_theme",
        "cbi_theme_version",
        ["id", "active_version"],
        ["theme_id", "version"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "cbi_theme_draft",
        sa.Column("theme_id", sa.BigInteger(), primary_key=True),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("base_version", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["theme_id", "base_version"],
            ["cbi_theme_version.theme_id", "cbi_theme_version.version"],
        ),
    )
    op.create_index(
        "ix_cbi_theme_draft_actor",
        "cbi_theme_draft",
        ["theme_id", "actor_id", "saved_at"],
    )
    op.create_table(
        "cbi_theme_preview",
        sa.Column("theme_id", sa.BigInteger(), primary_key=True),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("draft_id", sa.String(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("dependency", sa.String(64), nullable=False),
        sa.Column("receipt", JSONB(), nullable=False),
        sa.Column("checked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("checked_by", sa.BigInteger()),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["theme_id", "draft_id", "revision"],
            [
                "cbi_theme_draft.theme_id",
                "cbi_theme_draft.id",
                "cbi_theme_draft.revision",
            ],
        ),
    )
    op.create_index(
        "ix_cbi_theme_preview_draft",
        "cbi_theme_preview",
        ["theme_id", "draft_id", "created_at"],
    )
    op.create_table(
        "cbi_theme_publication",
        sa.Column("theme_id", sa.BigInteger(), primary_key=True),
        sa.Column("request_id", sa.String(128), primary_key=True),
        sa.Column("action", sa.String(8), nullable=False),
        sa.Column("previous_version", sa.String(128), nullable=False),
        sa.Column("version", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.String(36)),
        sa.Column("revision", sa.Integer()),
        sa.Column("validation_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["theme_id", "previous_version"],
            ["cbi_theme_version.theme_id", "cbi_theme_version.version"],
        ),
        sa.ForeignKeyConstraint(
            ["theme_id", "version"],
            ["cbi_theme_version.theme_id", "cbi_theme_version.version"],
        ),
        sa.ForeignKeyConstraint(
            ["theme_id", "draft_id", "revision"],
            [
                "cbi_theme_draft.theme_id",
                "cbi_theme_draft.id",
                "cbi_theme_draft.revision",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["theme_id", "validation_id"],
            ["cbi_theme_preview.theme_id", "cbi_theme_preview.id"],
        ),
    )
    op.create_index(
        "ix_cbi_theme_publication_draft",
        "cbi_theme_publication",
        ["theme_id", "draft_id"],
    )


def downgrade():
    """Remove theme management data. Restore a database backup to recover it."""
    op.drop_table("cbi_theme_publication")
    op.drop_table("cbi_theme_preview")
    op.drop_table("cbi_theme_draft")
    op.drop_constraint("fk_cbi_theme_active_version", "cbi_theme", type_="foreignkey")
    op.drop_table("cbi_theme_version")
    op.drop_table("cbi_theme")
