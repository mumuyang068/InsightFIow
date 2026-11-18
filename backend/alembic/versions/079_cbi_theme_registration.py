"""Persist first-registration intent and availability separately from publication."""

import sqlalchemy as sa

from alembic import op

revision = "93b5a4f731c2"
down_revision = "f748d3a190e2"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "cbi_theme", "active_version", existing_type=sa.String(128), nullable=True
    )
    op.add_column("cbi_theme", sa.Column("initial_version", sa.String(128)))
    op.add_column("cbi_theme", sa.Column("registration_request_id", sa.String(100)))
    op.add_column("cbi_theme", sa.Column("registration_digest", sa.String(64)))
    op.add_column(
        "cbi_theme",
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_foreign_key(
        "fk_cbi_theme_initial_version",
        "cbi_theme",
        "cbi_theme_version",
        ["id", "initial_version"],
        ["theme_id", "version"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_check_constraint(
        "ck_cbi_theme_version_present",
        "cbi_theme",
        "active_version IS NOT NULL OR initial_version IS NOT NULL",
    )


def downgrade():
    if op.get_bind().scalar(
        sa.text("SELECT 1 FROM cbi_theme WHERE initial_version IS NOT NULL LIMIT 1")
    ):
        raise RuntimeError("Registered themes require the current schema")
    op.drop_constraint("ck_cbi_theme_version_present", "cbi_theme", type_="check")
    op.drop_constraint("fk_cbi_theme_initial_version", "cbi_theme", type_="foreignkey")
    op.drop_column("cbi_theme", "enabled")
    op.drop_column("cbi_theme", "registration_digest")
    op.drop_column("cbi_theme", "registration_request_id")
    op.drop_column("cbi_theme", "initial_version")
    op.alter_column(
        "cbi_theme", "active_version", existing_type=sa.String(128), nullable=False
    )
