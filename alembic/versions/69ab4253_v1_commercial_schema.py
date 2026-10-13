"""v1 commercial schema: users, grants, API keys, conversations, audit, hub_chunks

Revision ID: 69ab4253
Revises: 69ab4252
Create Date: 2026-10-13

Legacy documents (pre-v1) had their vectors in langchain-postgres tables and no
stored file. They are kept, marked ``failed`` with an explanatory message, and must
be re-uploaded: v1 uses structure-aware chunking and its own ``hub_chunks`` table.
"""

import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "69ab4253"
down_revision: Union[str, None] = "69ab4252"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TS = sa.DateTime(timezone=True)


def _now():
    return sa.text("now()")


def upgrade() -> None:
    embedding_dim = int(os.getenv("EMBEDDING_DIM", "1024"))
    fts_language = os.getenv("FTS_LANGUAGE", "french")
    if not fts_language.isidentifier():
        raise ValueError(f"Invalid FTS_LANGUAGE: {fts_language!r}")

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ------------------------------------------------------------------ users
    op.create_table(
        "hub_users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(255)),
        sa.Column("password_hash", sa.String(255)),
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auth_provider", sa.String(20), nullable=False, server_default="local"),
        sa.Column("oidc_subject", sa.String(255), unique=True),
        sa.Column("session_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", TS, nullable=False, server_default=_now()),
        sa.Column("last_login_at", TS),
    )
    op.create_index("ix_hub_users_email", "hub_users", ["email"], unique=True)

    op.create_table(
        "hub_api_keys",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("hub_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=_now()),
        sa.Column("last_used_at", TS),
        sa.Column("revoked_at", TS),
    )
    op.create_index("ix_hub_api_keys_user_id", "hub_api_keys", ["user_id"])
    op.create_index("ix_hub_api_keys_key_hash", "hub_api_keys", ["key_hash"], unique=True)

    # ------------------------------------------------------------------ collections
    op.add_column("hub_collections", sa.Column("is_open", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column(
        "hub_collections",
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("hub_users.id", ondelete="SET NULL")),
    )
    op.drop_column("hub_collections", "document_count")
    for col in ("created_at", "updated_at"):
        op.alter_column("hub_collections", col, type_=TS, postgresql_using=f"{col} AT TIME ZONE 'UTC'")

    op.create_table(
        "hub_collection_grants",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "collection_id",
            UUID(as_uuid=True),
            sa.ForeignKey("hub_collections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("hub_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("level", sa.String(20), nullable=False, server_default="viewer"),
        sa.Column("created_at", TS, nullable=False, server_default=_now()),
        sa.UniqueConstraint("collection_id", "user_id", name="uq_grant_collection_user"),
    )
    op.create_index("ix_hub_collection_grants_collection_id", "hub_collection_grants", ["collection_id"])
    op.create_index("ix_hub_collection_grants_user_id", "hub_collection_grants", ["user_id"])

    # ------------------------------------------------------------------ documents
    op.execute(
        """
        INSERT INTO hub_collections (id, name, is_open, created_at, updated_at)
        SELECT gen_random_uuid(), d.collection_name, false, now(), now()
        FROM (SELECT DISTINCT collection_name FROM hub_documents) d
        WHERE NOT EXISTS (SELECT 1 FROM hub_collections c WHERE c.name = d.collection_name)
        """
    )
    op.add_column("hub_documents", sa.Column("collection_id", UUID(as_uuid=True)))
    op.execute(
        "UPDATE hub_documents d SET collection_id = c.id FROM hub_collections c WHERE c.name = d.collection_name"
    )
    op.alter_column("hub_documents", "collection_id", nullable=False)
    op.create_foreign_key(
        "fk_hub_documents_collection",
        "hub_documents",
        "hub_collections",
        ["collection_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_hub_documents_collection_id", "hub_documents", ["collection_id"])
    op.drop_index("ix_hub_documents_collection_name", table_name="hub_documents", if_exists=True)
    op.drop_column("hub_documents", "collection_name")

    new_doc_columns = [
        sa.Column("mime_type", sa.String(100)),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64)),
        sa.Column("storage_path", sa.String(1000)),
        sa.Column("source_url", sa.String(2000)),
        sa.Column("error_message", sa.Text()),
        sa.Column("warnings", JSONB()),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_current", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("uploaded_by", UUID(as_uuid=True), sa.ForeignKey("hub_users.id", ondelete="SET NULL")),
        sa.Column("indexed_at", TS),
    ]
    for column in new_doc_columns:
        op.add_column("hub_documents", column)
    for col in ("created_at", "updated_at"):
        op.alter_column("hub_documents", col, type_=TS, postgresql_using=f"{col} AT TIME ZONE 'UTC'")
    op.alter_column("hub_documents", "status", server_default="queued")
    op.execute(
        """
        UPDATE hub_documents
        SET status = 'failed',
            error_message = 'Document importé avant la v1 : supprimez-le puis réimportez-le.'
        """
    )
    op.create_unique_constraint("uq_document_collection_sha256", "hub_documents", ["collection_id", "sha256"])

    # ------------------------------------------------------------------ chunks
    op.execute(
        f"""
        CREATE TABLE hub_chunks (
            id uuid PRIMARY KEY,
            document_id uuid NOT NULL REFERENCES hub_documents(id) ON DELETE CASCADE,
            collection_id uuid NOT NULL REFERENCES hub_collections(id) ON DELETE CASCADE,
            chunk_index integer NOT NULL,
            content text NOT NULL,
            metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            embedding vector({embedding_dim}) NOT NULL,
            tsv tsvector GENERATED ALWAYS AS (to_tsvector('{fts_language}', content)) STORED
        )
        """
    )
    op.execute("CREATE INDEX ix_hub_chunks_document_id ON hub_chunks (document_id)")
    op.execute("CREATE INDEX ix_hub_chunks_collection_id ON hub_chunks (collection_id)")
    op.execute("CREATE INDEX ix_hub_chunks_tsv ON hub_chunks USING gin (tsv)")
    op.execute("CREATE INDEX ix_hub_chunks_embedding ON hub_chunks USING hnsw (embedding vector_cosine_ops)")

    # ------------------------------------------------------------------ conversations
    op.create_table(
        "hub_conversations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("hub_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("collection_ids", JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", TS, nullable=False, server_default=_now()),
        sa.Column("updated_at", TS, nullable=False, server_default=_now()),
    )
    op.create_index("ix_hub_conversations_user_id", "hub_conversations", ["user_id"])

    op.create_table(
        "hub_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("hub_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sources", JSONB()),
        sa.Column("confidence", sa.Float()),
        sa.Column("refused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("model", sa.String(255)),
        sa.Column("latency_seconds", sa.Float()),
        sa.Column("feedback", sa.Integer()),
        sa.Column("feedback_comment", sa.Text()),
        sa.Column("created_at", TS, nullable=False, server_default=_now()),
    )
    op.create_index("ix_hub_messages_conversation_id", "hub_messages", ["conversation_id"])

    # ------------------------------------------------------------------ audit
    op.create_table(
        "hub_audit_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", TS, nullable=False, server_default=_now()),
        sa.Column("actor_id", UUID(as_uuid=True)),
        sa.Column("actor_email", sa.String(320)),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False, server_default="success"),
        sa.Column("resource_type", sa.String(64)),
        sa.Column("resource_id", sa.String(255)),
        sa.Column("collection_id", UUID(as_uuid=True)),
        sa.Column("details", JSONB()),
        sa.Column("ip", sa.String(64)),
        sa.Column("user_agent", sa.String(500)),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False, unique=True),
    )
    op.create_index("ix_hub_audit_events_ts", "hub_audit_events", ["ts"])
    op.create_index("ix_hub_audit_events_actor_id", "hub_audit_events", ["actor_id"])
    op.create_index("ix_hub_audit_events_action", "hub_audit_events", ["action"])
    op.create_index("ix_hub_audit_events_collection_id", "hub_audit_events", ["collection_id"])


def downgrade() -> None:
    op.drop_table("hub_audit_events")
    op.drop_table("hub_messages")
    op.drop_table("hub_conversations")
    op.execute("DROP TABLE IF EXISTS hub_chunks")
    op.drop_constraint("uq_document_collection_sha256", "hub_documents", type_="unique")
    for name in (
        "indexed_at",
        "uploaded_by",
        "progress_total",
        "progress_current",
        "page_count",
        "warnings",
        "error_message",
        "source_url",
        "storage_path",
        "sha256",
        "size_bytes",
        "mime_type",
    ):
        op.drop_column("hub_documents", name)
    op.add_column("hub_documents", sa.Column("collection_name", sa.String(255)))
    op.execute(
        "UPDATE hub_documents d SET collection_name = c.name FROM hub_collections c WHERE c.id = d.collection_id"
    )
    op.drop_constraint("fk_hub_documents_collection", "hub_documents", type_="foreignkey")
    op.drop_index("ix_hub_documents_collection_id", table_name="hub_documents")
    op.drop_column("hub_documents", "collection_id")
    op.drop_table("hub_collection_grants")
    op.drop_column("hub_collections", "created_by")
    op.drop_column("hub_collections", "is_open")
    op.add_column(
        "hub_collections", sa.Column("document_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.drop_table("hub_api_keys")
    op.drop_table("hub_users")
