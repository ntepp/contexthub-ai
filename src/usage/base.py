"""Contract of a consumption provider, independent of any gateway product.

Dates are inclusive calendar days (UTC, as the gateway aggregates them). A breakdown the
gateway cannot provide is returned empty and named in ``missing`` — never invented.

Values of ``missing``:

* ``"by_model"`` — the gateway reported consumption without a per-model breakdown;
  ``by_model`` is empty although ``totals`` is not.
* ``"by_user"`` — global report only: consumption without a per-user breakdown;
  ``by_user`` is empty although ``totals`` is not.
* ``"budget"`` — individual report only: the gateway knows no budget of this user's own
  (none defined, or only a gateway-wide default it does not expose); ``budget`` is None.
  The global report has no budget at all: ``budget`` is None and ``"budget"`` is never
  listed.
* ``"daily"`` — reserved for adapters whose gateway has no daily series. The LiteLLM
  adapter never sets it (its source is daily by construction): an empty ``daily`` there
  just means no consumption over the period.

An empty list with zero totals means "no consumption", not "unknown".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Protocol

# Currency of every amount the gateway reports or enforces (LiteLLM: USD).
GATEWAY_CURRENCY = "USD"


class UsageError(Exception):
    """The gateway could not answer. The message is shown as is: French, no secret,
    no raw remote response."""


class BudgetConflict(UsageError):
    """The change would touch a budget ContextHub does not manage (set on the gateway)."""


class BudgetPeriod(StrEnum):
    """Budget windows ContextHub offers; adapters translate them to the gateway's format."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"  # 30 days, not a calendar month


@dataclass
class UsageTotals:
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0

    def add(self, other: UsageTotals) -> None:
        self.cost += other.cost
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.requests += other.requests

    def as_dict(self) -> dict:
        return {
            "cost": round(self.cost, 6),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "requests": self.requests,
        }


@dataclass
class ModelUsage:
    model: str
    totals: UsageTotals

    def as_dict(self) -> dict:
        return {"model": self.model, **self.totals.as_dict()}


@dataclass
class DailyUsage:
    day: date
    totals: UsageTotals

    def as_dict(self) -> dict:
        return {"date": self.day.isoformat(), **self.totals.as_dict()}


@dataclass
class Budget:
    """What the gateway knows of a budget. ``spent`` is the gateway's own counter for the
    budget window (``period``), which may differ from the requested period."""

    limit: float | None
    spent: float | None
    period: str | None = None  # e.g. "30d", as configured on the gateway
    resets_at: str | None = None
    # ``period`` in ContextHub's terms, None when the gateway window is another one.
    period_kind: BudgetPeriod | None = None
    # True when the budget is ContextHub's own (set from its administration, removable
    # there); False for a budget defined on the gateway (console, shared budget).
    managed: bool = False

    @property
    def remaining(self) -> float | None:
        if self.limit is None or self.spent is None:
            return None
        return round(max(0.0, self.limit - self.spent), 6)

    def as_dict(self) -> dict:
        return {
            "limit": self.limit,
            "spent": None if self.spent is None else round(self.spent, 6),
            "remaining": self.remaining,
            "period": self.period,
            "period_kind": self.period_kind.value if self.period_kind else None,
            "resets_at": self.resets_at,
            "managed": self.managed,
        }


@dataclass
class UserUsage:
    user_id: str  # identifier as sent to the gateway (ContextHub UUID)
    totals: UsageTotals


@dataclass
class UsageReport:
    start: date
    end: date
    totals: UsageTotals = field(default_factory=UsageTotals)
    by_model: list[ModelUsage] = field(default_factory=list)
    daily: list[DailyUsage] = field(default_factory=list)
    budget: Budget | None = None
    # Breakdowns the gateway did not provide ("by_model", "daily", "budget", "by_user").
    missing: list[str] = field(default_factory=list)
    currency: str = GATEWAY_CURRENCY


@dataclass
class GlobalUsageReport(UsageReport):
    by_user: list[UserUsage] = field(default_factory=list)


class UsageProvider(Protocol):
    kind: str

    async def user_usage(self, user_id: str, start: date, end: date) -> UsageReport:
        """Consumption of one end user (ContextHub UUID) over [start, end]."""
        ...

    async def global_usage(self, start: date, end: date) -> GlobalUsageReport:
        """Consumption of every end user over [start, end], with the per-user breakdown."""
        ...


@dataclass
class BudgetBatch:
    """Budgets of several users: ``budgets[user_id]`` is the budget or None (no budget);
    users whose budget could not be read are in ``failed``, not in ``budgets``."""

    budgets: dict[str, Budget | None] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)


class BudgetManager(Protocol):
    """Per-user budgets, held and enforced by the gateway (ADR-002): ContextHub stores none.

    ``user_id`` is the ContextHub UUID, as sent to the gateway on every chat call.
    """

    kind: str
    currency: str  # of ``limit`` and ``spent``, same as the usage reports

    async def get_budget(self, user_id: str) -> Budget | None:
        """The user's own budget, None when the gateway knows none."""
        ...

    async def get_budgets(self, user_ids: Sequence[str]) -> BudgetBatch:
        """Budgets of several users; never raises for one user's failure."""
        ...

    async def set_budget(self, user_id: str, limit: float, period: BudgetPeriod) -> Budget:
        """Create or replace the user's budget (``limit`` > 0, gateway currency)."""
        ...

    async def remove_budget(self, user_id: str) -> None:
        """Remove the budget ContextHub set; BudgetConflict for a gateway-defined one."""
        ...
