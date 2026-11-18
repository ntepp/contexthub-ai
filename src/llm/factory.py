"""LLM providers and the administrator-controlled model catalogue.

Models are addressed as ``provider:model`` (e.g. ``ollama:llama3.1:8b``,
``anthropic:claude-sonnet-5-5``). Only entries of ``LLM_MODELS`` can be selected
from the UI or the API, so an administrator decides which providers — and therefore
which data flows — are allowed.

Gateway mode (``LLM_GATEWAY_URL`` set, ADR-001): models are ``gateway:<name>``, where
``<name>`` is the model name the gateway serves (``GET /v1/models``). The catalogue is
what the gateway serves — the gateway is the source of truth, the scope of the
application key acting as allow-list — narrowed by the ``gateway:`` entries of
``LLM_MODELS`` when there are any. Entries of other providers are ignored (warned once).
Every chat call goes to the gateway.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.config import get_settings
from src.llm import gateway
from src.utils.logging_config import get_logger

log = get_logger()


class LLMProvider(Enum):
    OLLAMA = "ollama"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OPENAI_COMPATIBLE = "openai_compatible"
    GATEWAY = "gateway"


PROVIDER_LABELS = {
    LLMProvider.OLLAMA: "Ollama (local)",
    LLMProvider.OPENAI: "OpenAI",
    LLMProvider.ANTHROPIC: "Anthropic",
    LLMProvider.OPENAI_COMPATIBLE: None,  # resolved from settings
    LLMProvider.GATEWAY: None,  # resolved from settings
}


class ModelNotAllowed(ValueError):
    pass


@dataclass(frozen=True)
class ModelSpec:
    provider: LLMProvider
    name: str

    @property
    def id(self) -> str:
        return f"{self.provider.value}:{self.name}"

    @property
    def is_local(self) -> bool:
        if self.provider == LLMProvider.OLLAMA:
            return True
        if self.provider == LLMProvider.OPENAI_COMPATIBLE:
            return get_settings().openai_compatible_is_local
        if self.provider == LLMProvider.GATEWAY:
            return get_settings().llm_gateway_is_local
        return False

    @property
    def provider_label(self) -> str:
        if self.provider == LLMProvider.OPENAI_COMPATIBLE:
            return get_settings().openai_compatible_label
        if self.provider == LLMProvider.GATEWAY:
            return get_settings().llm_gateway_label
        return PROVIDER_LABELS[self.provider] or self.provider.value

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "provider": self.provider.value,
            "provider_label": self.provider_label,
            "name": self.name,
            "is_local": self.is_local,
        }


def parse_model_id(model_id: str) -> ModelSpec:
    provider, sep, name = model_id.partition(":")
    if not sep or not name:
        raise ValueError(f"Identifiant de modèle invalide : {model_id!r} (attendu provider:modele)")
    try:
        return ModelSpec(LLMProvider(provider.strip().lower()), name.strip())
    except ValueError as exc:
        raise ValueError(f"Fournisseur LLM inconnu : {provider!r}") from exc


_warned_ignored: tuple[str, ...] = ()


def ignored_model_entries() -> list[str]:
    """Entries of LLM_MODELS that gateway mode ignores (other providers than ``gateway``)."""
    settings = get_settings()
    if not settings.gateway_enabled:
        return []
    return [e.id for e in map(parse_model_id, settings.split_csv(settings.llm_models)) if e.provider != LLMProvider.GATEWAY]


def _gateway_catalogue() -> list[ModelSpec]:
    """Models served by the gateway, narrowed by the ``gateway:`` entries of LLM_MODELS.

    Without any ``gateway:`` entry (empty list, or other providers only) the catalogue is
    everything the gateway serves, ``LLM_MODEL_NAME`` first when served. Never empty:
    when the gateway is unreachable (or serves none of the allowed models) the configured
    models are returned anyway, and ``GET /models`` marks them unavailable; a call to one
    of them then fails with a clear gateway message.
    """
    global _warned_ignored
    settings = get_settings()
    entries = [parse_model_id(e) for e in settings.split_csv(settings.llm_models)]
    allowed = [e.name for e in entries if e.provider == LLMProvider.GATEWAY]
    ignored = tuple(e.id for e in entries if e.provider != LLMProvider.GATEWAY)
    if ignored and ignored != _warned_ignored:
        _warned_ignored = ignored  # once per configuration, not on every request
        log.warning("[gateway] LLM_MODELS : entrées ignorées en mode passerelle : %s", ", ".join(ignored))
    served = gateway.served_models()
    if allowed:
        names = [n for n in allowed if served is not None and n in served] or allowed
    elif served:
        names = sorted(served, key=lambda n: n != settings.llm_model_name)
    else:
        names = [settings.llm_model_name]
    return [ModelSpec(LLMProvider.GATEWAY, n) for n in dict.fromkeys(names)]


def model_catalogue() -> list[ModelSpec]:
    settings = get_settings()
    if settings.gateway_enabled:
        return _gateway_catalogue()
    entries = settings.split_csv(settings.llm_models)
    if not entries:
        entries = [f"{settings.llm_provider}:{settings.llm_model_name}"]
    seen: dict[str, ModelSpec] = {}
    for entry in entries:
        spec = parse_model_id(entry)
        seen.setdefault(spec.id, spec)
    return list(seen.values())


def default_model() -> ModelSpec:
    return model_catalogue()[0]


def resolve_model(model_id: str | None, catalogue: list[ModelSpec] | None = None) -> ModelSpec:
    """The allowed model ``model_id`` (default model when empty), else ModelNotAllowed.

    In gateway mode ``model_catalogue()`` may call the gateway (blocking): async code
    passes a catalogue fetched with ``asyncio.to_thread`` or calls this in a thread.
    """
    catalogue = catalogue if catalogue is not None else model_catalogue()
    if not model_id:
        return catalogue[0]
    spec = parse_model_id(model_id)
    if spec.id not in {m.id for m in catalogue}:
        raise ModelNotAllowed(f"Le modèle {model_id} n'est pas autorisé par l'administrateur.")
    return spec


class LLMFactory:
    @staticmethod
    def create(
        spec: ModelSpec | None = None,
        *,
        streaming: bool = True,
        user_id: str | None = None,
        step: str | None = None,
    ):
        """Chat model for ``spec``. ``user_id`` (ContextHub UUID) and ``step`` are only
        sent in gateway mode, as attribution headers."""
        settings = get_settings()
        spec = spec or default_model()
        temperature = settings.llm_temperature
        timeout = settings.llm_timeout_seconds

        if settings.gateway_enabled:
            # Whatever the model, the call goes through the gateway: no provider key here.
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=spec.name,
                base_url=f"{settings.gateway_root}/v1",
                api_key=settings.llm_gateway_api_key or "not-needed",
                temperature=temperature,
                timeout=timeout,
                streaming=streaming,
                # The gateway owns retries and fallbacks; a refusal (budget) must surface fast.
                max_retries=1,
                default_headers=gateway.call_headers(user_id, step),
            )
        if spec.provider == LLMProvider.GATEWAY:
            raise RuntimeError("LLM_GATEWAY_URL n'est pas configurée.")

        if spec.provider == LLMProvider.OLLAMA:
            from langchain_ollama import ChatOllama

            return ChatOllama(
                model=spec.name,
                base_url=settings.ollama_base_url,
                temperature=temperature,
                reasoning=settings.ollama_reasoning,
                num_ctx=settings.ollama_num_ctx,
                client_kwargs={"timeout": timeout},
            )

        if spec.provider == LLMProvider.OPENAI:
            from langchain_openai import ChatOpenAI

            if not settings.openai_api_key:
                raise RuntimeError("OPENAI_API_KEY n'est pas configurée.")
            return ChatOpenAI(
                model=spec.name,
                api_key=settings.openai_api_key,
                temperature=temperature,
                timeout=timeout,
                streaming=streaming,
            )

        if spec.provider == LLMProvider.ANTHROPIC:
            from langchain_anthropic import ChatAnthropic

            if not settings.anthropic_api_key:
                raise RuntimeError("ANTHROPIC_API_KEY n'est pas configurée.")
            return ChatAnthropic(
                model=spec.name,
                api_key=settings.anthropic_api_key,
                temperature=temperature,
                timeout=timeout,
                max_tokens=2048,
                streaming=streaming,
            )

        if spec.provider == LLMProvider.OPENAI_COMPATIBLE:
            from langchain_openai import ChatOpenAI

            if not settings.openai_compatible_base_url:
                raise RuntimeError("OPENAI_COMPATIBLE_BASE_URL n'est pas configurée.")
            return ChatOpenAI(
                model=spec.name,
                base_url=settings.openai_compatible_base_url,
                api_key=settings.openai_compatible_api_key or "not-needed",
                temperature=temperature,
                timeout=timeout,
                streaming=streaming,
            )

        raise ValueError(f"Fournisseur LLM non supporté : {spec.provider}")
