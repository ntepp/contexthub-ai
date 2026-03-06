"""create hub_documents table

Revision ID: 69ab4252
Revises: 69ab4251
Create Date: 2026-03-06

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "69ab4252"
down_revision: Union[str, None] = "69ab4251"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from sqlalchemy import inspect
    bind = op.get_bind()
    if "hub_documents" not in inspect(bind).get_table_names():
        op.create_table(
            "hub_documents",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("filename", sa.String(500), nullable=False),
            sa.Column("collection_name", sa.String(255), nullable=False, index=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="indexing"),
            sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("task_id", sa.String(255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table("hub_documents")
