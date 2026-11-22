"""Product references found in each document (model and part numbers)

Revision ID: 69ab4258
Revises: 69ab4257
Create Date: 2026-11-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "69ab4258"
down_revision: Union[str, None] = "69ab4257"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Portable types: JSONB on PostgreSQL, JSON elsewhere (the migration test runs on SQLite).
_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("hub_documents") as batch:
        batch.add_column(sa.Column("product_refs", _JSON, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("hub_documents") as batch:
        batch.drop_column("product_refs")
