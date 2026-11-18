"""LLM gateway (ADR-001): the narrow contract ContextHub depends on.

The gateway is OpenAI-compatible (reference: LiteLLM proxy). ContextHub sends every
chat call there, tagged with the end user's ContextHub UUID and the pipeline step,
lists the models it serves, and reads consumption from it (``src/usage``). Costs,
budgets and rate limits are the gateway's business: ContextHub stores none.

Nothing but a UUID and fixed tags ever goes into the headers: never an e-mail, never
document text.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from src.config import get_settings
from src.utils.logging_config import get_logger

log = get_logger()

# Request headers documented by LiteLLM: https://docs.litellm.ai/docs/proxy/request_headers
END_USER_HEADER = "x-litellm-end-user-id"
TAGS_HEADER = "x-litellm-tags"
APP_TAG = "app:contexthub"
STEPS = ("condense", "plan", "generate")

# Failed catalogue lookups are cached briefly so a down gateway is not hammered.
_FAILURE_TTL_SECONDS = 10

_transport_override: Any = None
_cache_lock = threading.Lock()
_models_cache: tuple[float, tuple[str, ...] | None] | None = None


def set_gateway_transport(transport: Any) -> None:
    """For tests: an ``httpx.MockTransport`` answering for the gateway (sync and async)."""
    global _transport_override
    _transport_override = transport
    reset_models_cache()


def reset_models_cache() -> None:
    global _models_cache
    with _cache_lock:
        _models_cache = None


def _client_kwargs(key: str | None, timeout: float | None) -> dict:
    settings = get_settings()
    headers = {"User-Agent": f"ContextHub/{settings.app_version}", "Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return {
        "base_url": settings.gateway_root,
        "headers": headers,
        "timeout": timeout if timeout is not None else settings.llm_gateway_timeout_seconds,
        # A misconfigured gateway must not make us send its key to another host.
        "follow_redirects": False,
    }


def sync_client(key: str | None, timeout: float | None = None) -> httpx.Client:
    return httpx.Client(transport=_transport_override, **_client_kwargs(key, timeout))


def async_client(key: str | None, timeout: float | None = None, transport: Any = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=transport or _transport_override, **_client_kwargs(key, timeout))


def call_headers(user_id: uuid.UUID | str | None, step: str | None) -> dict[str, str]:
    """Attribution headers of one chat call: end user (ContextHub UUID) and step tag."""
    if step is not None and step not in STEPS:
        raise ValueError(f"Étape inconnue : {step!r}")
    headers: dict[str, str] = {}
    if user_id is not None:
        # Parsing guarantees only a UUID leaves ContextHub, never an e-mail or free text.
        headers[END_USER_HEADER] = str(uuid.UUID(str(user_id)))
    headers[TAGS_HEADER] = ",".join([APP_TAG] + ([f"step:{step}"] if step else []))
    return headers


# ---------------------------------------------------------------------------
# Served models
# ---------------------------------------------------------------------------


def _fetch_models() -> tuple[tuple[str, ...] | None, str | None]:
    """(served model names, None) or (None, short error) — blocking, never from the event loop."""
    settings = get_settings()
    try:
        with sync_client(settings.llm_gateway_api_key, timeout=min(settings.llm_gateway_timeout_seconds, 5)) as client:
            resp = client.get("/v1/models")
        if resp.status_code != 200:
            log.warning("[gateway] GET /v1/models a répondu %d", resp.status_code)
            return None, f"HTTP {resp.status_code}"
        data = resp.json().get("data")
        names: list[str] = []
        for item in data if isinstance(data, list) else []:
            name = item.get("id") if isinstance(item, dict) else None
            if isinstance(name, str) and name.strip() and name.strip() not in names:
                names.append(name.strip())
        return tuple(names), None
    except (httpx.HTTPError, ValueError, AttributeError) as exc:
        log.warning("[gateway] catalogue des modèles indisponible (%s)", type(exc).__name__)
        return None, type(exc).__name__


def probe() -> dict:
    """System check (``/admin/system``): fresh lookup, same shape as the other checks."""
    names, error = _fetch_models()
    if names is None:
        return {"ok": False, "error": error}
    return {"ok": True, "models": sorted(names)}


def served_models() -> tuple[str, ...] | None:
    """Names of the models the gateway serves (``GET /v1/models``), in its order.

    Cached for ``LLM_GATEWAY_MODELS_TTL_SECONDS``; ``None`` when the gateway cannot be
    reached or answers garbage.
    """
    global _models_cache
    now = time.monotonic()
    with _cache_lock:
        if _models_cache is not None and _models_cache[0] > now:
            return _models_cache[1]
    names, _ = _fetch_models()
    ttl = get_settings().llm_gateway_models_ttl_seconds
    if names is None:
        ttl = min(ttl, _FAILURE_TTL_SECONDS)
    with _cache_lock:
        _models_cache = (now + ttl, names)
    return names


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GatewayRefusal:
    kind: str  # budget_exceeded | rate_limited | unreachable | unavailable | rejected
    status_code: int
    message: str


_REFUSALS = {
    "budget_exceeded": (
        402,
        "Votre budget d'utilisation de l'assistant est atteint pour la période en cours. "
        "Contactez votre administrateur.",
    ),
    "rate_limited": (429, "Trop de requêtes vers le service de modèles : réessayez dans un instant."),
    "unreachable": (503, "Le service de modèles (passerelle LLM) est injoignable. Réessayez dans un instant."),
    "unavailable": (503, "Le service de modèles (passerelle LLM) est indisponible. Réessayez dans un instant."),
    "rejected": (502, "La passerelle LLM a refusé la requête. Contactez votre administrateur."),
}


def _refusal(kind: str) -> GatewayRefusal:
    status, message = _REFUSALS[kind]
    return GatewayRefusal(kind, status, message)


def _causes(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(seen) < 10:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _is_budget(err: Any) -> bool:
    text = f"{getattr(err, 'message', '')} {getattr(err, 'body', '')}".lower()
    # LiteLLM: "ExceededBudget: End User=... over budget", "Budget has been exceeded", type "budget_exceeded".
    return "budget" in text and ("exceed" in text or "over budget" in text)


def classify_error(exc: BaseException) -> GatewayRefusal | None:
    """Translate a failed chat call through the gateway; ``None`` when it is not one
    (or when no gateway is configured: the historical behaviour is kept)."""
    if not get_settings().gateway_enabled:
        return None
    try:
        import openai
    except ImportError:  # pragma: no cover - langchain_openai depends on it
        return None
    for err in _causes(exc):
        if isinstance(err, openai.APIConnectionError):  # includes APITimeoutError
            return _refusal("unreachable")
        if isinstance(err, openai.APIStatusError):
            if _is_budget(err):
                return _refusal("budget_exceeded")
            if err.status_code == 429:
                return _refusal("rate_limited")
            return _refusal("unavailable" if err.status_code >= 500 else "rejected")
        if isinstance(err, openai.APIError):  # error event inside a stream
            return _refusal("budget_exceeded" if _is_budget(err) else "unavailable")
    return None
