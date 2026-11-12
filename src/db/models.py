"""Relational schema.

Portable types only (``Uuid``, ``JSON``) so the test suite can run on SQLite.
Vector chunks live in ``hub_chunks``, which is created by Alembic only — it relies
on pgvector and a generated ``tsvector`` column and is accessed through raw SQL in
``src/retrieval/store.py``.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Identity & access
# ---------------------------------------------------------------------------

ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"

ACCESS_VIEWER = "viewer"
ACCESS_EDITOR = "editor"


class User(Base):
    __tablename__ = "hub_users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default=ROLE_MEMBER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    auth_provider: Mapped[str] = mapped_column(String(20), default="local")
    oidc_subject: Mapped[str | None] = mapped_column(String(255), unique=True)
    # Groups carried by the last SSO login (claim OIDC_GROUPS_CLAIM); they open the
    # collections granted to those groups. Empty for password accounts.
    oidc_groups: Mapped[list | None] = mapped_column(JSONType)
    # Bumped to revoke every outstanding session of the user.
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    grants: Mapped[list["CollectionGrant"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN


class ApiKey(Base):
    __tablename__ = "hub_api_keys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hub_users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship()


# ---------------------------------------------------------------------------
# Knowledge base
# ---------------------------------------------------------------------------


class Collection(Base):
    __tablename__ = "hub_collections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    # When true, every active member can query the collection (viewer access).
    is_open: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("hub_users.id", ondelete="SET NULL"))
    # Set on the hidden "session collection" that holds the pages a user attached to one
    # conversation through a connector. Such a collection is reachable only through its
    # conversation: it is never listed, granted or opened directly (see api/deps.py).
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hub_conversations.id", ondelete="CASCADE"), unique=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    documents: Mapped[list["Document"]] = relationship(
        back_populates="collection", cascade="all, delete-orphan", passive_deletes=True
    )
    grants: Mapped[list["CollectionGrant"]] = relationship(
        back_populates="collection", cascade="all, delete-orphan", passive_deletes=True
    )


class CollectionGrant(Base):
    __tablename__ = "hub_collection_grants"
    __table_args__ = (UniqueConstraint("collection_id", "user_id", name="uq_grant_collection_user"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hub_collections.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hub_users.id", ondelete="CASCADE"), index=True)
    level: Mapped[str] = mapped_column(String(20), default=ACCESS_VIEWER)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    collection: Mapped[Collection] = relationship(back_populates="grants")
    user: Mapped[User] = relationship(back_populates="grants")


class CollectionGroupGrant(Base):
    """Access given to every member of an SSO group, re-evaluated at each login."""

    __tablename__ = "hub_collection_group_grants"
    __table_args__ = (UniqueConstraint("collection_id", "group_name", name="uq_group_grant_collection_group"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hub_collections.id", ondelete="CASCADE"), index=True
    )
    group_name: Mapped[str] = mapped_column(String(255), index=True)
    level: Mapped[str] = mapped_column(String(20), default=ACCESS_VIEWER)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


DOC_QUEUED = "queued"
DOC_PROCESSING = "processing"
DOC_READY = "ready"
DOC_FAILED = "failed"


class Document(Base):
    __tablename__ = "hub_documents"
    __table_args__ = (UniqueConstraint("collection_id", "sha256", name="uq_document_collection_sha256"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hub_collections.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(500))
    # Recognised official reference, e.g. "MiFID II — Directive 2014/65/UE".
    title: Mapped[str | None] = mapped_column(String(500))
    mime_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64))
    storage_path: Mapped[str | None] = mapped_column(String(1000))
    source_url: Mapped[str | None] = mapped_column(String(2000))
    # Page fetched through a connector: which connection, and its id in the remote system.
    connection_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("hub_connections.id", ondelete="SET NULL"))
    external_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), default=DOC_QUEUED)
    error_message: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list | None] = mapped_column(JSONType)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    task_id: Mapped[str | None] = mapped_column(String(255))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("hub_users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    collection: Mapped[Collection] = relationship(back_populates="documents")


# ---------------------------------------------------------------------------
# Connectors
# ---------------------------------------------------------------------------

CONNECTOR_CONFLUENCE = "confluence"
CONNECTOR_NOTION = "notion"
CONNECTOR_KINDS = (CONNECTOR_CONFLUENCE, CONNECTOR_NOTION)


class Connection(Base):
    """A configured link to an external document system (admin-managed).

    The secret (API token) is stored encrypted with a key derived from ``SECRET_KEY``
    and is never returned by the API.
    """

    __tablename__ = "hub_connections"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(100), unique=True)
    base_url: Mapped[str | None] = mapped_column(String(500))
    # Account e-mail for Confluence Cloud (basic auth); empty = bearer token (Data Center PAT).
    account: Mapped[str | None] = mapped_column(String(320))
    secret_encrypted: Mapped[str] = mapped_column(Text)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("hub_users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


class Conversation(Base):
    __tablename__ = "hub_conversations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("hub_users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(255), default="Nouvelle conversation")
    collection_ids: Mapped[list] = mapped_column(JSONType, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.created_at",
    )


class Message(Base):
    __tablename__ = "hub_messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hub_conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list | None] = mapped_column(JSONType)
    confidence: Mapped[float | None] = mapped_column(Float)
    refused: Mapped[bool] = mapped_column(Boolean, default=False)
    model: Mapped[str | None] = mapped_column(String(255))
    latency_seconds: Mapped[float | None] = mapped_column(Float)
    feedback: Mapped[int | None] = mapped_column(Integer)
    feedback_comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class AuditEvent(Base):
    """Append-only, hash-chained audit trail.

    ``hash = sha256(prev_hash || canonical(record))`` where the record covers every
    column except the personal-data fields (``actor_email``, ``ip``, ``user_agent``),
    which can therefore be redacted for GDPR erasure without breaking the chain.
    """

    __tablename__ = "hub_audit_events"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    actor_email: Mapped[str | None] = mapped_column(String(320))
    action: Mapped[str] = mapped_column(String(64), index=True)
    outcome: Mapped[str] = mapped_column(String(20), default="success")
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(255))
    collection_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    details: Mapped[dict | None] = mapped_column(JSONType)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)
