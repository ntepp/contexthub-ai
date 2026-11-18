"""LLM gateway (ADR-001): attribution of chat calls, model catalogue, refusals,
consumption read from LiteLLM (mocked HTTP) and the /usage routes."""

from __future__ import annotations

import json
import uuid
from datetime import date

import httpx
import openai
import pytest

from src.config import Settings, get_settings
from src.llm import gateway, llm_chain
from src.llm.factory import LLMFactory, ModelNotAllowed, model_catalogue, parse_model_id, resolve_model
from src.usage import registry
from src.usage.base import UsageError
from src.usage.litellm import LiteLLMUsageProvider
from tests.conftest import grant, upload
from tests.fakes import is_condense_prompt, make_pdf

GATEWAY = "http://gateway.test:4000"
PDF = make_pdf([["Article 93", "Transposition", "Les Etats membres appliquent ces dispositions a partir du 3 janvier 2017."]])
QUESTION = "A partir de quelle date les Etats membres appliquent ces dispositions ?"
OFF_TOPIC = "Recette du gateau au chocolat facile"


def _models_handler(names=("gpt-4o", "claude-sonnet")):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        assert request.headers["authorization"] == "Bearer sk-app"
        return httpx.Response(200, json={"data": [{"id": n, "object": "model"} for n in names], "object": "list"})

    return handler


@pytest.fixture
def gw(monkeypatch):
    """Gateway mode, the gateway answering through an httpx.MockTransport."""
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_gateway_url", GATEWAY)
    monkeypatch.setattr(settings, "llm_gateway_api_key", "sk-app")
    monkeypatch.setattr(settings, "llm_gateway_admin_key", "sk-admin")
    monkeypatch.setattr(settings, "llm_models", "")
    monkeypatch.setattr(settings, "llm_model_name", "claude-sonnet")
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    gateway.set_gateway_transport(httpx.MockTransport(_models_handler()))
    yield settings
    gateway.set_gateway_transport(None)


@pytest.fixture
def indexed(admin, member_user, collection):
    assert upload(admin, collection["id"], "directive.pdf", PDF).status_code == 202
    grant(admin, collection["id"], member_user.id, "viewer")
    return collection


@pytest.fixture
def recorder(monkeypatch, llm):
    """Goes through LLMFactory.create (no override) and records each call's attribution."""
    calls: list[dict] = []

    def create(spec=None, *, streaming=True, user_id=None, step=None):
        calls.append({"model": spec.id if spec else None, "user_id": user_id, "step": step})
        return llm

    llm_chain.set_llm_override(None)
    monkeypatch.setattr(llm_chain.LLMFactory, "create", staticmethod(create))
    return calls


def _events(resp) -> list[dict]:
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def _api_error(cls, status: int, message: str):
    response = httpx.Response(status, request=httpx.Request("POST", f"{GATEWAY}/v1/chat/completions"))
    return cls(message, response=response, body={"error": {"message": message}})


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_production_requires_a_gateway_key_and_a_clean_url():
    base = {"environment": "production", "secret_key": "x" * 40, "postgres_password": "s3cret", "_env_file": None}
    with pytest.raises(ValueError, match="LLM_GATEWAY_API_KEY"):
        Settings(llm_gateway_url=GATEWAY, **base)
    with pytest.raises(ValueError, match="credentials"):
        Settings(llm_gateway_url="http://user:pw@gateway.test:4000", llm_gateway_api_key="k", _env_file=None)
    with pytest.raises(ValueError, match="http"):
        Settings(llm_gateway_url="ftp://gateway.test", _env_file=None)
    ok = Settings(llm_gateway_url=f"{GATEWAY}/v1/", llm_gateway_api_key="k", **base)
    assert ok.gateway_root == GATEWAY and ok.llm_gateway_admin_key is None  # optional, no fallback
    assert Settings(_env_file=None).gateway_enabled is False


def test_call_headers_carry_only_a_uuid_and_the_step():
    uid = uuid.uuid4()
    headers = gateway.call_headers(uid, "plan")
    assert headers == {gateway.END_USER_HEADER: str(uid), gateway.TAGS_HEADER: "app:contexthub,step:plan"}
    with pytest.raises(ValueError):
        gateway.call_headers("analyste@banque.fr", "plan")
    with pytest.raises(ValueError):
        gateway.call_headers(uid, "retrieve")


def test_factory_routes_every_model_to_the_gateway(gw):
    uid = str(uuid.uuid4())
    for model_id in ("gateway:claude-sonnet", "anthropic:claude-sonnet-5-5", "ollama:llama3.1:8b"):
        chat = LLMFactory.create(parse_model_id(model_id), user_id=uid, step="generate")
        assert type(chat).__name__ == "ChatOpenAI"
        assert chat.openai_api_base == f"{GATEWAY}/v1"
        assert chat.openai_api_key.get_secret_value() == "sk-app"
        assert chat.default_headers[gateway.END_USER_HEADER] == uid
        assert chat.default_headers[gateway.TAGS_HEADER] == "app:contexthub,step:generate"


def test_gateway_model_without_gateway_is_refused():
    with pytest.raises(RuntimeError, match="LLM_GATEWAY_URL"):
        LLMFactory.create(parse_model_id("gateway:gpt-4o"))


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


def test_catalogue_reflects_the_gateway(gw, member):
    assert [m.id for m in model_catalogue()] == ["gateway:claude-sonnet", "gateway:gpt-4o"]
    body = member.get("/models").json()
    assert body["default"] == "gateway:claude-sonnet"
    assert body["models"][1] == {
        "id": "gateway:gpt-4o",
        "provider": "gateway",
        "provider_label": "Passerelle LLM",
        "name": "gpt-4o",
        "is_local": False,
        "available": True,
        "availability": "available",
    }


def test_llm_models_filters_the_gateway_catalogue(gw, monkeypatch):
    monkeypatch.setattr(gw, "llm_models", "gateway:gpt-4o,gateway:mistral-large,ollama:fake-model")
    assert [m.id for m in model_catalogue()] == ["gateway:gpt-4o"]
    assert resolve_model(None).id == "gateway:gpt-4o"
    with pytest.raises(ModelNotAllowed):
        resolve_model("gateway:claude-sonnet")  # served, but not allowed
    with pytest.raises(ModelNotAllowed):
        resolve_model("gateway:mistral-large")  # allowed, but not served


def test_unreachable_gateway_marks_models_unavailable(gw, monkeypatch, member):
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(gw, "llm_models", "gateway:gpt-4o,gateway:claude-sonnet")
    gateway.set_gateway_transport(httpx.MockTransport(down))
    resp = member.get("/models")
    assert resp.status_code == 200
    body = resp.json()
    assert [(m["id"], m["available"], m["availability"]) for m in body["models"]] == [
        ("gateway:gpt-4o", False, "unknown"),
        ("gateway:claude-sonnet", False, "unknown"),
    ]
    assert body["default"] == "gateway:gpt-4o"


def test_catalogue_is_cached(gw):
    hits = []

    def handler(request):
        hits.append(request.url.path)
        return _models_handler()(request)

    gateway.set_gateway_transport(httpx.MockTransport(handler))
    model_catalogue()
    model_catalogue()
    resolve_model("gateway:gpt-4o")
    assert hits == ["/v1/models"]


def test_without_gateway_the_catalogue_is_unchanged(member):
    body = member.get("/models").json()
    assert [m["id"] for m in body["models"]] == ["ollama:fake-model", "anthropic:claude-sonnet-5-5"]


# ---------------------------------------------------------------------------
# Attribution in the RAG graph
# ---------------------------------------------------------------------------


def test_user_and_step_reach_the_three_llm_nodes(gw, member, member_user, indexed, recorder, llm, monkeypatch):
    answer = llm.responder
    monkeypatch.setattr(llm, "responder", lambda m: QUESTION if is_condense_prompt(m) else answer(m))
    first = member.post("/chat/ask", json={"question": QUESTION}).json()
    recorder.clear()
    resp = member.post("/chat/ask", json={"question": "Et ensuite ?", "conversation_id": first["conversation_id"]})
    assert resp.status_code == 200, resp.text
    assert [c["step"] for c in recorder] == ["condense", "plan", "generate"]
    assert {c["user_id"] for c in recorder} == {str(member_user.id)}
    assert {c["model"] for c in recorder} == {"gateway:claude-sonnet"}

    recorder.clear()
    events = _events(member.post("/chat/stream", json={"question": QUESTION}))
    assert events[-1]["type"] == "done"
    assert [c["step"] for c in recorder] == ["plan", "generate"]
    assert {c["user_id"] for c in recorder} == {str(member_user.id)}


def test_refusal_still_calls_no_llm_in_gateway_mode(gw, member, indexed, recorder, llm):
    body = member.post("/chat/ask", json={"question": OFF_TOPIC}).json()
    assert body["refused"] is True
    assert recorder == [] and llm.calls == []


# ---------------------------------------------------------------------------
# Gateway refusals
# ---------------------------------------------------------------------------


def _failing_llm(monkeypatch, llm, exc):
    def responder(messages):
        if is_condense_prompt(messages) or "You plan the searches" in str(messages[-1].content):
            return "{}"
        raise exc

    monkeypatch.setattr(llm, "responder", responder)


def test_budget_exceeded_in_streaming(gw, member, indexed, llm, monkeypatch):
    exc = _api_error(openai.AuthenticationError, 401, "ExceededBudget: End User=x over budget. Spend=1.2, Budget=1.0")
    _failing_llm(monkeypatch, llm, exc)
    resp = member.post("/chat/stream", json={"question": QUESTION})
    events = _events(resp)
    assert events[0]["type"] == "meta"
    error = events[-1]
    assert error["type"] == "error" and error["code"] == "budget_exceeded" and error["status"] == 402
    assert "budget" in error["message"]
    assert "Traceback" not in resp.text and "ExceededBudget" not in resp.text


@pytest.mark.parametrize(
    ("exc", "status", "fragment", "code"),
    [
        (_api_error(openai.BadRequestError, 400, "Budget has been exceeded! Current cost: 3"), 402, "budget", "budget_exceeded"),
        (_api_error(openai.RateLimitError, 429, "Rate limit reached for end user"), 429, "Trop de requêtes", "rate_limited"),
        (openai.APIConnectionError(request=httpx.Request("POST", GATEWAY)), 503, "injoignable", "unreachable"),
        (_api_error(openai.InternalServerError, 500, "upstream down"), 503, "indisponible", "unavailable"),
        (_api_error(openai.AuthenticationError, 401, "Invalid proxy key"), 502, "refusé", "rejected"),
    ],
)
def test_gateway_refusals_answer_clearly(gw, member, admin, indexed, llm, monkeypatch, exc, status, fragment, code):
    _failing_llm(monkeypatch, llm, exc)
    resp = member.post("/chat/ask", json={"question": QUESTION})
    assert resp.status_code == status
    assert fragment in resp.json()["detail"] and set(resp.json()) == {"detail"}
    assert resp.headers["x-contexthub-error-code"] == code
    events = admin.get("/admin/audit", params={"action": "query.ask"}).json()["items"]
    assert events[0]["outcome"] == "failure" and events[0]["details"]["reason"]


def test_without_gateway_llm_errors_keep_the_generic_answer(member, indexed, llm, monkeypatch):
    _failing_llm(monkeypatch, llm, _api_error(openai.RateLimitError, 429, "slow down"))
    resp = member.post("/chat/ask", json={"question": QUESTION})
    assert resp.status_code == 502
    assert resp.json()["detail"] == "Le moteur de réponse est indisponible. Réessayez dans un instant."
    events = _events(member.post("/chat/stream", json={"question": QUESTION}))
    assert events[-1] == {"type": "error", "message": "Le moteur de réponse est indisponible. Réessayez dans un instant."}


# ---------------------------------------------------------------------------
# LiteLLM consumption adapter
# ---------------------------------------------------------------------------

UID = str(uuid.uuid4())
OTHER = str(uuid.uuid4())


def _metrics(spend, prompt=0, completion=0, requests=0) -> dict:
    return {"spend": spend, "prompt_tokens": prompt, "completion_tokens": completion, "api_requests": requests}


def _activity_handler(seen: list, *, entities=True, subject: str | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["authorization"] == "Bearer sk-admin"
        params = request.url.params
        if request.url.path == "/customer/info":
            return httpx.Response(
                200,
                json={
                    "user_id": params["end_user_id"],
                    "spend": 0.75,
                    "litellm_budget_table": {"max_budget": 2.0, "budget_duration": "30d"},
                },
            )
        assert request.url.path == "/customer/daily/activity"
        who = params.get("end_user_ids")
        page = int(params["page"])
        ents = {who or subject or UID: {"metrics": _metrics(0.5, 100, 20, 2)}}
        if not who:
            ents[OTHER] = {"metrics": _metrics(0.25, 50, 10, 1)}
        # The same day on two pages: the adapter must sum it.
        row = {
            "date": "2026-11-03",
            "metrics": _metrics(0.75 if not who else 0.5, 150 if not who else 100, 30 if not who else 20, 3 if not who else 2),
            "breakdown": {"models": {"gpt-4o": {"metrics": _metrics(0.5, 100, 20, 2)}}, **({"entities": ents} if entities else {})},
        }
        return httpx.Response(200, json={"results": [row], "metadata": {"page": page, "total_pages": 2, "has_more": page < 2}})

    return handler


async def test_litellm_user_usage(gw):
    seen: list[httpx.Request] = []
    provider = LiteLLMUsageProvider("sk-admin", transport=httpx.MockTransport(_activity_handler(seen)))
    report = await provider.user_usage(UID, date(2026, 11, 1), date(2026, 11, 14))
    activity = [r for r in seen if r.url.path == "/customer/daily/activity"]
    assert [r.url.params["page"] for r in activity] == ["1", "2"]
    assert all(r.url.params["end_user_ids"] == UID for r in activity)
    assert activity[0].url.params["start_date"] == "2026-11-01" and activity[0].url.params["end_date"] == "2026-11-14"
    assert report.totals.as_dict() == {"cost": 1.0, "input_tokens": 200, "output_tokens": 40, "requests": 4}
    assert [d.as_dict()["date"] for d in report.daily] == ["2026-11-03"]
    assert report.by_model[0].model == "gpt-4o" and report.by_model[0].totals.requests == 4
    assert report.budget.as_dict() == {
        "limit": 2.0,
        "spent": 0.75,
        "remaining": 1.25,
        "period": "30d",
        "period_kind": "month",
        "resets_at": None,
        "managed": False,  # not linked to ContextHub's own budget id
    }
    assert report.missing == []


async def test_litellm_refuses_figures_of_other_users(gw):
    def handler(request):
        row = {"date": "2026-11-03", "metrics": _metrics(1), "breakdown": {"entities": {OTHER: {"metrics": _metrics(1)}}}}
        return httpx.Response(200, json={"results": [row], "metadata": {"total_pages": 1}})

    provider = LiteLLMUsageProvider("sk-admin", transport=httpx.MockTransport(handler))
    with pytest.raises(UsageError, match="incohérente"):
        await provider.user_usage(UID, date(2026, 11, 1), date(2026, 11, 14))


async def test_litellm_unknown_customer_has_no_budget(gw):
    def handler(request):
        if request.url.path == "/customer/info":
            return httpx.Response(400, json={"error": "End User Id=... does not exist in db"})
        return httpx.Response(200, json={"results": [], "metadata": {"total_pages": 1}})

    provider = LiteLLMUsageProvider("sk-admin", transport=httpx.MockTransport(handler))
    report = await provider.user_usage(UID, date(2026, 11, 1), date(2026, 11, 14))
    assert report.budget is None and report.missing == ["budget"] and report.totals.requests == 0


@pytest.mark.parametrize(
    ("response", "fragment"),
    [
        (httpx.Response(401, json={"error": "Unauthorized"}), "refuse la clé"),
        (httpx.Response(503, text="<html>bad gateway</html>"), "indisponible \\(erreur 503\\)"),
        (httpx.Response(302, headers={"location": "https://evil.test/"}), "redirection"),
        (httpx.Response(200, text="not json"), "inattendue"),
    ],
)
async def test_litellm_errors_become_usage_errors(gw, response, fragment):
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return response

    provider = LiteLLMUsageProvider("sk-admin", transport=httpx.MockTransport(handler))
    with pytest.raises(UsageError, match=fragment) as info:
        await provider.global_usage(date(2026, 11, 1), date(2026, 11, 14))
    assert "sk-admin" not in str(info.value) and "html" not in str(info.value)
    assert set(seen) == {"gateway.test"}  # redirects are not followed


async def test_litellm_timeout(gw):
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    provider = LiteLLMUsageProvider("sk-admin", transport=httpx.MockTransport(handler))
    with pytest.raises(UsageError, match="délai dépassé"):
        await provider.user_usage(UID, date(2026, 11, 1), date(2026, 11, 14))


async def test_litellm_global_usage_without_entity_breakdown(gw):
    seen: list = []
    provider = LiteLLMUsageProvider("sk-admin", transport=httpx.MockTransport(_activity_handler(seen, entities=False)))
    report = await provider.global_usage(date(2026, 11, 1), date(2026, 11, 14))
    assert report.by_user == [] and "by_user" in report.missing
    assert all("end_user_ids" not in r.url.params for r in seen)


def test_registry_builds_litellm_only_with_a_gateway(gw, monkeypatch):
    assert isinstance(registry.build_usage_provider(), LiteLLMUsageProvider)
    monkeypatch.setattr(gw, "llm_gateway_admin_key", None)  # the application key is never used for it
    assert registry.build_usage_provider() is None
    assert registry.usage_not_configured() == registry.NO_ADMIN_KEY
    monkeypatch.setattr(gw, "llm_gateway_url", None)
    assert registry.build_usage_provider() is None


# ---------------------------------------------------------------------------
# /usage routes
# ---------------------------------------------------------------------------


def test_usage_routes_without_gateway(member, admin):
    for client, path in ((member, "/usage/me"), (admin, "/admin/usage")):
        resp = client.get(path)
        assert resp.status_code == 200
        assert resp.json()["available"] is False and resp.json()["reason"]


def test_usage_me_only_shows_the_current_user(gw, member, member_user, admin_user, anon):
    seen: list[httpx.Request] = []
    gateway.set_gateway_transport(httpx.MockTransport(_activity_handler(seen)))
    resp = member.get(
        "/usage/me",
        params={"from": "2026-11-01", "to": "2026-11-14", "user_id": str(admin_user.id), "end_user_ids": OTHER},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {r.url.params.get("end_user_ids") or r.url.params.get("end_user_id") for r in seen} == {str(member_user.id)}
    assert body["available"] is True and body["gateway"] == "litellm"
    assert body["period"] == {"from": "2026-11-01", "to": "2026-11-14"}
    assert body["totals"]["cost"] == 1.0
    assert body["budget"]["remaining"] == 1.25
    assert "by_user" not in body
    assert anon.get("/usage/me").status_code == 401


def test_usage_period_is_validated(gw, member):
    assert member.get("/usage/me", params={"from": "2026-11-10", "to": "2026-11-01"}).status_code == 422
    assert member.get("/usage/me", params={"from": "2024-01-01", "to": "2026-11-01"}).status_code == 422
    assert member.get("/usage/me", params={"from": "pas-une-date"}).status_code == 422
    assert member.get("/usage/me", params={"to": "2999-01-01"}).status_code == 422


def test_usage_me_defaults_to_the_current_month(gw, member):
    seen: list[httpx.Request] = []
    gateway.set_gateway_transport(httpx.MockTransport(_activity_handler(seen)))
    body = member.get("/usage/me").json()
    assert body["period"]["from"].endswith("-01")
    assert body["period"]["from"][:7] == body["period"]["to"][:7]


def test_usage_gateway_error_is_503(gw, member):
    gateway.set_gateway_transport(httpx.MockTransport(lambda request: httpx.Response(500)))
    resp = member.get("/usage/me")
    assert resp.status_code == 503
    assert "indisponible" in resp.json()["detail"]


def test_admin_usage_is_admin_only_and_names_users(gw, admin, member, member_user):
    seen: list[httpx.Request] = []
    # The gateway knows the member, and an id ContextHub does not.
    gateway.set_gateway_transport(httpx.MockTransport(_activity_handler(seen, subject=str(member_user.id))))
    assert member.get("/admin/usage").status_code == 403
    assert seen == []

    resp = admin.get("/admin/usage", params={"from": "2026-11-01", "to": "2026-11-14"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["totals"] == {"cost": 1.5, "input_tokens": 300, "output_tokens": 60, "requests": 6}
    known, unknown = body["by_user"]
    assert known["known"] is True and known["email"] == member_user.email and known["user_id"] == str(member_user.id)
    assert known["cost"] == 1.0
    assert unknown == {
        "user_id": OTHER,
        "known": False,
        "email": None,
        "full_name": None,
        "label": "Utilisateur inconnu ou supprimé",
        "cost": 0.5,
        "input_tokens": 100,
        "output_tokens": 20,
        "requests": 2,
        "budget": None,  # only read for ContextHub accounts
    }
    assert body["by_model"][0]["model"] == "gpt-4o"
    assert body["budget"] is None and body["missing"] == []  # no global budget, not "missing"


# ---------------------------------------------------------------------------
# Review follow-ups: strict attribution, no blocking call on the loop, system check
# ---------------------------------------------------------------------------


async def test_litellm_refuses_spend_without_entity_breakdown(gw):
    def handler(request):
        row = {"date": "2026-11-03", "metrics": _metrics(0.4, 10, 5, 1), "breakdown": {"models": {}}}
        return httpx.Response(200, json={"results": [row], "metadata": {"total_pages": 1}})

    provider = LiteLLMUsageProvider("sk-admin", transport=httpx.MockTransport(handler))
    with pytest.raises(UsageError, match="non affichée"):
        await provider.user_usage(UID, date(2026, 11, 1), date(2026, 11, 14))


async def test_litellm_accepts_empty_entities_without_spend(gw):
    def handler(request):
        if request.url.path == "/customer/info":
            return httpx.Response(400, json={"error": "unknown"})
        row = {"date": "2026-11-03", "metrics": _metrics(0), "breakdown": {"models": {}, "entities": {}}}
        return httpx.Response(200, json={"results": [row], "metadata": {"total_pages": 1}})

    provider = LiteLLMUsageProvider("sk-admin", transport=httpx.MockTransport(handler))
    report = await provider.user_usage(UID, date(2026, 11, 1), date(2026, 11, 14))
    assert report.totals.as_dict() == {"cost": 0.0, "input_tokens": 0, "output_tokens": 0, "requests": 0}


async def test_litellm_admin_only_key_has_a_precise_message(gw):
    def handler(request):
        return httpx.Response(401, json={"error": {"message": "Admin-only endpoint. Not allowed to access this."}})

    provider = LiteLLMUsageProvider("sk-app", transport=httpx.MockTransport(handler))
    with pytest.raises(UsageError, match="LLM_GATEWAY_ADMIN_KEY doit être une clé d'administrateur") as info:
        await provider.global_usage(date(2026, 11, 1), date(2026, 11, 14))
    assert "Not allowed" not in str(info.value)


def test_no_blocking_gateway_call_on_the_event_loop(gw, member, indexed):
    """Cold catalogue cache: /chat/ask and /chat/stream must look it up off the loop."""
    import asyncio

    on_loop: list[str] = []
    calls: list[str] = []
    answer = _models_handler()

    def handler(request):
        calls.append(request.url.path)
        try:
            asyncio.get_running_loop()
            on_loop.append(request.url.path)
        except RuntimeError:
            pass  # worker thread: fine
        return answer(request)

    for path in ("/chat/ask", "/chat/stream"):
        gateway.set_gateway_transport(httpx.MockTransport(handler))  # also empties the cache
        assert member.post(path, json={"question": QUESTION}).status_code == 200
    assert calls and on_loop == []


def test_admin_system_reports_the_gateway(gw, admin, monkeypatch):
    checks = admin.get("/admin/system").json()["checks"]
    assert checks["gateway"] == {"ok": True, "models": ["claude-sonnet", "gpt-4o"], "consumption": True, "notes": []}

    gateway.set_gateway_transport(httpx.MockTransport(lambda request: httpx.Response(401)))
    check = admin.get("/admin/system").json()["checks"]["gateway"]
    assert check["ok"] is False and check["error"] == "HTTP 401"

    monkeypatch.setattr(gw, "llm_gateway_url", None)
    assert "gateway" not in admin.get("/admin/system").json()["checks"]


# ---------------------------------------------------------------------------
# Second review: admin key without fallback, LLM_MODELS, error-code header
# ---------------------------------------------------------------------------


def test_without_admin_key_consumption_is_not_configured(gw, member, admin, monkeypatch):
    seen: list = []
    gateway.set_gateway_transport(httpx.MockTransport(_activity_handler(seen)))
    monkeypatch.setattr(gw, "llm_gateway_admin_key", None)
    assert member.get("/usage/me").json() == {
        "available": False,
        "reason": "Le suivi de la consommation n'est pas encore configuré. Contactez votre administrateur.",
    }
    body = admin.get("/admin/usage").json()
    assert body["available"] is False and "LLM_GATEWAY_ADMIN_KEY" in body["reason"]
    assert seen == []  # the application key never reads consumption


def test_admin_system_flags_missing_admin_key_and_ignored_models(gw, admin, monkeypatch):
    monkeypatch.setattr(gw, "llm_gateway_admin_key", None)
    monkeypatch.setattr(gw, "llm_models", "ollama:fake-model")
    check = admin.get("/admin/system").json()["checks"]["gateway"]
    assert check["ok"] is True and check["consumption"] is False  # chat still works
    assert len(check["notes"]) == 2
    assert "LLM_GATEWAY_ADMIN_KEY" in check["notes"][0] and "ollama:fake-model" in check["notes"][1]
    assert "workers" not in check  # the display shim of lot 1 is gone: the page reads `notes`


def test_production_does_not_require_the_admin_key():
    Settings(
        environment="production",
        secret_key="x" * 40,
        postgres_password="s3cret",
        llm_gateway_url=GATEWAY,
        llm_gateway_api_key="k",
        _env_file=None,
    )


def test_llm_models_without_gateway_entries_shows_everything_served(gw, monkeypatch):
    from src.llm import factory

    warnings: list = []
    monkeypatch.setattr(factory, "_warned_ignored", ())
    monkeypatch.setattr(factory.log, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(gw, "llm_models", "ollama:fake-model,anthropic:claude-sonnet-5-5")
    for _ in range(3):
        assert [m.id for m in model_catalogue()] == ["gateway:claude-sonnet", "gateway:gpt-4o"]
    assert len(warnings) == 1  # logged once, not on every request
    assert factory.ignored_model_entries() == ["ollama:fake-model", "anthropic:claude-sonnet-5-5"]
    with pytest.raises(ModelNotAllowed):
        resolve_model("ollama:fake-model")


def test_contexthub_rate_limit_has_no_gateway_error_code(gw, member, indexed, monkeypatch):
    monkeypatch.setattr(gw, "ask_rate_limit_per_minute", 1)
    assert member.post("/chat/ask", json={"question": QUESTION}).status_code == 200
    resp = member.post("/chat/ask", json={"question": QUESTION})
    assert resp.status_code == 429 and "x-contexthub-error-code" not in resp.headers


def test_error_code_header_is_exposed_to_the_browser(gw, member, indexed, llm, monkeypatch):
    _failing_llm(monkeypatch, llm, _api_error(openai.RateLimitError, 429, "Rate limit reached"))
    origin = get_settings().allowed_origins[0]
    resp = member.post("/chat/ask", json={"question": QUESTION}, headers={"Origin": origin})
    assert resp.headers["x-contexthub-error-code"] == "rate_limited"
    assert "x-contexthub-error-code" in resp.headers["access-control-expose-headers"].lower()
