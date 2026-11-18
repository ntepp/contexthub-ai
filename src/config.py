"""Centralised, validated configuration.

Every setting is read from the environment (or `.env`) exactly once, here. Modules
import `get_settings()` rather than calling `os.getenv` so that defaults, types and
production safety checks live in a single place.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger("contexthub")

_INSECURE_SECRET = "dev-insecure-secret-change-me"

# LLM gateway products ContextHub has adapters for (consumption, budgets).
SUPPORTED_GATEWAY_KINDS = ("litellm",)

DEFAULT_NO_ANSWER = "L'information n'est pas disponible dans la base de connaissances actuelle."


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # ------------------------------------------------------------------ runtime
    environment: Literal["development", "production", "test"] = "development"
    app_name: str = "ContextHub AI"
    app_version: str = "1.0.0"
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "text"
    # Public URL of the frontend, used for OIDC redirects and CORS.
    public_url: str = "http://localhost:5173"
    # Link to the source code shown on the login page; empty hides it.
    source_code_url: str = "https://github.com/ntepp/contexthub-ai"
    cors_origins: str = ""

    # ------------------------------------------------------------------ security
    secret_key: str = _INSECURE_SECRET
    session_ttl_minutes: int = 8 * 60
    cookie_secure: bool | None = None  # defaults to True in production
    login_max_attempts: int = 10
    login_window_seconds: int = 15 * 60
    # Same window: failed or not, sign-in attempts from one address on any account
    # (password spraying), and on one account from any address.
    login_ip_max_attempts: int = 50
    login_account_max_attempts: int = 30
    # When set, GET /metrics requires "Authorization: Bearer <token>" (it is never proxied by nginx).
    metrics_token: str | None = None
    ask_rate_limit_per_minute: int = 30
    password_min_length: int = 12

    # Bootstrap administrator, created on startup if no admin exists.
    admin_email: str | None = None
    admin_password: str | None = None

    # ------------------------------------------------------------------ OIDC SSO
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_label: str = "SSO"
    oidc_scopes: str = "openid email profile"
    oidc_email_claim: str = "email"
    oidc_groups_claim: str = "groups"
    oidc_admin_groups: str = ""
    oidc_allowed_domains: str = ""
    local_login_enabled: bool = True

    # ------------------------------------------------------------------ storage
    # 127.0.0.1, not localhost: on Windows localhost may resolve to ::1 first and stall.
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "contexthub"
    database_url: str | None = None

    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_password: str | None = None
    redis_url: str | None = None

    upload_dir: Path = Path("./uploads")
    upload_max_mb: int = 50

    # ------------------------------------------------------------------ ingestion
    allow_url_ingestion: bool = False
    url_fetch_timeout_seconds: int = 15
    url_max_mb: int = 10
    embed_batch_size: int = 32
    chunk_size: int = 1200
    chunk_overlap: int = 150
    # One chunk per definition in definition articles and glossaries (see chunking.py).
    chunk_definitions: bool = True
    ocr_enabled: bool = True
    ocr_languages: str = "fra+eng"

    # ------------------------------------------------------------------ connectors
    # Pages picked by a user in Confluence / Notion are attached to one conversation.
    connectors_enabled: bool = True
    connector_timeout_seconds: int = 20
    connector_max_page_mb: int = 5
    connector_max_pages_per_conversation: int = 20

    # ------------------------------------------------------------------ embeddings
    embedding_provider: Literal["ollama", "openai", "openai_compatible"] = "ollama"
    embedding_model: str = "bge-m3"
    embedding_dim: int = 1024

    # ------------------------------------------------------------------ LLMs
    llm_provider: str = "ollama"
    llm_model_name: str = "llama3.1:8b"
    # Comma-separated allow-list of "provider:model" the UI may select. The first
    # entry is the default. Empty = only LLM_PROVIDER:LLM_MODEL_NAME.
    llm_models: str = ""
    llm_temperature: float = 0.0
    llm_timeout_seconds: int = 120

    ollama_base_url: str = "http://127.0.0.1:11434"
    # "Thinking" models (qwen3, deepseek-r1…) are ~25x slower with reasoning on, for no
    # gain on short grounded answers. Off by default; ignored by non-thinking models.
    ollama_reasoning: bool = False
    # Ollama silently truncates prompts beyond num_ctx (default 4096): 6 sources of
    # ~1200 characters plus instructions need headroom.
    ollama_num_ctx: int = 8192
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    openai_compatible_base_url: str | None = None
    openai_compatible_api_key: str | None = None
    openai_compatible_label: str = "Compatible OpenAI"
    # Set to true when the OpenAI-compatible endpoint runs inside the client's network
    # (vLLM, LM Studio...) so the UI does not flag it as a cloud model.
    openai_compatible_is_local: bool = False

    # ------------------------------------------------------------------ LLM gateway (ADR-001)
    # When LLM_GATEWAY_URL is set, every chat call (condense, plan, generate) goes through
    # this OpenAI-compatible gateway, whatever the selected model; provider keys are not
    # needed. Costs, budgets and rate limits belong to the gateway: ContextHub stores none.
    llm_gateway_url: str | None = None  # proxy root, e.g. http://litellm:4000
    llm_gateway_kind: str = "litellm"  # one of SUPPORTED_GATEWAY_KINDS, checked at startup
    # Application key used for chat calls and for listing the served models.
    llm_gateway_api_key: str | None = None
    # Gateway administrator key, used only to read consumption (LiteLLM reserves the spend
    # endpoints to admin keys). Optional, no fallback on LLM_GATEWAY_API_KEY (least
    # privilege): without it the consumption pages say they are not configured.
    llm_gateway_admin_key: str | None = None
    llm_gateway_label: str = "Passerelle LLM"
    # True when the gateway and the models it serves run inside the client's network.
    llm_gateway_is_local: bool = False
    llm_gateway_timeout_seconds: int = 10  # admin / catalogue calls; chat uses LLM_TIMEOUT_SECONDS
    llm_gateway_models_ttl_seconds: int = 60
    # Address of the gateway's own console as the administrators' browser reaches it, linked
    # from Administration › Modèles. Empty: derived (LiteLLM: <LLM_GATEWAY_URL>/ui) unless the
    # gateway host is a bare service name (e.g. "litellm" in Docker), unreachable from a browser.
    llm_gateway_console_url: str | None = None

    # ------------------------------------------------------------------ retrieval
    retrieval_max_distance: float = 0.75
    retrieval_top_k: int = 8
    retrieval_candidates: int = 40
    retrieval_hybrid: bool = True
    fts_language: str = "fr_unaccent"
    rrf_k: int = 60
    reranker: Literal["none", "cross_encoder"] = "none"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    history_turns: int = 4
    # Agentic retrieval: the LLM splits multi-part questions into up to N sub-queries,
    # searched on top of the question (one extra LLM call per answered question;
    # refusals stay LLM-free).
    agentic_retrieval: bool = True
    agentic_max_queries: int = 3
    no_answer_text: str = DEFAULT_NO_ANSWER
    # Optional business context appended to the system prompt, e.g. "Tu assistes les équipes RH
    # d'ACME ; les documents sont la convention collective et les accords d'entreprise."
    assistant_context: str = ""

    # ------------------------------------------------------------------ retention
    audit_retention_days: int = 0  # 0 = keep forever
    conversation_retention_days: int = 0

    # ------------------------------------------------------------------ observability
    langchain_tracing_v2: bool = False

    @model_validator(mode="after")
    def _production_guards(self) -> Settings:
        self.llm_gateway_kind = (self.llm_gateway_kind or "").strip().lower()
        if self.llm_gateway_kind not in SUPPORTED_GATEWAY_KINDS:
            raise ValueError(
                f"LLM_GATEWAY_KIND={self.llm_gateway_kind!r} is not supported "
                f"(accepted values: {', '.join(SUPPORTED_GATEWAY_KINDS)})"
            )
        for name in ("llm_gateway_url", "llm_gateway_console_url"):
            value = getattr(self, name)
            if not value:
                continue
            parsed = urlparse(value.strip())
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError(f"{name.upper()} must be an http(s) URL")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError(f"{name.upper()} must not contain credentials or parameters")
        if self.environment == "production":
            problems = []
            if self.secret_key == _INSECURE_SECRET or len(self.secret_key) < 32:
                problems.append("SECRET_KEY must be set to a random value of at least 32 characters")
            if self.postgres_password in ("postgres", "change-me") and not self.database_url:
                problems.append("POSTGRES_PASSWORD must not be a default value")
            if self.redis_password == "change-me":
                problems.append("REDIS_PASSWORD must not be a default value")
            if self.admin_password == "change-me":
                problems.append("ADMIN_PASSWORD must not be a default value")
            if self.llm_gateway_url and not self.llm_gateway_api_key:
                problems.append("LLM_GATEWAY_API_KEY must be set when LLM_GATEWAY_URL is set")
            if problems:
                raise ValueError("Unsafe production configuration: " + "; ".join(problems))
        return self

    # ------------------------------------------------------------------ derived
    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    def redis_db_url(self, db: int) -> str:
        if self.redis_url:
            return f"{self.redis_url.rstrip('/')}/{db}"
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{db}"

    @property
    def secure_cookies(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.environment == "production"

    @property
    def gateway_enabled(self) -> bool:
        return bool(self.llm_gateway_url and self.llm_gateway_url.strip())

    @property
    def gateway_root(self) -> str:
        """Gateway root URL, without trailing slash nor ``/v1`` (accepted for convenience)."""
        root = (self.llm_gateway_url or "").strip().rstrip("/")
        return root[:-3] if root.endswith("/v1") else root

    @property
    def gateway_console_url(self) -> str | None:
        """Link to the gateway console for administrators, or None when not safely known."""
        if not self.gateway_enabled:
            return None
        if self.llm_gateway_console_url and self.llm_gateway_console_url.strip():
            return self.llm_gateway_console_url.strip()
        host = (urlparse(self.gateway_root).hostname or "").lower()
        if self.llm_gateway_kind != "litellm" or not _browser_reachable(host):
            return None
        return f"{self.gateway_root}/ui"

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id)

    @property
    def upload_max_bytes(self) -> int:
        return self.upload_max_mb * 1024 * 1024

    @property
    def allowed_origins(self) -> list[str]:
        origins = [o.strip() for o in self.cors_origins.split(",") if o.strip()]
        return origins or [self.public_url.rstrip("/")]

    @staticmethod
    def split_csv(value: str) -> list[str]:
        return [v.strip() for v in value.split(",") if v.strip()]


# Hosts that only resolve inside a container network or a cluster, or are no address at all.
_UNREACHABLE_HOSTS = ("0.0.0.0", "::", "host.docker.internal", "gateway.docker.internal")
_UNREACHABLE_SUFFIXES = (".svc", ".svc.cluster.local", ".cluster.local")


def _browser_reachable(host: str) -> bool:
    """Whether a browser on the administrator's machine can plausibly open ``host``: not a
    bare service name (``litellm`` in Docker Compose), not a cluster-internal name."""
    if host in ("localhost", "127.0.0.1", "::1"):
        return True
    if host in _UNREACHABLE_HOSTS or host.endswith(_UNREACHABLE_SUFFIXES):
        return False
    return "." in host or ":" in host  # FQDN, IPv4 or IPv6 literal


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.environment == "development" and settings.secret_key == _INSECURE_SECRET:
        log.warning("SECRET_KEY is not set — using an insecure development key")
    return settings


def reset_settings_cache() -> None:
    """For tests: re-read the environment on the next `get_settings()` call."""
    get_settings.cache_clear()
