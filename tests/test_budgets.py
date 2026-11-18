"""Per-user budgets driven on the gateway (ADR-002): LiteLLM adapter against a fake proxy
(httpx.MockTransport reproducing the semantics read in LiteLLM's source), admin routes,
budgets in /admin/usage, audit."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from src.audit import service as audit
from src.llm import gateway
from src.usage import registry
from src.usage.base import BudgetConflict, BudgetPeriod, UsageError
from src.usage.litellm import managed_budget_id
from src.usage.litellm_budgets import LiteLLMBudgetManager
from tests.test_gateway import _activity_handler, gw  # noqa: F401  (fixture)

UID = str(uuid.uuid4())


class FakeLiteLLM:
    """The /customer and /budget endpoints ContextHub uses, as LiteLLM implements them."""

    def __init__(self):
        self.customers: dict[str, dict] = {}
        self.budgets: dict[str, dict] = {}
        self.calls: list[tuple[str, str, dict]] = []
        self.fail: dict[str, httpx.Response] = {}

    def writes(self) -> list[tuple[str, dict]]:
        return [(path, body) for method, path, body in self.calls if method == "POST"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer sk-admin"
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, path, body))
        if path in self.fail:
            return self.fail[path]
        if path == "/customer/info":
            customer = self.customers.get(request.url.params["end_user_id"])
            if customer is None:
                return httpx.Response(404, json={"error": {"message": "End User Id=x does not exist in db", "code": "404"}})
            table = self.budgets.get(customer["budget_id"]) if customer["budget_id"] else None
            return httpx.Response(200, json={**customer, "litellm_budget_table": table})
        if path == "/budget/new":
            if body["budget_id"] in self.budgets:
                return httpx.Response(400, json={"detail": {"error": f"Budget with id '{body['budget_id']}' already exists."}})
            self.budgets[body["budget_id"]] = {**body, "budget_reset_at": "2026-12-15T00:00:00Z"}
            return httpx.Response(200, json=self.budgets[body["budget_id"]])
        if path == "/budget/update":
            if body["budget_id"] not in self.budgets:
                return httpx.Response(500, json={"error": "RecordNotFound"})
            self.budgets[body["budget_id"]].update(body)
            return httpx.Response(200, json=self.budgets[body["budget_id"]])
        if path == "/budget/delete":
            self.budgets.pop(body["id"])
            for customer in self.customers.values():  # FK ON DELETE SET NULL
                if customer["budget_id"] == body["id"]:
                    customer["budget_id"] = None
            return httpx.Response(200, json={})
        if path == "/customer/new":
            if body["user_id"] in self.customers:
                return httpx.Response(400, json={"error": {"message": "Customer already exists, passed user_id=x"}})
            self.customers[body["user_id"]] = {"user_id": body["user_id"], "spend": 0.0, "blocked": False, "budget_id": body.get("budget_id")}
            return httpx.Response(200, json=self.customers[body["user_id"]])
        if path == "/customer/update":
            customer = self.customers.get(body["user_id"])
            if customer is None:
                return httpx.Response(404, json={"error": {"message": "End User Id=x does not exist in db"}})
            if body.get("budget_id"):
                customer["budget_id"] = body["budget_id"]
            return httpx.Response(200, json=customer)
        raise AssertionError(f"unexpected call {request.method} {path}")


@pytest.fixture
def fake():
    return FakeLiteLLM()


def _manager(fake) -> LiteLLMBudgetManager:
    return LiteLLMBudgetManager("sk-admin", transport=httpx.MockTransport(fake))


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


async def test_set_budget_creates_the_customer_when_unknown(gw, fake):  # noqa: F811
    budget = await _manager(fake).set_budget(UID, 5.0, BudgetPeriod.MONTH)
    own = managed_budget_id(UID)
    assert [p for p, _ in fake.writes()] == ["/budget/new", "/customer/new"]
    assert fake.writes()[0][1] == {"budget_id": own, "max_budget": 5.0, "budget_duration": "30d"}
    assert fake.writes()[1][1] == {"user_id": UID, "budget_id": own}
    assert budget.as_dict() == {
        "limit": 5.0,
        "spent": 0.0,
        "remaining": 5.0,
        "period": "30d",
        "period_kind": "month",
        "resets_at": "2026-12-15T00:00:00Z",
        "managed": True,
    }


async def test_set_budget_updates_an_existing_one(gw, fake):  # noqa: F811
    manager = _manager(fake)
    await manager.set_budget(UID, 5.0, BudgetPeriod.MONTH)
    fake.calls.clear()
    budget = await manager.set_budget(UID, 8.0, BudgetPeriod.MONTH)
    # Same window: the duration is not resent, so the gateway does not restart it.
    assert fake.writes() == [
        ("/budget/update", {"budget_id": managed_budget_id(UID), "max_budget": 8.0}),
        ("/customer/update", {"user_id": UID, "budget_id": managed_budget_id(UID)}),  # evicts the proxy cache
    ]
    assert budget.limit == 8.0
    fake.calls.clear()
    budget = await manager.set_budget(UID, 1.0, BudgetPeriod.DAY)
    assert fake.writes()[0][1] == {"budget_id": managed_budget_id(UID), "max_budget": 1.0, "budget_duration": "1d"}
    assert budget.period_kind == BudgetPeriod.DAY


async def test_set_budget_never_edits_a_shared_gateway_budget(gw, fake):  # noqa: F811
    fake.budgets["free_tier"] = {"budget_id": "free_tier", "max_budget": 1.0, "budget_duration": "1mo"}
    fake.customers[UID] = {"user_id": UID, "spend": 0.4, "blocked": False, "budget_id": "free_tier"}
    manager = _manager(fake)
    before = await manager.get_budget(UID)
    assert before.managed is False and before.period == "1mo" and before.period_kind is None
    budget = await manager.set_budget(UID, 3.0, BudgetPeriod.WEEK)
    assert [p for p, _ in fake.writes()] == ["/budget/new", "/customer/update"]
    assert fake.budgets["free_tier"]["max_budget"] == 1.0  # untouched: other customers share it
    assert budget.managed is True and budget.limit == 3.0 and budget.spent == 0.4 and budget.period == "7d"


async def test_set_budget_reuses_a_leftover_budget(gw, fake):  # noqa: F811
    fake.budgets[managed_budget_id(UID)] = {"budget_id": managed_budget_id(UID), "max_budget": 9.0, "budget_duration": "1d"}
    fake.customers[UID] = {"user_id": UID, "spend": 0.0, "blocked": False, "budget_id": None}
    budget = await _manager(fake).set_budget(UID, 2.0, BudgetPeriod.MONTH)
    assert [p for p, _ in fake.writes()] == ["/budget/new", "/budget/update", "/customer/update"]
    assert budget.limit == 2.0 and budget.period == "30d"


async def test_remove_budget(gw, fake):  # noqa: F811
    manager = _manager(fake)
    await manager.remove_budget(UID)  # unknown customer: nothing to do
    assert fake.writes() == []
    await manager.set_budget(UID, 5.0, BudgetPeriod.MONTH)
    fake.calls.clear()
    await manager.remove_budget(UID)
    assert fake.writes() == [("/budget/delete", {"id": managed_budget_id(UID)}), ("/customer/update", {"user_id": UID})]
    assert fake.customers[UID]["budget_id"] is None
    assert await manager.get_budget(UID) is None
    fake.calls.clear()
    await manager.remove_budget(UID)  # idempotent
    assert fake.writes() == []


async def test_remove_refuses_a_gateway_defined_budget(gw, fake):  # noqa: F811
    fake.budgets["free_tier"] = {"budget_id": "free_tier", "max_budget": 1.0}
    fake.customers[UID] = {"user_id": UID, "spend": 0.0, "blocked": False, "budget_id": "free_tier"}
    with pytest.raises(BudgetConflict, match="console"):
        await _manager(fake).remove_budget(UID)
    assert fake.writes() == [] and "free_tier" in fake.budgets


@pytest.mark.parametrize(
    ("path", "response", "fragment"),
    [
        ("/customer/info", httpx.Response(401, json={"error": "Unauthorized"}), "refuse la clé"),
        ("/customer/info", httpx.Response(502, text="<html>bad gateway</html>"), "indisponible \\(erreur 502\\)"),
        ("/budget/new", httpx.Response(422, json={"error": "bad"}), "a refusé la requête"),
        ("/budget/new", httpx.Response(302, headers={"location": "https://evil.test/"}), "redirection"),
        ("/budget/delete", httpx.Response(400, json={"error": "Not allowed access, your role=proxy_admin_viewer"}), "LLM_GATEWAY_ADMIN_KEY"),
    ],
)
async def test_gateway_errors_become_usage_errors(gw, fake, path, response, fragment):  # noqa: F811
    manager = _manager(fake)
    if path == "/budget/delete":
        await manager.set_budget(UID, 5.0, BudgetPeriod.MONTH)
    fake.fail[path] = response
    with pytest.raises(UsageError, match=fragment) as info:
        if path == "/budget/delete":
            await manager.remove_budget(UID)
        else:
            await manager.set_budget(UID, 5.0, BudgetPeriod.MONTH)
    assert "sk-admin" not in str(info.value) and "html" not in str(info.value) and "role" not in str(info.value)


async def test_timeout(gw):  # noqa: F811
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    manager = LiteLLMBudgetManager("sk-admin", transport=httpx.MockTransport(handler))
    with pytest.raises(UsageError, match="délai dépassé"):
        await manager.get_budget(UID)


async def test_batch_read_stops_after_a_failure(gw, fake):  # noqa: F811
    ids = [str(uuid.uuid4()) for _ in range(20)]
    fake.budgets["b"] = {"budget_id": "b", "max_budget": 2.0, "budget_duration": "1d"}
    fake.customers[ids[0]] = {"user_id": ids[0], "spend": 1.0, "blocked": False, "budget_id": "b"}
    batch = await _manager(fake).get_budgets(ids)
    assert batch.failed == [] and batch.budgets[ids[0]].limit == 2.0 and batch.budgets[ids[1]] is None

    fake.calls.clear()
    fake.fail["/customer/info"] = httpx.Response(503)
    batch = await _manager(fake).get_budgets(ids)
    assert sorted(batch.failed) == sorted(ids) and batch.budgets == {}
    assert len(fake.calls) <= 8  # at most the calls already in flight: no N timeouts


def test_registry_builds_the_budget_manager_only_when_configured(gw, monkeypatch):  # noqa: F811
    assert isinstance(registry.build_budget_manager(), LiteLLMBudgetManager)
    monkeypatch.setattr(gw, "llm_gateway_admin_key", None)
    assert registry.build_budget_manager() is None and registry.budgets_not_configured() == registry.NO_ADMIN_KEY
    monkeypatch.setattr(gw, "llm_gateway_url", None)
    assert registry.budgets_not_configured() == registry.NO_GATEWAY


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------


@pytest.fixture
def proxy(gw, fake):  # noqa: F811
    """The fake proxy answering every gateway call of the API (budgets and /v1/models)."""

    def handler(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "claude-sonnet"}]})
        return fake(request)

    gateway.set_gateway_transport(httpx.MockTransport(handler))
    return fake


def test_budget_routes(proxy, admin, member_user, db):
    path = f"/admin/users/{member_user.id}/budget"
    assert admin.get(path).json() == {"available": True, "user_id": str(member_user.id), "currency": "USD", "budget": None}

    resp = admin.put(path, json={"limit": 12.5, "period": "week"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["budget"]["limit"] == 12.5 and body["budget"]["period_kind"] == "week" and body["budget"]["managed"] is True
    assert admin.get(path).json() == body

    assert admin.put(path, json={"limit": 20, "period": "month"}).status_code == 200
    resp = admin.delete(path)
    assert resp.status_code == 200 and resp.json()["budget"] is None and resp.json()["currency"] == "USD"
    assert proxy.customers[str(member_user.id)]["budget_id"] is None

    items = admin.get("/admin/audit", params={"action": "budget."}).json()["items"]
    assert [e["action"] for e in items] == ["budget.remove", "budget.set", "budget.set"]
    remove, second, first = items
    week = {"limit": 12.5, "period_kind": "week", "period": "7d", "managed": True}
    month = {"limit": 20.0, "period_kind": "month", "period": "30d", "managed": True}
    assert first["details"] == {"old": None, "new": week}
    assert second["details"] == {"old": week, "new": month}  # old and new in the same shape
    assert remove["details"] == {"old": month, "new": None}
    # Target user as for grants and account changes: resource "user" + UUID, no e-mail hashed.
    assert all(e["resource_type"] == "user" and e["resource_id"] == str(member_user.id) for e in items)
    assert member_user.email not in json.dumps([e["details"] for e in items])
    assert "spent" not in json.dumps([e["details"] for e in items])
    assert audit.verify_chain(db).valid


def test_usage_me_shows_a_budget_set_by_the_administrator(gw, fake, admin, member, member_user):  # noqa: F811
    activity = _activity_handler([], subject=str(member_user.id))

    def handler(request):
        if request.url.path == "/customer/daily/activity":
            return activity(request)
        return fake(request)

    gateway.set_gateway_transport(httpx.MockTransport(handler))
    assert admin.put(f"/admin/users/{member_user.id}/budget", json={"limit": 3, "period": "day"}).status_code == 200
    budget = member.get("/usage/me").json()["budget"]
    assert budget["limit"] == 3.0 and budget["period"] == "1d" and budget["period_kind"] == "day" and budget["managed"] is True
    assert member.get("/usage/me").json()["currency"] == admin.get(f"/admin/users/{member_user.id}/budget").json()["currency"]


@pytest.mark.parametrize(
    ("payload", "fragment"),
    [
        ({"limit": 0, "period": "month"}, "strictement positif"),
        ({"limit": -5, "period": "month"}, "strictement positif"),
        ({"limit": 2_000_000, "period": "month"}, "1 000 000"),
        ({"limit": 5, "period": "year"}, "Période inconnue"),
    ],
)
def test_budget_put_is_validated(proxy, admin, member_user, payload, fragment):
    resp = admin.put(f"/admin/users/{member_user.id}/budget", json=payload)
    assert resp.status_code == 422 and fragment in resp.json()["detail"]
    assert proxy.writes() == []


def test_budget_of_an_unknown_user_is_404(proxy, admin):
    path = f"/admin/users/{uuid.uuid4()}/budget"
    assert admin.get(path).status_code == 404
    assert admin.put(path, json={"limit": 5, "period": "month"}).status_code == 404
    assert admin.delete(path).status_code == 404
    assert proxy.calls == []


def test_budget_routes_are_admin_only(proxy, member, anon, member_user):
    path = f"/admin/users/{member_user.id}/budget"
    for client, status in ((member, 403), (anon, 401)):
        assert client.get(path).status_code == status
        assert client.put(path, json={"limit": 5, "period": "month"}).status_code == status
        assert client.delete(path).status_code == status
    assert proxy.calls == []


def test_removing_a_gateway_defined_budget_is_409(proxy, admin, member_user):
    proxy.budgets["free_tier"] = {"budget_id": "free_tier", "max_budget": 1.0}
    proxy.customers[str(member_user.id)] = {"user_id": str(member_user.id), "spend": 0.0, "blocked": False, "budget_id": "free_tier"}
    resp = admin.delete(f"/admin/users/{member_user.id}/budget")
    assert resp.status_code == 409 and "console" in resp.json()["detail"]


def test_gateway_failure_is_503_and_audited(proxy, admin, member_user):
    proxy.fail["/budget/new"] = httpx.Response(500)
    resp = admin.put(f"/admin/users/{member_user.id}/budget", json={"limit": 5, "period": "month"})
    assert resp.status_code == 503 and "indisponible" in resp.json()["detail"]
    event = admin.get("/admin/audit", params={"action": "budget.set"}).json()["items"][0]
    assert event["outcome"] == "failure"
    assert event["details"] == {"new": {"limit": 5.0, "period_kind": "month", "period": None, "managed": True}}


def test_budgets_without_gateway_or_admin_key(admin, member_user, monkeypatch):
    path = f"/admin/users/{member_user.id}/budget"
    body = admin.get(path).json()
    assert body["available"] is False and "Aucune passerelle" in body["reason"]
    assert admin.put(path, json={"limit": 5, "period": "month"}).status_code == 503
    assert admin.delete(path).status_code == 503
    # Models stay manageable without a gateway.
    assert admin.put("/admin/models", json={"offered": ["ollama:fake-model"], "default": "ollama:fake-model"}).status_code == 200


def test_budgets_without_admin_key(gw, admin, member_user, monkeypatch):  # noqa: F811
    monkeypatch.setattr(gw, "llm_gateway_admin_key", None)
    path = f"/admin/users/{member_user.id}/budget"
    assert "LLM_GATEWAY_ADMIN_KEY" in admin.get(path).json()["reason"]
    resp = admin.put(path, json={"limit": 5, "period": "month"})
    assert resp.status_code == 503 and "LLM_GATEWAY_ADMIN_KEY" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Budgets in /admin/usage
# ---------------------------------------------------------------------------


def _usage_gateway(member_user, info):
    activity = _activity_handler([], subject=str(member_user.id))
    calls: list[str] = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/customer/info":
            return info(request)
        return activity(request)

    gateway.set_gateway_transport(httpx.MockTransport(handler))
    return calls


def test_admin_usage_carries_the_budgets(gw, admin, member_user):  # noqa: F811
    def info(request):
        assert request.url.params["end_user_id"] == str(member_user.id)  # ContextHub accounts only
        return httpx.Response(
            200,
            json={
                "user_id": str(member_user.id),
                "spend": 0.75,
                "budget_id": managed_budget_id(str(member_user.id)),
                "litellm_budget_table": {"max_budget": 2.0, "budget_duration": "30d"},
            },
        )

    calls = _usage_gateway(member_user, info)
    body = admin.get("/admin/usage", params={"from": "2026-11-01", "to": "2026-11-14"}).json()
    known, unknown = body["by_user"]
    assert known["budget"] == {
        "limit": 2.0,
        "spent": 0.75,
        "remaining": 1.25,
        "period": "30d",
        "period_kind": "month",
        "resets_at": None,
        "managed": True,
    }
    assert unknown["budget"] is None
    assert body["missing"] == [] and body["budgets_available"] is True
    assert calls.count("/customer/info") == 1


def test_admin_usage_survives_a_budget_read_failure(gw, admin, member_user):  # noqa: F811
    _usage_gateway(member_user, lambda request: httpx.Response(500))
    resp = admin.get("/admin/usage", params={"from": "2026-11-01", "to": "2026-11-14"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["totals"]["requests"] == 6  # consumption still served
    assert body["missing"] == ["budgets"] and [u["budget"] for u in body["by_user"]] == [None, None]


def test_admin_usage_bounds_the_budget_lookups(gw, admin, member_user, monkeypatch):  # noqa: F811
    from src.api.routes import usage

    monkeypatch.setattr(usage, "MAX_BUDGET_LOOKUPS", 0)
    calls = _usage_gateway(member_user, lambda request: httpx.Response(200, json={}))
    body = admin.get("/admin/usage", params={"from": "2026-11-01", "to": "2026-11-14"}).json()
    assert "/customer/info" not in calls and body["missing"] == ["budgets"]


def test_admin_usage_without_admin_key_for_budgets(gw, admin, member_user, monkeypatch):  # noqa: F811
    from src.usage.litellm import LiteLLMUsageProvider

    _usage_gateway(member_user, lambda request: httpx.Response(200, json={}))
    registry.set_usage_provider_override(lambda: LiteLLMUsageProvider("sk-admin"))
    monkeypatch.setattr(gw, "llm_gateway_admin_key", None)
    try:
        body = admin.get("/admin/usage", params={"from": "2026-11-01", "to": "2026-11-14"}).json()
    finally:
        registry.set_usage_provider_override(None)
    assert body["budgets_available"] is False and body["missing"] == []
    assert all(u["budget"] is None for u in body["by_user"])


def test_unsupported_gateway_kind_is_refused_at_startup_and_never_a_500(gw, admin, member, member_user, monkeypatch):  # noqa: F811
    from src.config import Settings

    with pytest.raises(ValueError, match="accepted values: litellm"):
        Settings(llm_gateway_kind="portkey", _env_file=None)
    assert Settings(llm_gateway_kind=" LiteLLM ", _env_file=None).llm_gateway_kind == "litellm"

    monkeypatch.setattr(gw, "llm_gateway_kind", "portkey")  # bypassing the startup check
    assert registry.build_budget_manager() is None and registry.build_usage_provider() is None
    body = admin.get(f"/admin/users/{member_user.id}/budget").json()
    assert body["available"] is False and "LLM_GATEWAY_KIND" in body["reason"]
    resp = admin.put(f"/admin/users/{member_user.id}/budget", json={"limit": 5, "period": "month"})
    assert resp.status_code == 503 and "LLM_GATEWAY_KIND" in resp.json()["detail"]
    usage = admin.get("/admin/usage").json()
    assert usage["available"] is False and "LLM_GATEWAY_KIND" in usage["reason"]
    me = member.get("/usage/me").json()
    assert me["available"] is False and "LLM_GATEWAY_KIND" not in me["reason"]  # no detail for members
