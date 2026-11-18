"""Store task-scoped file and analysis versions."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "c0f0e55d2a02"
down_revision = "525d0f8d4adb"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cbi_multi_task",
        sa.Column("chat_id", sa.BigInteger(), sa.ForeignKey("chat.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("state", JSONB(), nullable=False),
    )


def downgrade():
    op.drop_table("cbi_multi_task")
