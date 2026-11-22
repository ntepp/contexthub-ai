from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.api.deps import accessible_collections, actor_of, current_user, load_collection
from src.api.routes.connectors import serialize_sources
from src.audit import service as audit
from src.audit.service import Actor
from src.auth.rate_limit import limiter
from src.config import get_settings
from src.connectors import session_sources
from src.db.models import ACCESS_VIEWER, Collection, Conversation, Message, User, utcnow
from src.db.session import SessionLocal, get_db
from src.llm import gateway
from src.llm.factory import (
    AVAILABILITY_AVAILABLE,
    AVAILABILITY_UNAVAILABLE,
    AVAILABILITY_UNKNOWN,
    LLMProvider,
    ModelNotAllowed,
    ModelSpec,
    model_catalogue,
    model_offer,
    resolve_model,
)
from src.rag.rag_pipeline import RAGResult, answer_question, stream_answer
from src.utils.logging_config import get_logger

log = get_logger()
router = APIRouter(tags=["chat"])

_AUDIT_TEXT_LIMIT = 20_000
_DEFAULT_TITLE = "Nouvelle conversation"
_ENGINE_DOWN = "Le moteur de réponse est indisponible. Réessayez dans un instant."
ERROR_CODE_HEADER = "X-ContextHub-Error-Code"


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    collection_ids: list[uuid.UUID] | None = None
    collection: str | None = Field(default=None, description="Nom de collection (compatibilité v0)")
    conversation_id: uuid.UUID | None = None
    model: str | None = None
    # Search only the pages attached to the conversation (Confluence, Notion…).
    attached_only: bool = False


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


def _prepare(body: AskRequest, request: Request, user: User, db: Session, catalogue: list[ModelSpec]) -> PreparedAsk:
    settings = get_settings()
    if not limiter.hit(f"rl:ask:{user.id}", settings.ask_rate_limit_per_minute, 60):
        raise HTTPException(status_code=429, detail="Trop de questions en une minute, patientez un instant.")
    try:
        spec = resolve_model(body.model, catalogue)
    except (ModelNotAllowed, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    conversation: Conversation | None = None
    attached_id: uuid.UUID | None = None
    if body.conversation_id:
        conversation = db.get(Conversation, body.conversation_id)
        if conversation is None or conversation.user_id != user.id:
            raise HTTPException(status_code=404, detail="Conversation introuvable.")
        attached = session_sources.session_collection(db, conversation.id)
        attached_id = attached.id if attached is not None else None

    if body.attached_only:
        if attached_id is None:
            raise HTTPException(status_code=400, detail="Aucune page n'est jointe à cette conversation.")
        collection_ids = []
    elif body.collection_ids:
        collection_ids = [load_collection(db, user, cid, ACCESS_VIEWER).id for cid in body.collection_ids]
    elif body.collection:
        col = db.execute(select(Collection).where(Collection.name == body.collection)).scalar_one_or_none()
        if col is None:
            raise HTTPException(status_code=404, detail="Collection introuvable.")
        collection_ids = [load_collection(db, user, col.id, ACCESS_VIEWER).id]
    else:
        collection_ids = [c.id for c, _ in accessible_collections(db, user)]
    if not collection_ids and attached_id is None:
        raise HTTPException(status_code=400, detail="Aucune collection accessible : demandez un accès à l'administrateur.")

    question = body.question.strip()
    history: list[tuple[str, str]] = []
    if conversation is not None:
        history = [(m.role, m.content) for m in conversation.messages][-2 * settings.history_turns :]
        if not conversation.messages and conversation.title == _DEFAULT_TITLE:
            # Conversation opened by attaching pages: name it after its first question.
            conversation.title = question[:80] + ("…" if len(question) > 80 else "")
    else:
        conversation = Conversation(
            user_id=user.id,
            title=question[:80] + ("…" if len(question) > 80 else ""),
            collection_ids=[str(c) for c in collection_ids],
        )
        db.add(conversation)
    # The stored scope lists the regular collections only; attached pages are implied.
    conversation.collection_ids = [str(c) for c in collection_ids]
    conversation.updated_at = utcnow()
    db.commit()
    searched = collection_ids + ([attached_id] if attached_id is not None else [])
    return PreparedAsk(question, searched, conversation.id, history, spec.id, actor_of(request, user), user.id)


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
            "search_queries": result.search_queries,
            "search_rewrites": result.search_rewrites,
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
                    "url": s.get("url"),
                    "cited": s["cited"],
                }
                for s in result.sources
            ],
        },
    )
    return message_id


def _failure(prepared: PreparedAsk, exc: Exception, where: str) -> tuple[int, str, str | None]:
    """Log and audit a failed answer; (HTTP status, French message, refusal kind)."""
    refusal = gateway.classify_error(exc)
    details: dict = {"question": prepared.question}
    if refusal is None:
        log.exception("[%s] échec du pipeline RAG", where)
        audit.record("query.ask", prepared.actor, outcome="failure", details=details)
        return 502, _ENGINE_DOWN, None
    # Expected refusal (budget, rate limit, gateway down): no stack trace, no secret.
    log.warning(
        "[%s] refus de la passerelle LLM : %s (utilisateur=%s, modèle=%s, %s)",
        where,
        refusal.kind,
        prepared.user_id,
        prepared.model_id,
        type(exc).__name__,
    )
    details["reason"] = refusal.kind
    audit.record("query.ask", prepared.actor, outcome="failure", details=details)
    return refusal.status_code, refusal.message, refusal.kind


async def _catalogue() -> list[ModelSpec]:
    # Blocking (offered-models setting in the database, gateway lookup): off the event loop.
    return await asyncio.to_thread(model_catalogue)


def _response(prepared: PreparedAsk, result: RAGResult, message_id: uuid.UUID) -> dict:
    return {
        **result.as_dict(),
        "conversation_id": str(prepared.conversation_id),
        "message_id": str(message_id),
    }


@router.post("/chat/ask")
async def ask(body: AskRequest, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    prepared = _prepare(body, request, user, db, await _catalogue())
    try:
        result = await answer_question(
            prepared.question, prepared.collection_ids, prepared.history, prepared.model_id, prepared.user_id
        )
    except Exception as exc:
        status, message, kind = _failure(prepared, exc, "ask")
        # Same code as the SSE error event: lets API clients tell a gateway 429 from ours.
        headers = {ERROR_CODE_HEADER: kind} if kind else None
        raise HTTPException(status_code=status, detail=message, headers=headers)
    message_id = _persist(prepared, result)
    return _response(prepared, result, message_id)


# v0 compatibility alias
router.add_api_route("/ask", ask, methods=["POST"], include_in_schema=False)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# SSE comment: ignored by every client, but bytes on the wire for the proxies in between.
SSE_HEARTBEAT = ": ping\n\n"


async def with_heartbeat(source: AsyncIterator[dict], interval: float) -> AsyncIterator[dict | None]:
    """The items of ``source``, and ``None`` each time ``interval`` seconds pass without one.

    ``source`` is consumed by a task of its own, never cancelled by a timeout: waiting for
    the next item is what times out, not the work producing it. Its errors are re-raised
    here; closing this generator (client gone) cancels the task."""
    queue: asyncio.Queue[tuple[bool, object]] = asyncio.Queue()
    end = object()

    async def pump() -> None:
        try:
            async for item in source:
                queue.put_nowait((True, item))
        except Exception as exc:  # CancelledError (client gone) is not caught
            queue.put_nowait((False, exc))
        else:
            queue.put_nowait((True, end))

    task = asyncio.create_task(pump())
    try:
        while True:
            try:
                ok, item = await asyncio.wait_for(queue.get(), interval)
            except TimeoutError:
                yield None
                continue
            if not ok:
                raise item  # type: ignore[misc]
            if item is end:
                return
            yield item  # type: ignore[misc]
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


@router.post("/chat/stream")
async def ask_stream(
    body: AskRequest, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)
):
    """Server-Sent Events, one JSON object per ``data:`` line, in this order:

    * ``meta`` (conversation id, model), at once;
    * ``status`` (``step``: condense, retrieve, rerank, plan, generate) while the answer is
      prepared — progress only, never part of the answer;
    * ``token`` (``content``): the answer, from the generate node only (or the refusal, at
      once, when retrieval found nothing);
    * ``final`` (the stored answer: sources, ids…) then ``done``; or ``error`` instead.

    An SSE comment ``: ping`` is sent whenever nothing else was sent for
    ``SSE_HEARTBEAT_SECONDS``, so that no proxy closes a stream that is still working."""
    prepared = _prepare(body, request, user, db, await _catalogue())

    async def events():
        yield _sse({"type": "meta", "conversation_id": str(prepared.conversation_id), "model": prepared.model_id})
        result: RAGResult | None = None
        try:
            async for event in with_heartbeat(
                stream_answer(
                    prepared.question, prepared.collection_ids, prepared.history, prepared.model_id, prepared.user_id
                ),
                get_settings().sse_heartbeat_seconds,
            ):
                if event is None:
                    yield SSE_HEARTBEAT
                elif event["type"] in ("token", "status"):
                    yield _sse(event)
                elif event["type"] == "result":
                    payload = {k: v for k, v in event.items() if k != "type"}
                    result = RAGResult(**payload)
        except Exception as exc:
            status, message, kind = _failure(prepared, exc, "stream")
            error = {"type": "error", "message": message}
            if kind is not None:
                error.update(code=kind, status=status)
            yield _sse(error)
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
        "sources": serialize_sources(db, c.id),
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
    session_sources.drop_for_conversations(db, [c.id])
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


def _state(ok: bool | None) -> str:
    return AVAILABILITY_UNKNOWN if ok is None else AVAILABILITY_AVAILABLE if ok else AVAILABILITY_UNAVAILABLE


@router.get("/models")
async def list_models(user: User = Depends(current_user)) -> dict:
    """Offered models, in the administrator's order. ``availability``: ``available``,
    ``unavailable`` (left the catalogue, not installed, no key…) or ``unknown`` (gateway or
    Ollama unreachable); ``available`` is its boolean form (true only when available)."""
    settings = get_settings()
    # Blocking lookups (database setting, gateway catalogue; both cached): off the event loop.
    offer = await asyncio.to_thread(model_offer)
    served = await asyncio.to_thread(gateway.served_models) if settings.gateway_enabled else None
    installed = None if settings.gateway_enabled else await _ollama_available()
    models = []
    for offered in offer.models:
        spec = offered.spec
        ok: bool | None
        if offered.available is False:
            ok = False
        elif settings.gateway_enabled:
            # Same answer as /admin/models: unknown while the gateway cannot be reached.
            ok = spec.provider == LLMProvider.GATEWAY and (None if served is None else spec.name in served)
        elif spec.provider == LLMProvider.OLLAMA:
            ok = None if installed is None else (spec.name in installed or f"{spec.name}:latest" in installed)
        elif spec.provider == LLMProvider.OPENAI:
            ok = bool(settings.openai_api_key)
        elif spec.provider == LLMProvider.ANTHROPIC:
            ok = bool(settings.anthropic_api_key)
        elif spec.provider == LLMProvider.OPENAI_COMPATIBLE:
            ok = bool(settings.openai_compatible_base_url)
        else:
            ok = False  # gateway:… entry while no gateway is configured
        models.append({**spec.as_dict(), "available": ok is True, "availability": _state(ok)})
    return {"models": models, "default": offer.default_id}
