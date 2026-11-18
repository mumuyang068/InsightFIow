"""Local method binding publication and run-linked feedback."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "cb104b1ad437"
down_revision = "c0f0e55d2a02"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cbi_binding_head",
        sa.Column("workspace", sa.BigInteger(), primary_key=True),
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("active", sa.Integer(), nullable=False),
        sa.Column("last_version", sa.Integer(), nullable=False),
    )
    op.create_table(
        "cbi_binding_version",
        sa.Column("workspace", sa.BigInteger(), primary_key=True),
        sa.Column("id", sa.String(48), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("state", JSONB(), nullable=False),
    )
    op.create_table(
        "cbi_method_feedback",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("workspace", sa.BigInteger(), nullable=False, index=True),
        sa.Column("chat_id", sa.BigInteger(), sa.ForeignKey("chat.id", ondelete="CASCADE"), nullable=False),
        sa.Column("state", JSONB(), nullable=False),
    )


def downgrade():
    op.drop_table("cbi_method_feedback")
    op.drop_table("cbi_binding_version")
    op.drop_table("cbi_binding_head")
