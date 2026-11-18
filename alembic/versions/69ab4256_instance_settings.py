"""Instance settings: administrator choices stored by ContextHub (ADR-002)

Revision ID: 69ab4256
Revises: 69ab4255
Create Date: 2026-11-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "69ab4256"
down_revision: Union[str, None] = "69ab4255"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "hub_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_by", UUID(as_uuid=True), sa.ForeignKey("hub_users.id", ondelete="SET NULL")),
    )


def downgrade() -> None:
    op.drop_table("hub_settings")
