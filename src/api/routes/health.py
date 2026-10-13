from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text

from src.config import get_settings
from src.db.session import get_engine
from src.observability import metrics

router = APIRouter(tags=["ops"])


@router.get("/health")
def health() -> dict:
    """Liveness: the process is up."""
    return {"status": "ok", "version": get_settings().app_version}


@router.get("/ready")
def ready():
    """Readiness: the database answers. Used by the container orchestrator."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        return JSONResponse({"status": "unavailable", "database": type(exc).__name__}, status_code=503)
    return {"status": "ready"}


@router.get("/metrics", include_in_schema=False)
def prometheus_metrics() -> Response:
    # Not proxied by nginx: reachable only from inside the deployment network.
    body, content_type = metrics.render()
    return Response(content=body, media_type=content_type)
