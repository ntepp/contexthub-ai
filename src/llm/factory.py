"""LLM providers and the administrator-controlled model catalogue.

Models are addressed as ``provider:model`` (e.g. ``ollama:llama3.1:8b``,
``anthropic:claude-sonnet-5-5``). Only entries of ``LLM_MODELS`` can be selected
from the UI or the API, so an administrator decides which providers — and therefore
which data flows — are allowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.config import get_settings


class LLMProvider(Enum):
    OLLAMA = "ollama"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OPENAI_COMPATIBLE = "openai_compatible"


PROVIDER_LABELS = {
    LLMProvider.OLLAMA: "Ollama (local)",
    LLMProvider.OPENAI: "OpenAI",
    LLMProvider.ANTHROPIC: "Anthropic",
    LLMProvider.OPENAI_COMPATIBLE: None,  # resolved from settings
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
        return False

    @property
    def provider_label(self) -> str:
        if self.provider == LLMProvider.OPENAI_COMPATIBLE:
            return get_settings().openai_compatible_label
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


def model_catalogue() -> list[ModelSpec]:
    settings = get_settings()
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


def resolve_model(model_id: str | None) -> ModelSpec:
    if not model_id:
        return default_model()
    spec = parse_model_id(model_id)
    if spec.id not in {m.id for m in model_catalogue()}:
        raise ModelNotAllowed(f"Le modèle {model_id} n'est pas autorisé par l'administrateur.")
    return spec


class LLMFactory:
    @staticmethod
    def create(spec: ModelSpec | None = None, *, streaming: bool = True):
        settings = get_settings()
        spec = spec or default_model()
        temperature = settings.llm_temperature
        timeout = settings.llm_timeout_seconds

        if spec.provider == LLMProvider.OLLAMA:
            from langchain_ollama import ChatOllama

            return ChatOllama(
                model=spec.name,
                base_url=settings.ollama_base_url,
                temperature=temperature,
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
