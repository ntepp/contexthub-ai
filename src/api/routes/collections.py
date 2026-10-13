from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.api.deps import accessible_collections, actor_of, current_user, load_collection, require_admin
from src.audit import service as audit
from src.config import get_settings
from src.db.models import (
    ACCESS_EDITOR,
    ACCESS_VIEWER,
    DOC_FAILED,
    DOC_QUEUED,
    Collection,
    Document,
    User,
)
from src.db.session import get_db
from src.ingestion import storage
from src.ingestion.filetypes import IngestionError, extension_of, mime_for, sniff
from src.ingestion.url_fetch import validate_url
from src.observability.metrics import UPLOADS
from src.retrieval.store import get_store
from src.utils.logging_config import get_logger

log = get_logger()
router = APIRouter(tags=["knowledge-base"])


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    is_open: bool = False


class CollectionUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=2000)
    is_open: bool | None = None


class UrlImport(BaseModel):
    url: str = Field(max_length=2000)


def serialize_collection(collection: Collection, access: str, document_count: int = 0) -> dict:
    return {
        "id": str(collection.id),
        "name": collection.name,
        "description": collection.description,
        "is_open": collection.is_open,
        "access": access,
        "document_count": document_count,
        "created_at": collection.created_at.isoformat() if collection.created_at else None,
    }


def serialize_document(doc: Document) -> dict:
    return {
        "id": str(doc.id),
        "collection_id": str(doc.collection_id),
        "filename": doc.filename,
        "mime_type": doc.mime_type,
        "size_bytes": doc.size_bytes,
        "source_url": doc.source_url,
        "status": doc.status,
        "error_message": doc.error_message,
        "warnings": doc.warnings or [],
        "chunk_count": doc.chunk_count,
        "page_count": doc.page_count,
        "progress": {"current": doc.progress_current, "total": doc.progress_total},
        "has_file": storage.resolve_stored(doc.storage_path) is not None,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "indexed_at": doc.indexed_at.isoformat() if doc.indexed_at else None,
    }


def _dispatch(db: Session, doc: Document) -> None:
    from src.workers.tasks import ingest_document

    try:
        task = ingest_document.delay(str(doc.id))
    except Exception:
        log.exception("[upload] file d'attente indisponible")
        doc.status = DOC_FAILED
        doc.error_message = "File d'attente d'indexation indisponible : réessayez plus tard."
        db.commit()
        raise HTTPException(status_code=503, detail="Service d'indexation indisponible.")
    doc.task_id = task.id
    db.commit()


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------


@router.get("/collections")
def list_collections(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    pairs = accessible_collections(db, user)
    counts = dict(
        db.execute(
            select(Document.collection_id, func.count())
            .where(Document.collection_id.in_([c.id for c, _ in pairs] or [uuid.uuid4()]))
            .group_by(Document.collection_id)
        ).all()
    )
    return [serialize_collection(c, level, counts.get(c.id, 0)) for c, level in pairs]


@router.post("/collections", status_code=201)
def create_collection(
    body: CollectionCreate, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> dict:
    name = body.name.strip()
    if db.execute(select(Collection).where(Collection.name == name)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"La collection « {name} » existe déjà.")
    collection = Collection(name=name, description=body.description, is_open=body.is_open, created_by=admin.id)
    db.add(collection)
    db.commit()
    audit.record(
        "collection.create",
        actor_of(request, admin),
        resource_type="collection",
        resource_id=collection.id,
        collection_id=collection.id,
        details={"name": name, "is_open": body.is_open},
    )
    return serialize_collection(collection, "admin")


@router.patch("/collections/{collection_id}")
def update_collection(
    collection_id: uuid.UUID,
    body: CollectionUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    collection = load_collection(db, admin, collection_id)
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(collection, key, value)
    db.commit()
    audit.record(
        "collection.update",
        actor_of(request, admin),
        resource_type="collection",
        resource_id=collection.id,
        collection_id=collection.id,
        details=changes,
    )
    return serialize_collection(collection, "admin")


@router.delete("/collections/{collection_id}")
def delete_collection(
    collection_id: uuid.UUID, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> dict:
    collection = load_collection(db, admin, collection_id)
    name = collection.name
    doc_count = db.execute(select(func.count()).where(Document.collection_id == collection.id)).scalar_one()
    chunks = get_store().delete_collection(collection.id)
    storage.delete_collection_dir(collection.id)
    db.delete(collection)
    db.commit()
    audit.record(
        "collection.delete",
        actor_of(request, admin),
        resource_type="collection",
        resource_id=collection_id,
        collection_id=collection_id,
        details={"name": name, "documents": doc_count, "chunks": chunks},
    )
    return {"ok": True, "documents_deleted": doc_count}


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


@router.get("/collections/{collection_id}/documents")
def list_documents(collection_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    collection = load_collection(db, user, collection_id, ACCESS_VIEWER)
    docs = db.execute(
        select(Document).where(Document.collection_id == collection.id).order_by(Document.created_at.desc())
    ).scalars()
    return [serialize_document(d) for d in docs]


@router.post("/collections/{collection_id}/documents", status_code=202)
def upload_document(
    collection_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    settings = get_settings()
    collection = load_collection(db, user, collection_id, ACCESS_EDITOR)
    filename = (file.filename or "document").replace("\\", "/").rsplit("/", 1)[-1][:255] or "document"
    mime = mime_for(filename)
    if mime is None:
        raise HTTPException(status_code=415, detail="Format non supporté (PDF, DOCX, TXT, MD, HTML).")

    try:
        staged = storage.stage_stream(file.file, settings.upload_max_bytes)
    except storage.FileTooLarge:
        raise HTTPException(status_code=413, detail=f"Fichier trop volumineux (maximum {settings.upload_max_mb} Mo).")
    try:
        if staged.size == 0:
            raise HTTPException(status_code=400, detail="Fichier vide.")
        try:
            sniff(staged.path, mime)
        except IngestionError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        duplicate = db.execute(
            select(Document).where(Document.collection_id == collection.id, Document.sha256 == staged.sha256)
        ).scalar_one_or_none()
        if duplicate is not None:
            raise HTTPException(
                status_code=409,
                detail={"message": f"Ce fichier est déjà présent : « {duplicate.filename} ».", "document_id": str(duplicate.id)},
            )

        doc = Document(
            collection_id=collection.id,
            filename=filename,
            mime_type=mime,
            size_bytes=staged.size,
            sha256=staged.sha256,
            status=DOC_QUEUED,
            uploaded_by=user.id,
        )
        db.add(doc)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail={"message": "Ce fichier est déjà présent."})
        destination = storage.commit_staged(staged, storage.final_path(collection.id, doc.id, extension_of(filename)))
        doc.storage_path = storage.relative_to_root(destination)
        db.commit()
    finally:
        staged.path.unlink(missing_ok=True)

    UPLOADS.labels(mime).inc()
    audit.record(
        "document.upload",
        actor_of(request, user),
        resource_type="document",
        resource_id=doc.id,
        collection_id=collection.id,
        details={"filename": filename, "size_bytes": doc.size_bytes, "sha256": doc.sha256},
    )
    _dispatch(db, doc)
    return serialize_document(doc)


@router.post("/collections/{collection_id}/documents/url", status_code=202)
def import_url(
    collection_id: uuid.UUID,
    body: UrlImport,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not get_settings().allow_url_ingestion:
        raise HTTPException(status_code=403, detail="L'import d'URL est désactivé par l'administrateur.")
    collection = load_collection(db, user, collection_id, ACCESS_EDITOR)
    try:
        url = validate_url(body.url)
    except IngestionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    doc = Document(collection_id=collection.id, filename=url[:500], source_url=url, status=DOC_QUEUED, uploaded_by=user.id)
    db.add(doc)
    db.commit()
    audit.record(
        "document.import_url",
        actor_of(request, user),
        resource_type="document",
        resource_id=doc.id,
        collection_id=collection.id,
        details={"url": url},
    )
    _dispatch(db, doc)
    return serialize_document(doc)


def _load_document(db: Session, user: User, document_id: uuid.UUID, minimum: str) -> Document:
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document introuvable.")
    load_collection(db, user, doc.collection_id, minimum)
    return doc


@router.get("/documents/{document_id}")
def get_document(document_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return serialize_document(_load_document(db, user, document_id, ACCESS_VIEWER))


@router.get("/documents/{document_id}/file")
def download_document(
    document_id: uuid.UUID, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    doc = _load_document(db, user, document_id, ACCESS_VIEWER)
    path = storage.resolve_stored(doc.storage_path)
    if path is None:
        raise HTTPException(status_code=404, detail="Fichier original indisponible.")
    audit.record(
        "document.view",
        actor_of(request, user),
        resource_type="document",
        resource_id=doc.id,
        collection_id=doc.collection_id,
    )
    return FileResponse(
        path,
        media_type=doc.mime_type or "application/octet-stream",
        filename=doc.filename,
        content_disposition_type="inline" if doc.mime_type == "application/pdf" else "attachment",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/documents/{document_id}")
def delete_document(
    document_id: uuid.UUID, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    doc = _load_document(db, user, document_id, ACCESS_EDITOR)
    chunks = get_store().delete_document(doc.id)
    storage.delete_stored(doc.storage_path)
    details = {"filename": doc.filename, "chunks": chunks}
    collection_id = doc.collection_id
    db.delete(doc)
    db.commit()
    audit.record(
        "document.delete",
        actor_of(request, user),
        resource_type="document",
        resource_id=document_id,
        collection_id=collection_id,
        details=details,
    )
    return {"ok": True}


@router.post("/documents/{document_id}/reindex", status_code=202)
def reindex_document(
    document_id: uuid.UUID, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    doc = _load_document(db, user, document_id, ACCESS_EDITOR)
    if doc.storage_path is None and doc.source_url is None:
        raise HTTPException(status_code=409, detail="Aucun fichier source : supprimez puis réimportez ce document.")
    doc.status = DOC_QUEUED
    doc.error_message = None
    db.commit()
    audit.record(
        "document.reindex",
        actor_of(request, user),
        resource_type="document",
        resource_id=doc.id,
        collection_id=doc.collection_id,
    )
    _dispatch(db, doc)
    return serialize_document(doc)
