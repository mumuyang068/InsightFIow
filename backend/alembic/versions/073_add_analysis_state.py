"""Persist the optional analysis state of enabled chat records."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "525d0f8d4adb"
down_revision = "f898322341a5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "chat_record", sa.Column("analysis_state", postgresql.JSONB(), nullable=True)
    )


def downgrade():
    op.drop_column("chat_record", "analysis_state")
