"""Per-user budgets driven on a LiteLLM proxy (ADR-002), open-source edition only.

ContextHub gives each user a budget of its own on the gateway, id
``contexthub-<uuid>`` (``managed_budget_id``), linked to the LiteLLM customer whose
``user_id`` is the ContextHub UUID. Endpoints (source:
``litellm/proxy/management_endpoints/customer_endpoints.py`` and
``budget_management_endpoints.py``, checked on main and on the pinned v1.106.6):

* ``GET /customer/info?end_user_id=`` — the customer, ``budget_id``, ``spend`` and
  ``litellm_budget_table``; 404 (``not_found``) when the gateway has never seen it.
* ``POST /budget/new`` ``{budget_id, max_budget, budget_duration}`` — sets
  ``budget_reset_at``; 400 "already exists" for a known id.
* ``POST /budget/update`` ``{budget_id, max_budget[, budget_duration]}`` — sending
  ``budget_duration`` recomputes ``budget_reset_at``, so it is only sent when it changes.
* ``POST /customer/new`` ``{user_id, budget_id}`` — 400 "already exists" for a known
  customer; ``POST /customer/update`` ``{user_id, budget_id}`` — 404 for an unknown one.
  Both evict the proxy's cached customer, which ``/budget/update`` and ``/budget/delete``
  do not: every change ends with one of them.
* ``POST /budget/delete`` ``{id}`` — proxy-admin only. The customer's foreign key is
  ``ON DELETE SET NULL`` (baseline migration): the customer goes back to no budget of its
  own, i.e. the gateway default (``max_end_user_budget_id``) if one is configured.

Why not ``/customer/update`` with ``max_budget`` alone: it edits whatever budget row the
customer is linked to, which may be shared with other customers (a tier set in the
console), and it ignores ``max_budget: null`` / ``0`` (``_should_update_field``), so a
budget cannot be removed that way. A budget ContextHub did not set is never modified:
setting one replaces the link (the shared row is untouched), removing one is refused.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import httpx

from src.llm import gateway
from src.usage.base import GATEWAY_CURRENCY, Budget, BudgetBatch, BudgetConflict, BudgetPeriod, UsageError
from src.usage.litellm import BUDGET_DURATIONS, BUDGETS, SYSTEM, _raise_for_status, customer_budget, managed_budget_id
from src.utils.logging_config import get_logger

log = get_logger()

# Batch reads: a few calls in parallel, and none once one has failed (a down gateway
# must not cost N timeouts).
BATCH_CONCURRENCY = 8

FOREIGN_BUDGET = (
    f"Ce budget a été défini directement dans {SYSTEM.lower()} (console ou budget partagé) : "
    "retirez-le depuis la console de la passerelle."
)


def _already_exists(resp: httpx.Response) -> bool:
    return resp.status_code == 400 and "already exists" in resp.text[:2000].lower()


class LiteLLMBudgetManager:
    kind = "litellm"
    currency = GATEWAY_CURRENCY  # LiteLLM budgets are in USD, like its spend

    def __init__(self, admin_key: str | None, transport: httpx.AsyncBaseTransport | None = None):
        self._admin_key = admin_key
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return gateway.async_client(self._admin_key, transport=self._transport)

    async def _send(self, client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise UsageError(f"{SYSTEM} ne répond pas (délai dépassé).") from exc
        except httpx.HTTPError as exc:
            raise UsageError(f"{SYSTEM} est injoignable.") from exc

    async def _post(self, client: httpx.AsyncClient, path: str, body: dict) -> None:
        _raise_for_status(await self._send(client, "POST", path, json=body), BUDGETS)

    async def _customer(self, client: httpx.AsyncClient, user_id: str) -> dict | None:
        resp = await self._send(client, "GET", "/customer/info", params={"end_user_id": user_id})
        if resp.status_code in (400, 404):  # never seen by the gateway
            return None
        _raise_for_status(resp, BUDGETS)
        try:
            data = resp.json()
        except ValueError as exc:
            raise UsageError(f"Réponse inattendue de {SYSTEM.lower()}.") from exc
        if not isinstance(data, dict):
            raise UsageError(f"Réponse inattendue de {SYSTEM.lower()}.")
        return data

    async def get_budget(self, user_id: str) -> Budget | None:
        async with self._client() as client:
            return customer_budget(await self._customer(client, user_id), user_id)

    async def get_budgets(self, user_ids: Sequence[str]) -> BudgetBatch:
        batch = BudgetBatch()
        semaphore = asyncio.Semaphore(BATCH_CONCURRENCY)
        broken = False

        async def one(client: httpx.AsyncClient, user_id: str) -> None:
            nonlocal broken
            async with semaphore:
                if broken:
                    batch.failed.append(user_id)
                    return
                try:
                    batch.budgets[user_id] = customer_budget(await self._customer(client, user_id), user_id)
                except UsageError as exc:
                    broken = True
                    batch.failed.append(user_id)
                    log.warning("[budgets] lecture interrompue : %s", exc)

        async with self._client() as client:
            await asyncio.gather(*(one(client, uid) for uid in dict.fromkeys(user_ids)))
        return batch

    async def set_budget(self, user_id: str, limit: float, period: BudgetPeriod) -> Budget:
        own = managed_budget_id(user_id)
        duration = BUDGET_DURATIONS[BudgetPeriod(period)]
        async with self._client() as client:
            customer = await self._customer(client, user_id)
            table = (customer or {}).get("litellm_budget_table") or {}
            if customer is not None and customer.get("budget_id") == own:
                body: dict[str, Any] = {"budget_id": own, "max_budget": limit}
                if table.get("budget_duration") != duration:
                    body["budget_duration"] = duration  # also restarts the window
                await self._post(client, "/budget/update", body)
            else:
                body = {"budget_id": own, "max_budget": limit, "budget_duration": duration}
                resp = await self._send(client, "POST", "/budget/new", json=body)
                if _already_exists(resp):  # left over from a previous link: reuse it
                    await self._post(client, "/budget/update", body)
                else:
                    _raise_for_status(resp, BUDGETS)
            # Link (or re-link) the customer; this also evicts the proxy's cached customer.
            created = False
            if customer is None:
                resp = await self._send(client, "POST", "/customer/new", json={"user_id": user_id, "budget_id": own})
                if not _already_exists(resp):  # else: created meanwhile by a chat call
                    _raise_for_status(resp, BUDGETS)
                    created = True
            if not created:
                await self._post(client, "/customer/update", {"user_id": user_id, "budget_id": own})
            budget = customer_budget(await self._customer(client, user_id), user_id)
        if budget is None or not budget.managed:
            raise UsageError(f"Réponse incohérente de {SYSTEM.lower()} : le budget n'a pas été appliqué.")
        return budget

    async def remove_budget(self, user_id: str) -> None:
        own = managed_budget_id(user_id)
        async with self._client() as client:
            customer = await self._customer(client, user_id)
            if customer is None or not customer.get("budget_id"):
                return  # nothing of its own: idempotent
            if customer.get("budget_id") != own:
                raise BudgetConflict(FOREIGN_BUDGET)
            await self._post(client, "/budget/delete", {"id": own})
            try:
                # /budget/delete does not evict the cached customer: touch it so the limit
                # stops applying now rather than when the proxy's cache expires.
                await self._post(client, "/customer/update", {"user_id": user_id})
            except UsageError as exc:
                log.warning("[budgets] budget retiré, cache de la passerelle non rafraîchi : %s", exc)
