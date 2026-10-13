from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.deps import accessible_collections, actor_of, current_user, load_collection
from src.audit import service as audit
from src.audit.service import Actor
from src.auth.rate_limit import limiter
from src.config import get_settings
from src.db.models import ACCESS_VIEWER, Collection, Conversation, Message, User, utcnow
from src.db.session import SessionLocal, get_db
from src.llm.factory import LLMProvider, ModelNotAllowed, default_model, model_catalogue, resolve_model
from src.rag.rag_pipeline import RAGResult, answer_question, stream_answer
from src.utils.logging_config import get_logger

log = get_logger()
router = APIRouter(tags=["chat"])

_AUDIT_TEXT_LIMIT = 20_000


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    collection_ids: list[uuid.UUID] | None = None
    collection: str | None = Field(default=None, description="Nom de collection (compatibilité v0)")
    conversation_id: uuid.UUID | None = None
    model: str | None = None


class FeedbackRequest(BaseModel):
    value: int = Field(ge=-1, le=1)
    comment: str | None = Field(default=None, max_length=2000)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=255)


@dataclass
class PreparedAsk:
    question: str
    collection_ids: list[uuid.UUID]
    conversation_id: uuid.UUID
    history: list[tuple[str, str]]
    model_id: str
    actor: Actor
    user_id: uuid.UUID


def _prepare(body: AskRequest, request: Request, user: User, db: Session) -> PreparedAsk:
    settings = get_settings()
    if not limiter.hit(f"rl:ask:{user.id}", settings.ask_rate_limit_per_minute, 60):
        raise HTTPException(status_code=429, detail="Trop de questions en une minute, patientez un instant.")
    try:
        spec = resolve_model(body.model)
    except (ModelNotAllowed, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if body.collection_ids:
        collection_ids = [load_collection(db, user, cid, ACCESS_VIEWER).id for cid in body.collection_ids]
    elif body.collection:
        col = db.execute(select(Collection).where(Collection.name == body.collection)).scalar_one_or_none()
        if col is None:
            raise HTTPException(status_code=404, detail="Collection introuvable.")
        collection_ids = [load_collection(db, user, col.id, ACCESS_VIEWER).id]
    else:
        collection_ids = [c.id for c, _ in accessible_collections(db, user)]
    if not collection_ids:
        raise HTTPException(status_code=400, detail="Aucune collection accessible : demandez un accès à l'administrateur.")

    question = body.question.strip()
    history: list[tuple[str, str]] = []
    if body.conversation_id:
        conversation = db.get(Conversation, body.conversation_id)
        if conversation is None or conversation.user_id != user.id:
            raise HTTPException(status_code=404, detail="Conversation introuvable.")
        history = [(m.role, m.content) for m in conversation.messages][-2 * settings.history_turns :]
    else:
        conversation = Conversation(
            user_id=user.id,
            title=question[:80] + ("…" if len(question) > 80 else ""),
            collection_ids=[str(c) for c in collection_ids],
        )
        db.add(conversation)
    conversation.collection_ids = [str(c) for c in collection_ids]
    conversation.updated_at = utcnow()
    db.commit()
    return PreparedAsk(question, collection_ids, conversation.id, history, spec.id, actor_of(request, user), user.id)


def _persist(prepared: PreparedAsk, result: RAGResult) -> uuid.UUID:
    db = SessionLocal()
    try:
        db.add(Message(conversation_id=prepared.conversation_id, role="user", content=prepared.question))
        assistant = Message(
            conversation_id=prepared.conversation_id,
            role="assistant",
            content=result.answer,
            sources=result.sources,
            confidence=result.confidence_metric,
            refused=result.refused,
            model=result.model,
            latency_seconds=result.latency_seconds,
        )
        db.add(assistant)
        db.commit()
        message_id = assistant.id
    finally:
        db.close()
    audit.record(
        "query.ask",
        prepared.actor,
        resource_type="conversation",
        resource_id=prepared.conversation_id,
        collection_id=prepared.collection_ids[0] if len(prepared.collection_ids) == 1 else None,
        details={
            "question": prepared.question,
            "standalone_question": result.standalone_question,
            "answer": result.answer[:_AUDIT_TEXT_LIMIT],
            "refused": result.refused,
            "model": result.model,
            "confidence": result.confidence_metric,
            "latency_seconds": result.latency_seconds,
            "collection_ids": [str(c) for c in prepared.collection_ids],
            "message_id": str(message_id),
            "sources": [
                {
                    "index": s["index"],
                    "document_id": s["document_id"],
                    "filename": s["filename"],
                    "page": s["page"],
                    "section": s["section"],
                    "cited": s["cited"],
                }
                for s in result.sources
            ],
        },
    )
    return message_id


def _response(prepared: PreparedAsk, result: RAGResult, message_id: uuid.UUID) -> dict:
    return {
        **result.as_dict(),
        "conversation_id": str(prepared.conversation_id),
        "message_id": str(message_id),
    }


@router.post("/chat/ask")
async def ask(body: AskRequest, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    prepared = _prepare(body, request, user, db)
    try:
        result = await answer_question(prepared.question, prepared.collection_ids, prepared.history, prepared.model_id)
    except Exception:
        log.exception("[ask] échec du pipeline RAG")
        audit.record("query.ask", prepared.actor, outcome="failure", details={"question": prepared.question})
        raise HTTPException(status_code=502, detail="Le moteur de réponse est indisponible. Réessayez dans un instant.")
    message_id = _persist(prepared, result)
    return _response(prepared, result, message_id)


# v0 compatibility alias
router.add_api_route("/ask", ask, methods=["POST"], include_in_schema=False)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def ask_stream(
    body: AskRequest, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    prepared = _prepare(body, request, user, db)

    async def events():
        yield _sse({"type": "meta", "conversation_id": str(prepared.conversation_id), "model": prepared.model_id})
        result: RAGResult | None = None
        try:
            async for event in stream_answer(
                prepared.question, prepared.collection_ids, prepared.history, prepared.model_id
            ):
                if event["type"] == "token":
                    yield _sse(event)
                elif event["type"] == "result":
                    payload = {k: v for k, v in event.items() if k != "type"}
                    result = RAGResult(**payload)
        except Exception:
            log.exception("[stream] échec du pipeline RAG")
            audit.record("query.ask", prepared.actor, outcome="failure", details={"question": prepared.question})
            yield _sse({"type": "error", "message": "Le moteur de réponse est indisponible. Réessayez dans un instant."})
            return
        finally:
            if result is None:
                log.info("[stream] interrompu avant la fin — rien n'est enregistré")
        message_id = _persist(prepared, result)
        yield _sse({"type": "final", **_response(prepared, result, message_id)})
        yield _sse({"type": "done"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


router.add_api_route("/ask/stream", ask_stream, methods=["POST"], include_in_schema=False)


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


def _serialize_message(m: Message) -> dict:
    return {
        "id": str(m.id),
        "role": m.role,
        "content": m.content,
        "sources": m.sources or [],
        "confidence": m.confidence,
        "refused": m.refused,
        "model": m.model,
        "latency_seconds": m.latency_seconds,
        "feedback": m.feedback,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def _own_conversation(db: Session, user: User, conversation_id: uuid.UUID) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation introuvable.")
    return conversation


@router.get("/conversations")
def list_conversations(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(
        select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.updated_at.desc()).limit(200)
    ).scalars()
    return [
        {
            "id": str(c.id),
            "title": c.title,
            "collection_ids": c.collection_ids or [],
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }
        for c in rows
    ]


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = _own_conversation(db, user, conversation_id)
    return {
        "id": str(c.id),
        "title": c.title,
        "collection_ids": c.collection_ids or [],
        "messages": [_serialize_message(m) for m in c.messages],
    }


@router.patch("/conversations/{conversation_id}")
def rename_conversation(
    conversation_id: uuid.UUID, body: ConversationRename, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    c = _own_conversation(db, user, conversation_id)
    c.title = body.title.strip()
    db.commit()
    return {"ok": True}


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: uuid.UUID, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    c = _own_conversation(db, user, conversation_id)
    db.delete(c)
    db.commit()
    audit.record("conversation.delete", actor_of(request, user), resource_type="conversation", resource_id=conversation_id)
    return {"ok": True}


@router.post("/messages/{message_id}/feedback")
def message_feedback(
    message_id: uuid.UUID,
    body: FeedbackRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    message = db.get(Message, message_id)
    if message is None or message.role != "assistant":
        raise HTTPException(status_code=404, detail="Message introuvable.")
    _own_conversation(db, user, message.conversation_id)
    message.feedback = body.value or None
    message.feedback_comment = body.comment
    db.commit()
    audit.record(
        "query.feedback",
        actor_of(request, user),
        resource_type="message",
        resource_id=message_id,
        details={"value": body.value, "comment": body.comment},
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


async def _ollama_available() -> set[str] | None:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{get_settings().ollama_base_url}/api/tags")
            resp.raise_for_status()
            return {m["name"] for m in resp.json().get("models", [])}
    except Exception:
        return None


@router.get("/models")
async def list_models(user: User = Depends(current_user)) -> dict:
    settings = get_settings()
    installed = await _ollama_available()
    models = []
    for spec in model_catalogue():
        entry = spec.as_dict()
        if spec.provider == LLMProvider.OLLAMA:
            entry["available"] = installed is not None and (
                spec.name in installed or f"{spec.name}:latest" in installed
            )
        elif spec.provider == LLMProvider.OPENAI:
            entry["available"] = bool(settings.openai_api_key)
        elif spec.provider == LLMProvider.ANTHROPIC:
            entry["available"] = bool(settings.anthropic_api_key)
        else:
            entry["available"] = bool(settings.openai_compatible_base_url)
        models.append(entry)
    return {"models": models, "default": default_model().id}
