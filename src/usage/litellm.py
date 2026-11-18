"""Consumption read from a LiteLLM proxy, open-source edition only.

Endpoints used (none Enterprise-gated):

* ``GET /customer/daily/activity`` — daily spend of end users ("customers"). Source:
  ``litellm/proxy/management_endpoints/customer_endpoints.py``, response types in
  ``litellm/types/proxy/management_endpoints/common_daily_activity.py`` (branch main).
  Parameters ``start_date``, ``end_date``, ``end_user_ids`` (comma-separated), ``page``,
  ``page_size``; response ``results[].{date, metrics, breakdown{models, entities}}`` and
  ``metadata{page, total_pages, has_more}``. ``breakdown.entities`` (per customer) is
  always part of the schema. **Proxy-admin keys only** (401 "Admin-only endpoint"
  otherwise): LLM_GATEWAY_ADMIN_KEY must be a gateway administrator key.
* ``GET /customer/info?end_user_id=`` — the customer's budget (``litellm_budget_table``)
  and the spend counted against it (https://docs.litellm.ai/docs/proxy/customers).

``/global/spend/report`` is Enterprise-only and deliberately not used.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from src.llm import gateway
from src.usage.base import (
    Budget,
    DailyUsage,
    GlobalUsageReport,
    ModelUsage,
    UsageError,
    UsageReport,
    UsageTotals,
    UserUsage,
)
from src.utils.logging_config import get_logger

log = get_logger()

SYSTEM = "La passerelle LLM"
_PAGE_SIZE = 100
_MAX_PAGES = 50


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _totals(metrics: Any) -> UsageTotals:
    m = metrics if isinstance(metrics, dict) else {}
    return UsageTotals(
        cost=_num(m.get("spend")),
        input_tokens=int(_num(m.get("prompt_tokens"))),
        output_tokens=int(_num(m.get("completion_tokens"))),
        requests=int(_num(m.get("api_requests"))),
    )


def _raise_for_status(resp: httpx.Response) -> None:
    code = resp.status_code
    if code < 300:
        return
    if code < 400:
        raise UsageError(f"{SYSTEM} a répondu par une redirection : vérifiez LLM_GATEWAY_URL.")
    if code in (401, 403) and "admin-only" in resp.text[:2000].lower():
        # The remote text is only inspected, never shown.
        raise UsageError(
            f"{SYSTEM} exige une clé d'administrateur pour lire la consommation : "
            "LLM_GATEWAY_ADMIN_KEY doit être une clé d'administrateur de la passerelle."
        )
    if code in (401, 403):
        raise UsageError(f"{SYSTEM} refuse la clé de service de ContextHub (lecture de la consommation).")
    if code == 404:
        raise UsageError(f"{SYSTEM} ne propose pas ce point d'accès de consommation (version de LiteLLM ?).")
    if code == 429:
        raise UsageError(f"{SYSTEM} limite le débit des requêtes : réessayez dans un instant.")
    raise UsageError(f"{SYSTEM} est indisponible (erreur {code}).")


class LiteLLMUsageProvider:
    kind = "litellm"

    def __init__(self, admin_key: str | None, transport: httpx.AsyncBaseTransport | None = None):
        self._admin_key = admin_key
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return gateway.async_client(self._admin_key, transport=self._transport)

    async def _get(self, client: httpx.AsyncClient, path: str, params: dict, *, allow: tuple[int, ...] = ()) -> dict | None:
        try:
            resp = await client.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise UsageError(f"{SYSTEM} ne répond pas (délai dépassé).") from exc
        except httpx.HTTPError as exc:
            raise UsageError(f"{SYSTEM} est injoignable.") from exc
        if resp.status_code in allow:
            return None
        _raise_for_status(resp)
        try:
            data = resp.json()
        except ValueError as exc:
            raise UsageError(f"Réponse inattendue de {SYSTEM.lower()}.") from exc
        if not isinstance(data, dict):
            raise UsageError(f"Réponse inattendue de {SYSTEM.lower()}.")
        return data

    async def _daily_activity(self, start: date, end: date, end_user_id: str | None) -> list[dict]:
        params: dict[str, Any] = {
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "page_size": _PAGE_SIZE,
        }
        if end_user_id is not None:
            params["end_user_ids"] = end_user_id
        results: list[dict] = []
        async with self._client() as client:
            for page in range(1, _MAX_PAGES + 1):
                data = await self._get(client, "/customer/daily/activity", {**params, "page": page}) or {}
                rows = data.get("results")
                if not isinstance(rows, list):
                    raise UsageError(f"Réponse inattendue de {SYSTEM.lower()}.")
                results.extend(r for r in rows if isinstance(r, dict))
                meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
                total_pages = int(_num(meta.get("total_pages")) or 1)
                if not meta.get("has_more") and page >= total_pages:
                    break
            else:
                log.warning("[usage] activité tronquée après %d pages", _MAX_PAGES)
        return results

    @staticmethod
    def _aggregate(rows: list[dict], report: UsageReport) -> dict[str, UsageTotals] | None:
        """Fill totals / by_model / daily; return the per-entity totals (None if absent).

        Pages are cut on the gateway's underlying rows, so one day can appear on several
        pages: everything is summed by key.
        """
        days: dict[date, UsageTotals] = {}
        models: dict[str, UsageTotals] = {}
        entities: dict[str, UsageTotals] | None = None
        has_models = False
        for row in rows:
            try:
                day = date.fromisoformat(str(row.get("date"))[:10])
            except ValueError:
                continue
            totals = _totals(row.get("metrics"))
            days.setdefault(day, UsageTotals()).add(totals)
            report.totals.add(totals)
            breakdown = row.get("breakdown") if isinstance(row.get("breakdown"), dict) else {}
            if isinstance(breakdown.get("models"), dict):
                has_models = True
                for name, item in breakdown["models"].items():
                    models.setdefault(str(name), UsageTotals()).add(_totals((item or {}).get("metrics")))
            if isinstance(breakdown.get("entities"), dict):
                entities = entities if entities is not None else {}
                for entity, item in breakdown["entities"].items():
                    entities.setdefault(str(entity), UsageTotals()).add(_totals((item or {}).get("metrics")))
        report.daily = [DailyUsage(d, t) for d, t in sorted(days.items())]
        report.by_model = sorted((ModelUsage(m, t) for m, t in models.items()), key=lambda m: -m.totals.cost)
        if rows and not has_models:
            report.missing.append("by_model")
        return entities

    async def _budget(self, user_id: str) -> Budget | None:
        async with self._client() as client:
            # 400/404: the gateway has never seen this customer, hence no budget of its own.
            data = await self._get(client, "/customer/info", {"end_user_id": user_id}, allow=(400, 404))
        if not data:
            return None
        table = data.get("litellm_budget_table") if isinstance(data.get("litellm_budget_table"), dict) else {}
        limit = table.get("max_budget")
        if limit is None:
            return None
        return Budget(
            limit=_num(limit),
            spent=_num(data.get("spend")),
            period=table.get("budget_duration") or None,
            resets_at=data.get("budget_reset_at") or table.get("budget_reset_at") or None,
        )

    async def user_usage(self, user_id: str, start: date, end: date) -> UsageReport:
        report = UsageReport(start=start, end=end)
        entities = self._aggregate(await self._daily_activity(start, end, user_id), report)
        # Never show a total that cannot be proven to be this user's: consumption must come
        # with a per-customer breakdown naming this user, and only this user.
        consumed = bool(report.totals.cost or report.totals.requests or report.totals.input_tokens or report.totals.output_tokens)
        foreign = bool(entities and set(entities) - {user_id})
        unproven = consumed and not (entities and user_id in entities)
        if foreign or unproven:
            log.error(
                "[usage] activité non attribuable à l'utilisateur demandé (autres clients=%s, ventilation absente=%s)",
                foreign,
                unproven and not foreign,
            )
            raise UsageError(f"Réponse incohérente de {SYSTEM.lower()} : consommation non affichée.")
        report.budget = await self._budget(user_id)
        if report.budget is None:
            report.missing.append("budget")
        return report

    async def global_usage(self, start: date, end: date) -> GlobalUsageReport:
        report = GlobalUsageReport(start=start, end=end)
        entities = self._aggregate(await self._daily_activity(start, end, None), report)
        if entities is None:
            if report.totals.requests or report.daily:
                report.missing.append("by_user")
        else:
            report.by_user = sorted(
                (UserUsage(uid, t) for uid, t in entities.items()), key=lambda u: -u.totals.cost
            )
        # No global budget exists: ``budget`` stays None and is not listed in ``missing``.
        return report
