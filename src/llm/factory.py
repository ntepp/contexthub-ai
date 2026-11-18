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

Offered models (ADR-002): the administrator may store the list of offered models and the
default one in ``hub_settings`` (key ``models``). That setting, when present, replaces the
environment selection above; it can only pick models of the *available catalogue* (what
the gateway serves, or without a gateway the ``LLM_MODELS`` entries, whose provider keys
are configured). An offered model that left the available catalogue stays listed, marked
unavailable, and cannot be selected. Without the setting nothing changes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from src import instance_settings
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


def _environment_catalogue() -> list[ModelSpec]:
    """Offered models without an administrator setting (lot 1 behaviour), default first."""
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


# ---------------------------------------------------------------------------
# Offered models (ADR-002)
# ---------------------------------------------------------------------------

MODELS_SETTING = "models"
# Availability of a model as the API reports it ("available" stays a boolean for older clients).
AVAILABILITY_AVAILABLE = "available"
AVAILABILITY_UNAVAILABLE = "unavailable"
AVAILABILITY_UNKNOWN = "unknown"  # gateway (or Ollama) cannot be reached right now
MAX_OFFERED = 50
SOURCE_SETTINGS = "settings"
SOURCE_ENVIRONMENT = "environment"


class CatalogueUnavailable(RuntimeError):
    """The available catalogue is unknown (gateway unreachable)."""


def available_catalogue() -> list[ModelSpec] | None:
    """Models the administrator may offer: those the gateway serves (None when it cannot
    be reached), else the entries of LLM_MODELS (or LLM_PROVIDER:LLM_MODEL_NAME).
    Blocking in gateway mode (cached lookup)."""
    settings = get_settings()
    if not settings.gateway_enabled:
        entries = settings.split_csv(settings.llm_models) or [f"{settings.llm_provider}:{settings.llm_model_name}"]
        return list({spec.id: spec for spec in map(parse_model_id, entries)}.values())
    served = gateway.served_models()
    if served is None:
        return None
    return [ModelSpec(LLMProvider.GATEWAY, name) for name in served]


@dataclass(frozen=True)
class OfferedModel:
    spec: ModelSpec
    # In the available catalogue? None = unknown (gateway unreachable): still selectable,
    # the call then fails with the gateway's own message (lot 1 behaviour).
    available: bool | None


@dataclass(frozen=True)
class ModelOffer:
    models: tuple[OfferedModel, ...]  # in the administrator's order
    default_id: str
    source: str  # SOURCE_SETTINGS | SOURCE_ENVIRONMENT

    def selectable(self) -> list[ModelSpec]:
        """Models a request may use, default first (empty when none is available)."""
        specs = [m.spec for m in self.models if m.available is not False]
        return sorted(specs, key=lambda s: s.id != self.default_id)


def _stored_offer() -> tuple[list[str], str] | None:
    value = instance_settings.get(MODELS_SETTING)
    if not isinstance(value, dict):
        return None
    offered, default = value.get("offered"), value.get("default")
    if not isinstance(offered, list) or not offered or not isinstance(default, str):
        log.warning("[models] réglage des modèles illisible : valeurs d'environnement")
        return None
    ids: list[str] = []
    for raw in offered:
        try:
            ids.append(parse_model_id(str(raw)).id)
        except ValueError:
            continue  # an entry of a provider this version no longer knows
    return (list(dict.fromkeys(ids)), default) if ids else None


def model_offer() -> ModelOffer:
    """What the chat offers: the administrator setting if any, else the environment.
    Blocking (database read cached a few seconds, gateway lookup cached): not from the
    event loop."""
    stored = _stored_offer()
    if stored is None:
        specs = _environment_catalogue()
        return ModelOffer(tuple(OfferedModel(s, True) for s in specs), specs[0].id, SOURCE_ENVIRONMENT)
    ids, default = stored
    catalogue = available_catalogue()
    known = None if catalogue is None else {s.id for s in catalogue}
    models = tuple(OfferedModel(parse_model_id(i), None if known is None else i in known) for i in ids)
    selectable = [m.spec.id for m in models if m.available is not False]
    if default not in selectable:
        default = selectable[0] if selectable else ids[0]
    return ModelOffer(models, default, SOURCE_SETTINGS)


def validate_offer(offered: Sequence[str] | None, default: str | None) -> tuple[list[str], str]:
    """Normalised (offered ids, default id) for the administrator setting, else ValueError
    (French message) or CatalogueUnavailable. Blocking in gateway mode."""
    if not offered:
        raise ValueError("Proposez au moins un modèle.")
    if len(offered) > MAX_OFFERED:
        raise ValueError(f"Vous ne pouvez pas proposer plus de {MAX_OFFERED} modèles.")
    ids: list[str] = []
    for raw in offered:
        spec = parse_model_id(raw if isinstance(raw, str) else "")
        if not spec.name or len(spec.id) > 255:
            raise ValueError(f"Identifiant de modèle invalide : {raw!r} (attendu provider:modele)")
        if spec.id in ids:
            raise ValueError(f"Le modèle {spec.id} est en double dans la liste.")
        ids.append(spec.id)
    if not default:
        raise ValueError("Choisissez le modèle par défaut.")
    default_id = parse_model_id(default).id
    if default_id not in ids:
        raise ValueError("Le modèle par défaut doit faire partie des modèles proposés.")
    catalogue = available_catalogue()
    if catalogue is None:
        raise CatalogueUnavailable(
            "Le catalogue de la passerelle LLM est injoignable : impossible de vérifier les modèles. "
            "Réessayez dans un instant."
        )
    known = {s.id for s in catalogue}
    missing = [i for i in ids if i not in known]
    if missing:
        where = "servis par la passerelle LLM" if get_settings().gateway_enabled else "autorisés par LLM_MODELS"
        raise ValueError(f"Modèle(s) non disponible(s), car non {where} : {', '.join(missing)}.")
    return ids, default_id


def model_catalogue() -> list[ModelSpec]:
    """Selectable models, default first. Blocking (see ``model_offer``)."""
    return model_offer().selectable()


def default_model() -> ModelSpec:
    return resolve_model(None)


def resolve_model(model_id: str | None, catalogue: list[ModelSpec] | None = None) -> ModelSpec:
    """The allowed model ``model_id`` (default model when empty), else ModelNotAllowed.

    ``model_catalogue()`` is blocking (database setting, gateway lookup): async code
    passes a catalogue fetched with ``asyncio.to_thread`` or calls this in a thread.
    """
    catalogue = catalogue if catalogue is not None else model_catalogue()
    if not catalogue:
        raise ModelNotAllowed("Aucun des modèles proposés par l'administrateur n'est disponible actuellement.")
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
