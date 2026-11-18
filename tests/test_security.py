"""Regression tests for the findings of the internal penetration test (docs/pentest-interne.md)."""

from __future__ import annotations

import bcrypt
import pytest
from fastapi.testclient import TestClient

from src.app import create_app
from src.audit.service import _csv_cell
from src.auth import security
from src.config import reset_settings_cache
from tests.conftest import PASSWORD, _client, login


def test_unknown_account_costs_the_same_bcrypt_work_as_a_wrong_password(monkeypatch):
    """PT-01: the response time must not tell which e-mail addresses have an account."""
    seen: list[bytes] = []
    real = bcrypt.checkpw

    def spy(password: bytes, hashed: bytes) -> bool:
        seen.append(hashed)
        return real(password, hashed)

    monkeypatch.setattr(security.bcrypt, "checkpw", spy)
    assert security.verify_password("whatever", None) is False
    assert len(seen) == 1
    # Same cost factor as real password hashes ("$2b$12$...").
    assert seen[0].split(b"$")[2] == str(security._BCRYPT_ROUNDS).encode()
    assert security.hash_password("Correct-Horse-42!").split("$")[2] == str(security._BCRYPT_ROUNDS)


def test_login_does_the_bcrypt_work_for_unknown_accounts_too(anon, member_user, monkeypatch):
    """PT-01, at the route: one password check per attempt, account or not."""
    calls: list[int] = []
    real = bcrypt.checkpw
    monkeypatch.setattr(security.bcrypt, "checkpw", lambda p, h: calls.append(1) or real(p, h))
    assert anon.post("/auth/login", json={"email": "personne@banque.fr", "password": "x" * 14}).status_code == 401
    assert len(calls) == 1
    assert anon.post("/auth/login", json={"email": member_user.email, "password": "x" * 14}).status_code == 401
    assert len(calls) == 2


def test_logout_revokes_a_copied_session_cookie(member, member_user):
    """PT-02: a cookie copied before sign-out must not work afterwards."""
    stolen = member.cookies.get("ch_session")
    assert member.post("/auth/logout").status_code == 200

    thief = _client()
    thief.cookies.set("ch_session", stolen)
    assert thief.get("/auth/me").status_code == 401

    # Replaying the dead cookie on /auth/logout must not sign the user out again.
    fresh = _client()
    assert login(fresh, member_user.email).status_code == 200
    assert thief.post("/auth/logout").status_code == 200
    assert fresh.get("/auth/me").status_code == 200


def test_password_spraying_from_one_address_is_throttled(anon, monkeypatch):
    """PT-03: one address trying one password on many accounts."""
    monkeypatch.setenv("LOGIN_IP_MAX_ATTEMPTS", "5")
    reset_settings_cache()
    try:
        codes = [
            anon.post("/auth/login", json={"email": f"user{i}@banque.fr", "password": "Winter-2026-Password"}).status_code
            for i in range(7)
        ]
        assert codes == [401] * 5 + [429] * 2
    finally:
        monkeypatch.delenv("LOGIN_IP_MAX_ATTEMPTS")
        reset_settings_cache()


def test_account_lock_holds_even_for_the_right_password(anon, member_user, monkeypatch):
    """PT-03: guesses on one account are counted whatever the address; once the limit is
    reached, even the right password waits for the window to end."""
    monkeypatch.setenv("LOGIN_ACCOUNT_MAX_ATTEMPTS", "3")
    reset_settings_cache()
    try:
        for i in range(3):
            assert anon.post("/auth/login", json={"email": member_user.email, "password": f"guess-{i}-xxxxxxx"}).status_code == 401
        assert anon.post("/auth/login", json={"email": member_user.email, "password": PASSWORD}).status_code == 429
    finally:
        monkeypatch.delenv("LOGIN_ACCOUNT_MAX_ATTEMPTS")
        reset_settings_cache()


@pytest.mark.parametrize("value", ["=HYPERLINK(\"http://evil\")", "+1+1", "-2+3", "@SUM(A1)", "\tcmd", "\rcmd"])
def test_csv_cells_cannot_start_a_formula(value):
    """PT-04."""
    assert _csv_cell(value) == "'" + value
    assert _csv_cell("question normale") == "question normale"
    assert _csv_cell(None) == "" and _csv_cell(42) == "42"


def test_audit_export_neutralises_formulas_typed_by_users(admin, collection):
    payload = '=HYPERLINK("http://evil.example","clic")'
    assert admin.put(
        f"/admin/collections/{collection['id']}/group-grants", json={"group": payload, "level": "viewer"}
    ).status_code == 200
    export = admin.get("/admin/audit/export.csv", params={"action": "grant.group_set"}).text
    cells = [cell.strip('"') for line in export.splitlines() for cell in line.split(";")]
    assert not [c for c in cells if c.startswith(("=", "+", "@"))]
    assert any(c.startswith("'=HYPERLINK") for c in cells)


def test_api_schema_is_not_served_in_production(monkeypatch):
    """PT-05."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("POSTGRES_PASSWORD", "a-real-password")
    reset_settings_cache()
    try:
        production = TestClient(create_app())
        assert production.get("/openapi.json").status_code == 404
        assert production.get("/docs").status_code == 404
        assert production.get("/health").status_code == 200
    finally:
        monkeypatch.delenv("ENVIRONMENT")
        monkeypatch.delenv("POSTGRES_PASSWORD")
        reset_settings_cache()


def test_metrics_token(anon, monkeypatch):
    """PT-06: optional bearer token on /metrics."""
    assert anon.get("/metrics").status_code == 200  # default: network-level protection only
    monkeypatch.setenv("METRICS_TOKEN", "scrape-me-if-you-can")
    reset_settings_cache()
    try:
        assert anon.get("/metrics").status_code == 401
        assert anon.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert anon.get("/metrics", headers={"Authorization": "Bearer scrape-me-if-you-can"}).status_code == 200
    finally:
        monkeypatch.delenv("METRICS_TOKEN")
        reset_settings_cache()


def test_oversized_connector_reference_is_rejected(member):
    body = {"connection_id": "00000000-0000-0000-0000-000000000000", "references": ["x" * 2001]}
    assert member.post("/chat/sources", json=body).status_code == 422
