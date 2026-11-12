"""SSO (OpenID Connect) login flow and collections opened to identity-provider groups."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from src.auth.oidc import OIDCError, OIDCIdentity, oidc_client
from src.config import reset_settings_cache
from tests.conftest import _client, upload
from tests.fakes import make_pdf

PDF = make_pdf([["Article 1", "Conges", "Chaque salarie acquiert trente jours ouvrables de conges payes par an."]])


@pytest.fixture
def sso(monkeypatch):
    """OIDC enabled against a fake identity provider; ``sso.login(...)`` signs a user in."""
    monkeypatch.setenv("OIDC_ISSUER", "https://idp.acme.test/realms/acme")
    monkeypatch.setenv("OIDC_CLIENT_ID", "contexthub")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "secret")
    monkeypatch.setenv("OIDC_ADMIN_GROUPS", "contexthub-admins")
    reset_settings_cache()
    monkeypatch.setattr(
        oidc_client, "discovery", lambda: {"authorization_endpoint": "https://idp.acme.test/auth", "issuer": "x"}
    )

    class Flow:
        @staticmethod
        def login(email: str, groups: list[str], subject: str | None = None, name: str | None = None):
            client = _client()
            start = client.get("/auth/oidc/login", follow_redirects=False)
            assert start.status_code in (302, 303, 307), start.text
            query = parse_qs(urlparse(start.headers["location"]).query)
            assert query["code_challenge_method"] == ["S256"] and query["redirect_uri"][0].endswith("/api/auth/oidc/callback")
            identity = OIDCIdentity(subject=subject or f"sub-{email}", email=email, name=name, groups=groups)
            monkeypatch.setattr(oidc_client, "exchange", lambda code, flow: identity)
            done = client.get(
                "/auth/oidc/callback", params={"code": "abc", "state": query["state"][0]}, follow_redirects=False
            )
            assert done.status_code in (302, 303, 307), done.text
            return client

    yield Flow
    reset_settings_cache()


def _group_grant(admin, collection_id: str, group: str, level: str = "viewer"):
    return admin.put(f"/admin/collections/{collection_id}/group-grants", json={"group": group, "level": level})


def test_sso_login_creates_the_account_and_keeps_its_groups(sso, admin):
    alice = sso.login("Alice@ACME.test", ["rh", "tous"], name="Alice Martin")
    me = alice.get("/auth/me").json()
    assert me["email"] == "alice@acme.test" and me["auth_provider"] == "oidc" and me["role"] == "member"
    assert me["full_name"] == "Alice Martin" and me["sso_groups"] == ["rh", "tous"]
    assert admin.get("/admin/sso-groups").json() == ["rh", "tous"]
    event = admin.get("/admin/audit", params={"action": "auth.login"}).json()["items"][0]
    assert event["details"]["method"] == "oidc" and event["actor_email"] == "alice@acme.test"


def test_group_grant_opens_the_collection_to_group_members_only(sso, admin, collection):
    assert upload(admin, collection["id"], "conges.pdf", PDF).status_code == 202
    assert _group_grant(admin, collection["id"], "rh").status_code == 200

    alice = sso.login("alice@acme.test", ["rh"])
    chloe = sso.login("chloe@acme.test", ["juridique"])
    assert [c["name"] for c in alice.get("/auth/me").json()["collections"]] == [collection["name"]]
    assert alice.get("/auth/me").json()["collections"][0]["access"] == "viewer"
    assert chloe.get("/auth/me").json()["collections"] == []
    assert chloe.get(f"/collections/{collection['id']}/documents").status_code == 404

    answer = alice.post("/chat/ask", json={"question": "Combien de jours de conges payes par an ?"})
    assert answer.status_code == 200 and answer.json()["refused"] is False
    # Read access through a group does not allow importing.
    assert upload(alice, collection["id"], "autre.pdf", PDF).status_code == 403


def test_leaving_the_group_removes_the_access_at_next_login(sso, admin, collection):
    _group_grant(admin, collection["id"], "rh")
    alice = sso.login("alice@acme.test", ["rh"])
    assert len(alice.get("/collections").json()) == 1
    alice = sso.login("alice@acme.test", [])
    assert alice.get("/collections").json() == []
    assert alice.get("/auth/me").json()["sso_groups"] == []


def test_best_of_personal_and_group_grant_wins(sso, admin, collection):
    _group_grant(admin, collection["id"], "rh", "editor")
    alice = sso.login("alice@acme.test", ["rh", "tous"])
    user_id = alice.get("/auth/me").json()["id"]
    admin.put(f"/admin/collections/{collection['id']}/grants", json={"user_id": user_id, "level": "viewer"})
    assert alice.get("/collections").json()[0]["access"] == "editor"
    assert upload(alice, collection["id"], "conges.pdf", PDF).status_code == 202

    _group_grant(admin, collection["id"], "tous", "viewer")  # a second, weaker group changes nothing
    assert alice.get("/collections").json()[0]["access"] == "editor"
    assert admin.delete(f"/admin/collections/{collection['id']}/group-grants", params={"group": "rh"}).status_code == 200
    assert alice.get("/collections").json()[0]["access"] == "viewer"


def test_admin_group_gives_and_removes_the_admin_role(sso, admin):
    bob = sso.login("bob@acme.test", ["contexthub-admins"])
    assert bob.get("/auth/me").json()["role"] == "admin"
    assert bob.get("/admin/users").status_code == 200
    bob = sso.login("bob@acme.test", ["rh"])
    assert bob.get("/auth/me").json()["role"] == "member"
    assert bob.get("/admin/users").status_code == 403


def test_group_grants_are_admin_only_validated_and_audited(sso, admin, member, collection):
    assert _group_grant(member, collection["id"], "rh").status_code == 403
    assert member.get(f"/admin/collections/{collection['id']}/group-grants").status_code == 403
    assert _group_grant(admin, collection["id"], "rh", "owner").status_code == 422
    assert _group_grant(admin, collection["id"], "   ").status_code == 422
    assert _group_grant(admin, collection["id"], " rh ", "editor").status_code == 200
    assert _group_grant(admin, collection["id"], "rh", "viewer").status_code == 200  # update, not duplicate
    assert admin.get(f"/admin/collections/{collection['id']}/group-grants").json() == [{"group": "rh", "level": "viewer"}]
    assert admin.delete(f"/admin/collections/{collection['id']}/group-grants", params={"group": "x"}).status_code == 404
    events = admin.get("/admin/audit", params={"action": "grant.group_set"}).json()["items"]
    assert events[0]["details"] == {"group": "rh", "level": "viewer"}
    assert admin.get("/admin/audit/verify").json()["valid"] is True


def test_password_accounts_have_no_groups(member, admin, collection):
    _group_grant(admin, collection["id"], "rh")
    assert member.get("/auth/me").json()["sso_groups"] == []
    assert member.get("/collections").json() == []


def test_failed_sso_login_is_refused_and_audited(sso, admin, monkeypatch):
    client = _client()
    start = client.get("/auth/oidc/login", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    # A callback with another state is refused before the provider is called.
    wrong = client.get("/auth/oidc/callback", params={"code": "abc", "state": "forged"}, follow_redirects=False)
    assert "ch_session" not in wrong.headers.get("set-cookie", "")

    def refuse(code, flow):
        raise OIDCError("Domaine de messagerie non autorisé.")

    monkeypatch.setattr(oidc_client, "exchange", refuse)
    failed = client.get("/auth/oidc/callback", params={"code": "abc", "state": state}, follow_redirects=False)
    assert "ch_session" not in failed.headers.get("set-cookie", "")
    assert client.get("/auth/me").status_code == 401
    reasons = [e["details"].get("reason") for e in admin.get("/admin/audit", params={"action": "auth.login", "outcome": "failure"}).json()["items"]]
    assert "invalid_state" in reasons and any("Domaine" in (r or "") for r in reasons)
