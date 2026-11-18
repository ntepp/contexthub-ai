"""document connectors: connections, session collections, connector pages

Revision ID: 69ab4254
Revises: 69ab4253
Create Date: 2026-11-12

A "session collection" (``hub_collections.conversation_id`` set) holds the pages a
user attached to one conversation through a connector. It is deleted with the
conversation, and the cascade removes its documents and chunks.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "69ab4254"
down_revision: Union[str, None] = "69ab4253"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "hub_connections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("base_url", sa.String(500)),
        sa.Column("account", sa.String(320)),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("hub_users.id", ondelete="SET NULL")),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("name", name="uq_hub_connections_name"),
    )

    op.add_column(
        "hub_collections",
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("hub_conversations.id", ondelete="CASCADE", name="fk_hub_collections_conversation"),
        ),
    )
    op.create_unique_constraint("uq_hub_collections_conversation", "hub_collections", ["conversation_id"])

    op.add_column(
        "hub_documents",
        sa.Column(
            "connection_id",
            UUID(as_uuid=True),
            sa.ForeignKey("hub_connections.id", ondelete="SET NULL", name="fk_hub_documents_connection"),
        ),
    )
    op.add_column("hub_documents", sa.Column("external_id", sa.String(255)))


def downgrade() -> None:
    op.execute("DELETE FROM hub_collections WHERE conversation_id IS NOT NULL")
    op.drop_column("hub_documents", "external_id")
    op.drop_column("hub_documents", "connection_id")
    op.drop_constraint("uq_hub_collections_conversation", "hub_collections", type_="unique")
    op.drop_column("hub_collections", "conversation_id")
    op.drop_table("hub_connections")
