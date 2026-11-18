"""Offered models set from the administration (ADR-002): hub_settings, priority over the
environment, validation, enforcement on the chat routes, audit, gateway catalogue."""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import inspect, text

from src import instance_settings
from src.audit import service as audit
from src.config import get_settings
from src.db.models import InstanceSetting
from src.db.session import SessionLocal, get_engine
from src.llm import gateway
from src.llm.factory import ModelNotAllowed, model_catalogue, resolve_model
from tests.conftest import grant, upload
from tests.fakes import make_pdf
from tests.test_gateway import GATEWAY, QUESTION, _models_handler, gw  # noqa: F401  (fixture)

PDF = make_pdf([["Article 93", "Transposition", "Les Etats membres appliquent ces dispositions a partir du 3 janvier 2017."]])
OLLAMA = "ollama:fake-model"
ANTHROPIC = "anthropic:claude-sonnet-5-5"


@pytest.fixture
def indexed(admin, member_user, collection):
    assert upload(admin, collection["id"], "directive.pdf", PDF).status_code == 202
    grant(admin, collection["id"], member_user.id, "viewer")
    return collection


def _events(resp) -> list[dict]:
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


def _put(client, offered, default):
    return client.put("/admin/models", json={"offered": offered, "default": default})


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def test_settings_table_and_migration():
    columns = {c["name"] for c in inspect(get_engine()).get_columns("hub_settings")}
    assert columns == {"key", "value", "updated_at", "updated_by"}

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_current_head() == "69ab4256"
    assert script.get_revision("69ab4256").down_revision == "69ab4255"


def test_setting_is_cached_then_expires(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(instance_settings.time, "monotonic", lambda: clock[0])
    instance_settings.put("demo", {"v": 1}, None)
    assert instance_settings.get("demo") == {"v": 1}
    # Another API worker writes: this process keeps its cached value for a few seconds...
    db = SessionLocal()
    db.get(InstanceSetting, "demo").value = {"v": 2}
    db.commit()
    db.close()
    assert instance_settings.get("demo") == {"v": 1}
    clock[0] += instance_settings._TTL_SECONDS + 0.1
    assert instance_settings.get("demo") == {"v": 2}  # ...then reads it again by itself
    instance_settings.delete("demo")
    assert instance_settings.get("demo") is None  # a local write invalidates at once


def test_unreadable_settings_table_falls_back_on_the_environment(member):
    with get_engine().begin() as conn:
        conn.execute(text("DROP TABLE hub_settings"))  # e.g. migration 69ab4256 not applied
    instance_settings.reset_cache()
    body = member.get("/models").json()
    assert [m["id"] for m in body["models"]] == [OLLAMA, ANTHROPIC]
    with get_engine().begin() as conn:  # teardown drops every table
        InstanceSetting.__table__.create(conn)


# ---------------------------------------------------------------------------
# Priority, order, default
# ---------------------------------------------------------------------------


def test_setting_overrides_the_environment_and_reset_restores_it(admin, member):
    view = admin.get("/admin/models").json()
    assert view["source"] == "environment" and view["gateway"] is False and "console_url" not in view
    assert [(m["id"], m["offered"], m["is_default"], m["available"]) for m in view["models"]] == [
        (OLLAMA, True, True, True),
        (ANTHROPIC, True, False, True),
    ]

    resp = _put(admin, [ANTHROPIC, OLLAMA], OLLAMA)
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "settings"
    body = member.get("/models").json()
    assert [m["id"] for m in body["models"]] == [ANTHROPIC, OLLAMA]  # the administrator's order
    assert body["default"] == OLLAMA
    assert resolve_model(None).id == OLLAMA
    assert [m.id for m in model_catalogue()] == [OLLAMA, ANTHROPIC]  # default first

    assert _put(admin, [ANTHROPIC], ANTHROPIC).status_code == 200
    view = admin.get("/admin/models").json()
    assert [(m["id"], m["offered"], m["is_default"]) for m in view["models"]] == [
        (ANTHROPIC, True, True),
        (OLLAMA, False, False),  # available, not offered: the administrator may check it
    ]
    assert [m["id"] for m in member.get("/models").json()["models"]] == [ANTHROPIC]

    resp = admin.delete("/admin/models")
    assert resp.status_code == 200 and resp.json()["source"] == "environment"
    assert [m["id"] for m in member.get("/models").json()["models"]] == [OLLAMA, ANTHROPIC]


@pytest.mark.parametrize(
    ("payload", "fragment"),
    [
        ({"offered": [], "default": OLLAMA}, "au moins un modèle"),
        ({"default": OLLAMA}, "au moins un modèle"),
        ({"offered": ["pas-un-identifiant"], "default": "pas-un-identifiant"}, "Identifiant de modèle invalide"),
        ({"offered": ["inconnu:modele"], "default": "inconnu:modele"}, "Fournisseur LLM inconnu"),
        ({"offered": ["ollama:   "], "default": OLLAMA}, "Identifiant de modèle invalide"),
        ({"offered": [OLLAMA, " Ollama:fake-model "], "default": OLLAMA}, "en double"),
        ({"offered": [OLLAMA, "openai:gpt-4o"], "default": OLLAMA}, "non autorisés par LLM_MODELS : openai:gpt-4o"),
        ({"offered": [OLLAMA]}, "modèle par défaut"),
        ({"offered": [OLLAMA], "default": ANTHROPIC}, "doit faire partie des modèles proposés"),
        ({"offered": [f"ollama:m{i}" for i in range(51)], "default": "ollama:m0"}, "plus de 50"),
    ],
)
def test_put_models_is_strictly_validated(admin, payload, fragment):
    resp = admin.put("/admin/models", json=payload)
    assert resp.status_code == 422
    assert fragment in resp.json()["detail"]
    assert admin.get("/admin/models").json()["source"] == "environment"  # nothing stored


def test_ids_are_normalised(admin):
    resp = _put(admin, [" OLLAMA:fake-model", ANTHROPIC], "ollama:fake-model ")
    assert resp.status_code == 200, resp.text
    assert resp.json()["default"] == OLLAMA
    assert instance_settings.get("models") == {"offered": [OLLAMA, ANTHROPIC], "default": OLLAMA}


# ---------------------------------------------------------------------------
# Enforcement on the chat routes
# ---------------------------------------------------------------------------


def test_withdrawn_model_is_refused_by_ask_and_stream(admin, member, indexed):
    first = member.post("/chat/ask", json={"question": QUESTION, "model": ANTHROPIC})
    assert first.status_code == 200, first.text
    conversation_id = first.json()["conversation_id"]

    assert _put(admin, [OLLAMA], OLLAMA).status_code == 200
    for path in ("/chat/ask", "/chat/stream"):
        for extra in ({}, {"conversation_id": conversation_id}):
            resp = member.post(path, json={"question": QUESTION, "model": ANTHROPIC, **extra})
            assert resp.status_code == 400, (path, extra)
            assert "n'est pas autorisé" in resp.json()["detail"]
    resp = member.post("/chat/ask", json={"question": QUESTION, "conversation_id": conversation_id})
    assert resp.status_code == 200 and resp.json()["model"] == OLLAMA  # default of the setting
    events = _events(member.post("/chat/stream", json={"question": QUESTION, "model": OLLAMA}))
    assert events[0] == {"type": "meta", "conversation_id": events[0]["conversation_id"], "model": OLLAMA}
    assert events[-1]["type"] == "done"


def test_offered_model_that_left_the_catalogue_is_listed_but_not_selectable(admin, member, indexed, monkeypatch):
    assert _put(admin, [ANTHROPIC, OLLAMA], ANTHROPIC).status_code == 200
    monkeypatch.setattr(get_settings(), "llm_models", OLLAMA)  # the environment no longer allows it
    instance_settings.reset_cache()

    body = member.get("/models").json()
    # Anthropic left the catalogue; Ollama is not running in the tests (unknown, not selectable in the UI).
    assert [(m["id"], m["availability"]) for m in body["models"]] == [(ANTHROPIC, "unavailable"), (OLLAMA, "unknown")]
    assert [m["available"] for m in body["models"]] == [False, False]
    assert body["default"] == OLLAMA  # the stored default is not selectable any more
    view = admin.get("/admin/models").json()
    assert [(m["id"], m["availability"], m["offered"]) for m in view["models"]] == [
        (ANTHROPIC, "unavailable", True),
        (OLLAMA, "available", True),
    ]
    resp = member.post("/chat/ask", json={"question": QUESTION, "model": ANTHROPIC})
    assert resp.status_code == 400
    assert member.post("/chat/ask", json={"question": QUESTION}).json()["model"] == OLLAMA

    monkeypatch.setattr(get_settings(), "llm_models", "ollama:other-model")
    resp = member.post("/chat/ask", json={"question": QUESTION})
    assert resp.status_code == 400 and "Aucun des modèles proposés" in resp.json()["detail"]
    with pytest.raises(ModelNotAllowed):
        resolve_model(None)


# ---------------------------------------------------------------------------
# Access control, CSRF, audit
# ---------------------------------------------------------------------------


def test_models_routes_are_admin_only(member, anon, admin_user):
    for method, kwargs in (("get", {}), ("put", {"json": {"offered": [OLLAMA], "default": OLLAMA}}), ("delete", {})):
        assert getattr(member, method)("/admin/models", **kwargs).status_code == 403
        assert getattr(anon, method)("/admin/models", **kwargs).status_code == 401
    assert instance_settings.get("models") is None


def test_put_models_needs_the_csrf_header(admin):
    resp = admin.put(
        "/admin/models", json={"offered": [OLLAMA], "default": OLLAMA}, headers={"X-Requested-With": ""}
    )
    assert resp.status_code == 403


def test_models_changes_are_audited_and_the_chain_holds(admin, admin_user, db):
    assert _put(admin, [OLLAMA, ANTHROPIC], ANTHROPIC).status_code == 200
    assert _put(admin, [OLLAMA], OLLAMA).status_code == 200
    assert admin.delete("/admin/models").status_code == 200
    items = admin.get("/admin/audit", params={"action": "settings.models"}).json()["items"]
    assert [e["action"] for e in items] == ["settings.models_reset", "settings.models_set", "settings.models_set"]
    reset, second, first = items
    assert first["details"] == {"old": None, "new": {"offered": [OLLAMA, ANTHROPIC], "default": ANTHROPIC}}
    assert second["details"]["old"] == first["details"]["new"]
    assert reset["details"] == {"old": {"offered": [OLLAMA], "default": OLLAMA}, "new": None}
    assert {e["actor_id"] for e in items} == {str(admin_user.id)}
    assert all(e["resource_type"] == "settings" and e["resource_id"] == "models" for e in items)
    assert audit.verify_chain(db).valid


# ---------------------------------------------------------------------------
# Gateway mode
# ---------------------------------------------------------------------------


def test_gateway_catalogue_is_what_the_gateway_serves(gw, admin, member):  # noqa: F811
    view = admin.get("/admin/models").json()
    assert view["gateway"] is True and view["catalogue_available"] is True
    assert view["console_url"] == f"{GATEWAY}/ui"
    assert [(m["id"], m["offered"], m["is_default"]) for m in view["models"]] == [
        ("gateway:claude-sonnet", True, True),
        ("gateway:gpt-4o", True, False),
    ]
    resp = _put(admin, ["gateway:mistral-large"], "gateway:mistral-large")
    assert resp.status_code == 422 and "servis par la passerelle" in resp.json()["detail"]
    resp = _put(admin, [OLLAMA], OLLAMA)  # another provider cannot be offered in gateway mode
    assert resp.status_code == 422

    assert _put(admin, ["gateway:gpt-4o"], "gateway:gpt-4o").status_code == 200
    body = member.get("/models").json()
    assert body == {"models": [{**body["models"][0], "id": "gateway:gpt-4o", "available": True}], "default": "gateway:gpt-4o"}
    resp = member.post("/chat/ask", json={"question": QUESTION, "model": "gateway:claude-sonnet"})
    assert resp.status_code == 400


@pytest.mark.parametrize(
    ("url", "console"),
    [
        ("http://127.0.0.1:4000", "http://127.0.0.1:4000/ui"),  # development: kept
        ("http://localhost:4000/v1", "http://localhost:4000/ui"),
        ("https://llm.acme.fr", "https://llm.acme.fr/ui"),
        ("http://litellm:4000", None),  # Docker Compose service name
        ("http://host.docker.internal:4000", None),
        ("http://litellm.ai.svc.cluster.local:4000", None),  # Kubernetes service
        ("http://0.0.0.0:4000", None),
    ],
)
def test_console_url_rule(gw, admin, monkeypatch, url, console):  # noqa: F811
    monkeypatch.setattr(gw, "llm_gateway_url", url)
    assert admin.get("/admin/models").json()["console_url"] == console


def test_console_url_is_only_given_when_safe(gw, admin, monkeypatch):  # noqa: F811
    monkeypatch.setattr(gw, "llm_gateway_url", "http://litellm:4000")  # Docker service name
    assert admin.get("/admin/models").json()["console_url"] is None
    monkeypatch.setattr(gw, "llm_gateway_console_url", "https://llm-console.acme.fr/ui")
    assert admin.get("/admin/models").json()["console_url"] == "https://llm-console.acme.fr/ui"
    from src.config import Settings

    with pytest.raises(ValueError, match="LLM_GATEWAY_CONSOLE_URL"):
        Settings(llm_gateway_console_url="https://user:pw@console.test/", _env_file=None)


def test_unreachable_gateway_blocks_changes_but_not_reading(gw, admin, member):  # noqa: F811
    assert _put(admin, ["gateway:gpt-4o"], "gateway:gpt-4o").status_code == 200

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    gateway.set_gateway_transport(httpx.MockTransport(down))
    view = admin.get("/admin/models").json()
    assert view["catalogue_available"] is False
    assert [(m["id"], m["available"], m["availability"], m["offered"]) for m in view["models"]] == [
        ("gateway:gpt-4o", False, "unknown", True)
    ]
    resp = _put(admin, ["gateway:claude-sonnet"], "gateway:claude-sonnet")
    assert resp.status_code == 503 and "injoignable" in resp.json()["detail"]
    # Unknown availability: still selectable, the call will fail with the gateway's message.
    assert [m.id for m in model_catalogue()] == ["gateway:gpt-4o"]
    chat = member.get("/models").json()["models"][0]
    assert (chat["available"], chat["availability"]) == (False, "unknown")  # same answer as /admin/models


def test_no_blocking_call_on_the_event_loop_with_a_stored_setting(gw, admin, member, indexed, monkeypatch):  # noqa: F811
    import asyncio

    assert _put(admin, ["gateway:gpt-4o", "gateway:claude-sonnet"], "gateway:gpt-4o").status_code == 200
    on_loop: list[str] = []
    reads: list[str] = []
    real_get = instance_settings.get

    def spy(key):
        reads.append(key)
        try:
            asyncio.get_running_loop()
            on_loop.append(f"settings:{key}")
        except RuntimeError:
            pass
        return real_get(key)

    def handler(request):
        try:
            asyncio.get_running_loop()
            on_loop.append(request.url.path)
        except RuntimeError:
            pass
        return _models_handler()(request)

    monkeypatch.setattr(instance_settings, "get", spy)
    for path in ("/chat/ask", "/chat/stream", "/models"):
        gateway.set_gateway_transport(httpx.MockTransport(handler))  # cold gateway cache
        instance_settings.reset_cache()  # cold settings cache
        method = member.get if path == "/models" else member.post
        kwargs = {} if path == "/models" else {"json": {"question": QUESTION}}
        assert method(path, **kwargs).status_code == 200
    assert reads and on_loop == []


# ---------------------------------------------------------------------------
# Missing table vs. transient database errors
# ---------------------------------------------------------------------------


def _logged(monkeypatch) -> dict[str, list]:
    seen: dict[str, list] = {"warning": [], "debug": []}
    monkeypatch.setattr(instance_settings.log, "warning", lambda *args: seen["warning"].append(args))
    monkeypatch.setattr(instance_settings.log, "debug", lambda *args: seen["debug"].append(args))
    return seen


def test_missing_table_is_logged_once_and_shown_in_admin_system(admin, monkeypatch):
    monkeypatch.setattr(instance_settings, "_missing_table_logged", False)
    seen = _logged(monkeypatch)
    assert "migration_missing" not in admin.get("/admin/system").json()["checks"]["database"]
    with get_engine().begin() as conn:
        conn.execute(text("DROP TABLE hub_settings"))
    for _ in range(4):
        instance_settings.reset_cache()
        assert instance_settings.get("models") is None
    assert len(seen["warning"]) == 1 and "alembic upgrade head" in seen["warning"][0][1]
    assert len(seen["debug"]) == 3

    database = admin.get("/admin/system").json()["checks"]["database"]
    assert database["ok"] is True and database["migration_missing"] is True
    assert "Migration manquante" in database["notes"][0]
    resp = _put(admin, [OLLAMA], OLLAMA)
    assert resp.status_code == 503 and "migration manquante" in resp.json()["detail"]

    with get_engine().begin() as conn:
        InstanceSetting.__table__.create(conn)
    assert instance_settings.probe() == {"ok": True}
    assert "migration_missing" not in admin.get("/admin/system").json()["checks"]["database"]


def test_transient_database_errors_are_not_muted(monkeypatch):
    from sqlalchemy.exc import OperationalError

    class DownSession:
        def get(self, *args):
            raise OperationalError("SELECT", {}, Exception("connection refused"))

        def rollback(self):
            pass

        def close(self):
            pass

    clock = [1000.0]
    monkeypatch.setattr(instance_settings.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(instance_settings, "SessionLocal", DownSession)
    monkeypatch.setattr(instance_settings, "_table_exists", lambda: None)  # cannot tell: database down
    monkeypatch.setattr(instance_settings, "_transient_logged_at", None)
    seen = _logged(monkeypatch)
    for _ in range(3):
        clock[0] += instance_settings._TTL_SECONDS + 1  # cache expired: read again
        assert instance_settings.get("models") is None
    assert len(seen["warning"]) == 1  # at most once a minute...
    clock[0] += instance_settings._TRANSIENT_LOG_SECONDS
    instance_settings.get("models")
    assert len(seen["warning"]) == 2  # ...but never silenced for good
    assert seen["debug"] == []


def test_production_bcrypt_cost_is_unchanged():
    from tests.conftest import PRODUCTION_BCRYPT_ROUNDS

    assert PRODUCTION_BCRYPT_ROUNDS >= 12  # the suite lowers it for speed only
