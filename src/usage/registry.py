"""Builds the consumption provider of the configured LLM gateway."""

from __future__ import annotations

from collections.abc import Callable

from src.config import get_settings
from src.usage.base import UsageProvider
from src.usage.litellm import LiteLLMUsageProvider

_factory_override: Callable[[], UsageProvider | None] | None = None


def set_usage_provider_override(factory: Callable[[], UsageProvider | None] | None) -> None:
    """For tests: replace the gateway by a fake."""
    global _factory_override
    _factory_override = factory


NO_GATEWAY = "no_gateway"
NO_ADMIN_KEY = "no_admin_key"


def usage_not_configured() -> str | None:
    """Why consumption cannot be read (``NO_GATEWAY``, ``NO_ADMIN_KEY``), or None."""
    if _factory_override is not None:
        return None
    settings = get_settings()
    if not settings.gateway_enabled:
        return NO_GATEWAY
    if not settings.llm_gateway_admin_key:
        return NO_ADMIN_KEY
    return None


def build_usage_provider() -> UsageProvider | None:
    """The provider for LLM_GATEWAY_KIND, or None when consumption cannot be read
    (no gateway, or no LLM_GATEWAY_ADMIN_KEY: the application key is never used for it)."""
    if _factory_override is not None:
        return _factory_override()
    if usage_not_configured() is not None:
        return None
    settings = get_settings()
    if settings.llm_gateway_kind == "litellm":
        return LiteLLMUsageProvider(settings.llm_gateway_admin_key)
    raise ValueError(f"Type de passerelle LLM non supporté : {settings.llm_gateway_kind}")
