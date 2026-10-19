"""Generic OpenID Connect login (Authorization Code flow + PKCE).

Works with Entra ID (Azure AD), Google Workspace, Okta, Keycloak, Auth0… Only the
issuer URL, client id and secret are required; endpoints come from discovery.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import jwt

from src.config import get_settings

_DISCOVERY_TTL = 3600


class OIDCError(Exception):
    pass


@dataclass
class OIDCIdentity:
    subject: str
    email: str
    name: str | None
    groups: list[str]


class OIDCClient:
    def __init__(self) -> None:
        self._discovery: dict | None = None
        self._discovered_at = 0.0
        self._jwks: jwt.PyJWKClient | None = None

    @property
    def redirect_uri(self) -> str:
        return get_settings().public_url.rstrip("/") + "/api/auth/oidc/callback"

    def discovery(self) -> dict:
        if self._discovery is None or time.time() - self._discovered_at > _DISCOVERY_TTL:
            issuer = get_settings().oidc_issuer.rstrip("/")
            resp = httpx.get(f"{issuer}/.well-known/openid-configuration", timeout=10)
            resp.raise_for_status()
            self._discovery = resp.json()
            self._discovered_at = time.time()
            self._jwks = jwt.PyJWKClient(self._discovery["jwks_uri"], cache_keys=True)
        return self._discovery

    @staticmethod
    def new_flow() -> dict:
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        return {
            "state": secrets.token_urlsafe(24),
            "nonce": secrets.token_urlsafe(24),
            "verifier": verifier,
            "challenge": challenge,
        }

    def authorization_url(self, flow: dict) -> str:
        settings = get_settings()
        params = {
            "response_type": "code",
            "client_id": settings.oidc_client_id,
            "redirect_uri": self.redirect_uri,
            "scope": settings.oidc_scopes,
            "state": flow["state"],
            "nonce": flow["nonce"],
            "code_challenge": flow["challenge"],
            "code_challenge_method": "S256",
        }
        return f"{self.discovery()['authorization_endpoint']}?{urlencode(params)}"

    def exchange(self, code: str, flow: dict) -> OIDCIdentity:
        settings = get_settings()
        meta = self.discovery()
        resp = httpx.post(
            meta["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "client_id": settings.oidc_client_id,
                "client_secret": settings.oidc_client_secret or "",
                "code_verifier": flow["verifier"],
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )
        if resp.status_code != 200:
            raise OIDCError(f"Échange du code refusé par le fournisseur ({resp.status_code}).")
        id_token = resp.json().get("id_token")
        if not id_token:
            raise OIDCError("Le fournisseur n'a pas renvoyé d'id_token.")

        assert self._jwks is not None
        signing_key = self._jwks.get_signing_key_from_jwt(id_token)
        try:
            claims = jwt.decode(
                id_token,
                signing_key.key,
                # Asymmetric algorithms only: never "none", never HS* with a public key.
                algorithms=[
                    alg
                    for alg in meta.get("id_token_signing_alg_values_supported") or ["RS256"]
                    if alg.startswith(("RS", "PS", "ES", "EdDSA"))
                ]
                or ["RS256"],
                audience=settings.oidc_client_id,
                issuer=meta["issuer"],
                leeway=60,
            )
        except jwt.PyJWTError as exc:
            raise OIDCError(f"id_token invalide : {exc}") from exc
        if claims.get("nonce") != flow["nonce"]:
            raise OIDCError("Nonce invalide.")

        email = claims.get(settings.oidc_email_claim) or claims.get("preferred_username") or claims.get("upn")
        if not email or "@" not in email:
            raise OIDCError("Le jeton ne contient pas d'adresse e-mail.")
        email = email.lower()
        allowed = [d.lower().lstrip("@") for d in settings.split_csv(settings.oidc_allowed_domains)]
        if allowed and email.rsplit("@", 1)[1] not in allowed:
            raise OIDCError("Domaine de messagerie non autorisé.")
        if claims.get("email_verified") is False:
            raise OIDCError("Adresse e-mail non vérifiée chez le fournisseur.")

        groups = claims.get(settings.oidc_groups_claim) or []
        if isinstance(groups, str):
            groups = [groups]
        return OIDCIdentity(subject=str(claims["sub"]), email=email, name=claims.get("name"), groups=list(groups))


oidc_client = OIDCClient()
