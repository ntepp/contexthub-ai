"""Pages attached to one conversation through a connector.

Each conversation that has attached pages owns a hidden "session collection"
(``Collection.conversation_id``). The pages go through the normal ingestion pipeline,
so answers keep citations, the distance threshold and the audit trail; the collection
is searched only by its conversation and is deleted with it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.config import get_settings
from src.connectors.base import ConnectorError, RemoteContent
from src.db.models import DOC_QUEUED, Collection, Connection, Conversation, Document, User
from src.ingestion import storage
from src.retrieval.store import get_store

_EXTENSIONS = {"text/html": ".html", "text/markdown": ".md"}


def session_collection(db: Session, conversation_id: uuid.UUID) -> Collection | None:
    return db.execute(select(Collection).where(Collection.conversation_id == conversation_id)).scalar_one_or_none()


def _ensure_session_collection(db: Session, conversation: Conversation) -> Collection:
    collection = session_collection(db, conversation.id)
    if collection is None:
        collection = Collection(
            name=f"session:{conversation.id}",
            description="Pages jointes à une conversation",
            is_open=False,
            created_by=conversation.user_id,
            conversation_id=conversation.id,
        )
        db.add(collection)
        db.flush()
    return collection


def list_sources(db: Session, conversation_id: uuid.UUID) -> list[Document]:
    collection = session_collection(db, conversation_id)
    if collection is None:
        return []
    return list(
        db.execute(
            select(Document).where(Document.collection_id == collection.id).order_by(Document.created_at)
        ).scalars()
    )


def _remove_document(db: Session, doc: Document) -> None:
    get_store().delete_document(doc.id)
    storage.delete_stored(doc.storage_path)
    db.delete(doc)


def attach_page(
    db: Session, user: User, conversation: Conversation, connection: Connection, content: RemoteContent
) -> Document:
    """Store one fetched page in the conversation's session collection (not yet indexed).

    Attaching a page that is already attached refreshes it.
    """
    settings = get_settings()
    collection = _ensure_session_collection(db, conversation)
    page = content.page

    previous = db.execute(
        select(Document).where(
            Document.collection_id == collection.id,
            Document.connection_id == connection.id,
            Document.external_id == page.external_id,
        )
    ).scalar_one_or_none()
    if previous is not None:
        _remove_document(db, previous)
        db.flush()

    count = db.execute(select(func.count()).where(Document.collection_id == collection.id)).scalar_one()
    if count >= settings.connector_max_pages_per_conversation:
        raise ConnectorError(
            f"Une conversation ne peut pas dépasser {settings.connector_max_pages_per_conversation} pages jointes."
        )

    staged = storage.stage_bytes(content.content)
    try:
        doc = Document(
            collection_id=collection.id,
            filename=page.title[:500],
            mime_type=content.mime_type,
            size_bytes=staged.size,
            sha256=staged.sha256,
            source_url=page.url[:2000],
            connection_id=connection.id,
            external_id=page.external_id[:255],
            status=DOC_QUEUED,
            warnings=content.warnings or None,
            uploaded_by=user.id,
        )
        db.add(doc)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise ConnectorError(f"Une page au contenu identique à « {page.title} » est déjà jointe.") from exc
        destination = storage.commit_staged(
            staged, storage.final_path(collection.id, doc.id, _EXTENSIONS.get(content.mime_type, ".txt"))
        )
        doc.storage_path = storage.relative_to_root(destination)
        db.commit()
    finally:
        staged.path.unlink(missing_ok=True)
    return doc


def detach(db: Session, conversation_id: uuid.UUID, document_id: uuid.UUID) -> dict | None:
    """Remove one attached page; returns what was removed (for the audit trail)."""
    collection = session_collection(db, conversation_id)
    if collection is None:
        return None
    doc = db.get(Document, document_id)
    if doc is None or doc.collection_id != collection.id:
        return None
    removed = {"title": doc.filename, "url": doc.source_url}
    _remove_document(db, doc)
    db.commit()
    return removed


def drop_for_conversations(db: Session, conversation_ids: Iterable[uuid.UUID]) -> int:
    """Delete the attached pages (chunks, files, rows) of conversations about to be deleted.

    PostgreSQL would cascade the rows, but neither the files on disk nor a non-SQL chunk
    store would follow: do it explicitly, before the conversations go.
    """
    ids = list(conversation_ids)
    if not ids:
        return 0
    collections = list(db.execute(select(Collection).where(Collection.conversation_id.in_(ids))).scalars())
    store = get_store()
    for collection in collections:
        store.delete_collection(collection.id)
        storage.delete_collection_dir(collection.id)
        db.delete(collection)
    db.flush()
    return len(collections)
