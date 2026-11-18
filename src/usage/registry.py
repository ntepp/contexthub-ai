"""Builds the consumption provider and the budget manager of the configured LLM gateway."""

from __future__ import annotations

from collections.abc import Callable

from src.config import SUPPORTED_GATEWAY_KINDS, get_settings
from src.usage.base import BudgetManager, UsageProvider
from src.usage.litellm import LiteLLMUsageProvider
from src.usage.litellm_budgets import LiteLLMBudgetManager

_factory_override: Callable[[], UsageProvider | None] | None = None
_budget_override: Callable[[], BudgetManager | None] | None = None


def set_usage_provider_override(factory: Callable[[], UsageProvider | None] | None) -> None:
    """For tests: replace the gateway by a fake."""
    global _factory_override
    _factory_override = factory


NO_GATEWAY = "no_gateway"
NO_ADMIN_KEY = "no_admin_key"
UNSUPPORTED_KIND = "unsupported_kind"  # rejected at startup; kept as a safety net


def set_budget_manager_override(factory: Callable[[], BudgetManager | None] | None) -> None:
    """For tests: replace the gateway's budget API by a fake."""
    global _budget_override
    _budget_override = factory


def _not_configured() -> str | None:
    settings = get_settings()
    if not settings.gateway_enabled:
        return NO_GATEWAY
    if settings.llm_gateway_kind not in SUPPORTED_GATEWAY_KINDS:
        return UNSUPPORTED_KIND
    if not settings.llm_gateway_admin_key:
        return NO_ADMIN_KEY
    return None


def usage_not_configured() -> str | None:
    """Why consumption cannot be read (``NO_GATEWAY``, ``UNSUPPORTED_KIND``, ``NO_ADMIN_KEY``), or None."""
    return None if _factory_override is not None else _not_configured()


def budgets_not_configured() -> str | None:
    """Why budgets cannot be managed (``NO_GATEWAY``, ``UNSUPPORTED_KIND``, ``NO_ADMIN_KEY``), or None."""
    return None if _budget_override is not None else _not_configured()


def build_budget_manager() -> BudgetManager | None:
    """The budget manager for LLM_GATEWAY_KIND, or None without a gateway or without
    LLM_GATEWAY_ADMIN_KEY (budgets are admin operations on the gateway)."""
    if _budget_override is not None:
        return _budget_override()
    if _not_configured() is not None:
        return None
    return LiteLLMBudgetManager(get_settings().llm_gateway_admin_key)  # the only supported kind


def build_usage_provider() -> UsageProvider | None:
    """The provider for LLM_GATEWAY_KIND, or None when consumption cannot be read
    (no gateway, or no LLM_GATEWAY_ADMIN_KEY: the application key is never used for it)."""
    if _factory_override is not None:
        return _factory_override()
    if usage_not_configured() is not None:
        return None
    return LiteLLMUsageProvider(get_settings().llm_gateway_admin_key)  # the only supported kind
