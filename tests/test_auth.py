from src.auth.rate_limit import limiter
from src.config import get_settings
from tests.conftest import PASSWORD, login


def test_business_routes_require_authentication(anon):
    for method, path in [
        ("get", "/collections"),
        ("get", "/conversations"),
        ("get", "/models"),
        ("post", "/chat/ask"),
        ("get", "/admin/users"),
        ("get", "/admin/audit"),
    ]:
        resp = getattr(anon, method)(path, **({"json": {"question": "x"}} if method == "post" else {}))
        assert resp.status_code == 401, (path, resp.status_code)


def test_public_routes(anon):
    assert anon.get("/health").json()["status"] == "ok"
    assert anon.get("/auth/config").json()["local_enabled"] is True


def test_login_sets_httponly_cookie_and_me(anon, member_user):
    resp = login(anon, member_user.email)
    assert resp.status_code == 200
    cookie = resp.headers["set-cookie"]
    assert "ch_session=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    me = anon.get("/auth/me").json()
    assert me["email"] == member_user.email
    assert me["role"] == "member"
    assert "password_hash" not in me


def test_login_is_case_insensitive_on_email(anon, member_user):
    assert login(anon, member_user.email.upper()).status_code == 200


def test_bad_password_is_rejected_and_audited(anon, admin, member_user):
    assert login(anon, member_user.email, "wrong-password").status_code == 401
    events = admin.get("/admin/audit", params={"action": "auth.login"}).json()["items"]
    assert any(e["outcome"] == "failure" and e["details"]["reason"] == "bad_credentials" for e in events)


def test_login_rate_limit(anon, member_user):
    limiter.clear_memory()
    attempts = get_settings().login_max_attempts
    for _ in range(attempts):
        assert login(anon, member_user.email, "nope").status_code == 401
    assert login(anon, member_user.email, PASSWORD).status_code == 429


def test_state_changing_cookie_request_requires_csrf_header(member):
    resp = member.post("/auth/api-keys", json={"name": "x"}, headers={"X-Requested-With": ""})
    assert resp.status_code == 403
    assert member.post("/auth/api-keys", json={"name": "x"}).status_code == 201


def test_logout_clears_cookie(member):
    assert member.post("/auth/logout").status_code == 200
    assert member.get("/auth/me").status_code == 401


def test_deactivation_revokes_sessions_immediately(admin, member, member_user):
    assert member.get("/auth/me").status_code == 200
    assert admin.patch(f"/admin/users/{member_user.id}", json={"is_active": False}).status_code == 200
    assert member.get("/auth/me").status_code == 401


def test_password_change_enforces_policy_and_revokes_other_sessions(member, member_user, anon):
    login(anon, member_user.email)  # second session
    weak = member.post("/auth/password", json={"current_password": PASSWORD, "new_password": "short"})
    assert weak.status_code == 422
    ok = member.post("/auth/password", json={"current_password": PASSWORD, "new_password": "Une-Phrase-Longue-2026"})
    assert ok.status_code == 200
    assert member.get("/auth/me").status_code == 200  # this session got a fresh cookie
    assert anon.get("/auth/me").status_code == 401  # the other one is revoked


def test_api_key_lifecycle(member, anon):
    created = member.post("/auth/api-keys", json={"name": "SIEM"}).json()
    key = created["key"]
    assert key.startswith("chk_")
    headers = {"Authorization": f"Bearer {key}"}
    assert anon.get("/auth/me", headers=headers).status_code == 200
    # API keys are not subject to the CSRF header (no ambient credentials).
    assert anon.post("/auth/api-keys", json={"name": "y"}, headers={**headers, "X-Requested-With": ""}).status_code == 201
    listed = member.get("/auth/api-keys").json()
    assert all("key" not in k for k in listed)
    assert member.delete(f"/auth/api-keys/{created['id']}").status_code == 200
    assert anon.get("/auth/me", headers=headers).status_code == 401


def test_oidc_disabled_by_default(anon):
    assert anon.get("/auth/oidc/login", follow_redirects=False).status_code == 404
