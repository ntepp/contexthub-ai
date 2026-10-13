"""FastAPI application.

All business routes require authentication (see ``src/api/deps.py``). Unhandled
errors return a generic message plus a correlation id; details stay in the logs.
"""

from __future__ import annotations

import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api.routes import admin, auth, chat, collections, health
from src.auth.users import ensure_bootstrap_admin
from src.config import get_settings
from src.db.session import SessionLocal
from src.observability.metrics import HTTP_LATENCY, HTTP_REQUESTS
from src.utils.logging_config import get_logger, request_id_var

log = get_logger()

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "SAMEORIGIN",
}


def _configure_tracing() -> None:
    # LangSmith ships prompts — i.e. document excerpts — to a third-party SaaS. It
    # stays off unless explicitly enabled, and LangChain must not pick it up from a
    # stray environment variable either.
    if not get_settings().langchain_tracing_v2:
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        os.environ["LANGSMITH_TRACING"] = "false"
    else:
        log.warning("Traçage LangSmith ACTIVÉ : les prompts et extraits de documents sont envoyés à LangSmith.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_tracing()
    settings = get_settings()
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()
    try:
        ensure_bootstrap_admin(db)
    except Exception:
        log.exception("Initialisation de l'administrateur impossible (migrations appliquées ?)")
    finally:
        db.close()
    log.info("%s %s démarré (%s)", settings.app_name, settings.app_version, settings.environment)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=f"{settings.app_name} — API",
        description="Assistant documentaire souverain pour la conformité. Authentification requise.",
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        HTTP_REQUESTS.labels(request.method, route_path, str(response.status_code)).inc()
        HTTP_LATENCY.labels(request.method, route_path).observe(time.perf_counter() - started)
        response.headers["X-Request-ID"] = request_id
        for header, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errors = [
            {"field": ".".join(str(p) for p in e.get("loc", [])[1:]), "message": e.get("msg")} for e in exc.errors()
        ]
        return JSONResponse({"detail": "Requête invalide.", "errors": errors}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception):
        request_id = request_id_var.get() or "-"
        log.exception("Erreur non gérée sur %s %s", request.method, request.url.path)
        return JSONResponse(
            {"detail": "Erreur interne. Communiquez cet identifiant au support.", "request_id": request_id},
            status_code=500,
        )

    for module in (health, auth, collections, chat, admin):
        app.include_router(module.router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
