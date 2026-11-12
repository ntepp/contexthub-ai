"""End-to-end SSO scenario through the real UI, against the local Keycloak.

    docker compose -f docker-compose.dev.yaml --profile sso up -d      # deploy/keycloak/README.md
    E2E_BASE_URL=http://localhost:5173 E2E_EMAIL=... E2E_PASSWORD=... E2E_SSO=1 pytest -m e2e tests/e2e/test_ui_sso.py

A local administrator gives the SSO group ``rh`` access to a collection. Alice (group
``rh``) signs in through Keycloak and finds the collection; Chloé (``juridique``) does
not; Bob (``contexthub-admins``) arrives as an administrator. Skipped unless E2E_SSO=1.
"""

from __future__ import annotations

import os
import time

import pytest

BASE = os.getenv("E2E_BASE_URL")
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not (BASE and os.getenv("E2E_SSO")), reason="E2E_BASE_URL et E2E_SSO=1 requis"),
]
HEADERS = {"X-Requested-With": "contexthub"}
ACCOUNTS = {"alice": "alice-demo-2026", "chloe": "chloe-demo-2026", "bob": "bob-demo-2026"}


def test_sso_login_and_group_access_through_the_ui():
    sync_api = pytest.importorskip("playwright.sync_api")
    expect = sync_api.expect
    name = f"E2E SSO {time.strftime('%H%M%S')}"

    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch(channel=os.getenv("E2E_BROWSER_CHANNEL", "chrome"), headless=True)

        def sso_session(username: str):
            """A fresh browser profile signed in through Keycloak."""
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            page.goto(f"{BASE}/login")
            page.get_by_role("link", name="Se connecter avec Keycloak").click()
            page.wait_for_url("**/realms/contexthub/**", timeout=30_000)
            page.fill("#username", username)
            page.fill("#password", ACCOUNTS[username])
            page.locator("#kc-login").click()
            page.wait_for_url(f"{BASE}/", timeout=30_000)
            return context, page

        admin_context = browser.new_context(viewport={"width": 1440, "height": 900})
        admin = admin_context.new_page()
        admin.goto(f"{BASE}/login")
        admin.fill("#email", os.environ["E2E_EMAIL"])
        admin.fill("#password", os.environ["E2E_PASSWORD"])
        admin.locator("button[type=submit]").click()
        admin.wait_for_url(f"{BASE}/")

        collection = admin.request.post(f"{BASE}/api/collections", headers=HEADERS, data={"name": name}).json()
        try:
            # --- the administrator opens the collection to the directory group "rh" ---
            admin.goto(f"{BASE}/admin/acces")
            admin.locator("#acc-col").select_option(label=name)
            admin.locator("#acc-group").fill("rh")
            admin.get_by_role("button", name="Donner l'accès au groupe").click()
            expect(admin.get_by_role("row").filter(has_text="rh").get_by_label("Droit du groupe rh")).to_be_visible()

            # --- Alice (rh) signs in with Keycloak and finds the collection ---------
            alice_context, alice = sso_session("alice")
            me = alice.request.get(f"{BASE}/api/auth/me").json()
            assert me["email"] == "alice@acme.test" and me["auth_provider"] == "oidc" and me["role"] == "member"
            assert me["sso_groups"] == ["rh"]
            assert [c["name"] for c in me["collections"] if c["name"] == name] == [name]
            expect(alice.get_by_text("Alice Martin")).to_be_visible()
            expect(alice.locator("select").first.locator("option", has_text=name)).to_have_count(1)
            assert alice.request.get(f"{BASE}/api/admin/users").status == 403
            alice_context.close()

            # --- Chloé (juridique) does not --------------------------------------------
            chloe_context, chloe = sso_session("chloe")
            me = chloe.request.get(f"{BASE}/api/auth/me").json()
            assert me["sso_groups"] == ["juridique"]
            assert name not in [c["name"] for c in me["collections"]]
            assert chloe.request.get(f"{BASE}/api/collections/{collection['id']}/documents").status == 404
            chloe_context.close()

            # --- Bob (contexthub-admins) is an administrator ---------------------------
            bob_context, bob = sso_session("bob")
            assert bob.request.get(f"{BASE}/api/auth/me").json()["role"] == "admin"
            bob.goto(f"{BASE}/admin/utilisateurs")
            expect(bob.get_by_role("row").filter(has_text="alice@acme.test")).to_be_visible()
            bob_context.close()

            events = admin.request.get(f"{BASE}/api/admin/audit", params={"action": "auth.login"}).json()["items"]
            assert [e["details"]["method"] for e in events[:3]] == ["oidc", "oidc", "oidc"]
        finally:
            admin.request.delete(f"{BASE}/api/collections/{collection['id']}", headers=HEADERS)
            for user in admin.request.get(f"{BASE}/api/admin/users").json():
                if user["email"] in {f"{u}@acme.test" for u in ACCOUNTS}:
                    admin.request.delete(f"{BASE}/api/admin/users/{user['id']}", headers=HEADERS)
            browser.close()
