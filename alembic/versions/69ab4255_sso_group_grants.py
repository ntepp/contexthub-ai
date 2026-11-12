"""SSO group grants: collections opened to identity-provider groups

Revision ID: 69ab4255
Revises: 69ab4254
Create Date: 2026-11-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "69ab4255"
down_revision: Union[str, None] = "69ab4254"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("hub_users", sa.Column("oidc_groups", JSONB()))
    op.create_table(
        "hub_collection_group_grants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "collection_id", UUID(as_uuid=True), sa.ForeignKey("hub_collections.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("group_name", sa.String(255), nullable=False),
        sa.Column("level", sa.String(20), nullable=False, server_default="viewer"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("collection_id", "group_name", name="uq_group_grant_collection_group"),
    )
    op.create_index("ix_hub_collection_group_grants_collection_id", "hub_collection_group_grants", ["collection_id"])
    op.create_index("ix_hub_collection_group_grants_group_name", "hub_collection_group_grants", ["group_name"])


def downgrade() -> None:
    op.drop_table("hub_collection_group_grants")
    op.drop_column("hub_users", "oidc_groups")
