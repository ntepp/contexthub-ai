"""Scanned documents: how each document was read and the OCR quality (ADR-003)

Revision ID: 69ab4257
Revises: 69ab4256
Create Date: 2026-11-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "69ab4257"
down_revision: Union[str, None] = "69ab4256"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Portable types: JSONB on PostgreSQL, JSON elsewhere (the migration test runs on SQLite).
_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("hub_documents") as batch:
        batch.add_column(sa.Column("read_mode", sa.String(16), nullable=True))
        batch.add_column(sa.Column("ocr_pages", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("ocr_confidence", sa.Float(), nullable=True))
        batch.add_column(sa.Column("ocr_low_pages", _JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("hub_documents") as batch:
        batch.drop_column("ocr_low_pages")
        batch.drop_column("ocr_confidence")
        batch.drop_column("ocr_pages")
        batch.drop_column("read_mode")
