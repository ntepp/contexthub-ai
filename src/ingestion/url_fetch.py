"""Server-side URL fetching with SSRF protection.

URL ingestion is disabled by default (ALLOW_URL_INGESTION). When enabled, only
http(s) on ports 80/443 to public addresses is allowed, every redirect hop is
re-validated, and the body is capped. Residual risk: DNS rebinding between the check
and the connection — acceptable because the feature is opt-in and editor-only.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

from src.config import get_settings
from src.ingestion.filetypes import IngestionError

_ALLOWED_PORTS = {80, 443}
_MAX_REDIRECTS = 5
_CONTENT_TYPES = {
    "text/html": "text/html",
    "application/xhtml+xml": "text/html",
    "application/pdf": "application/pdf",
    "text/plain": "text/plain",
    "text/markdown": "text/markdown",
}


class UnsafeURL(IngestionError):
    pass


def _is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def validate_url(url: str, resolver=socket.getaddrinfo) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURL("Seules les URL http(s) sont acceptées.")
    if not parsed.hostname:
        raise UnsafeURL("URL sans nom d'hôte.")
    if parsed.username or parsed.password:
        raise UnsafeURL("Les identifiants dans l'URL ne sont pas acceptés.")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in _ALLOWED_PORTS:
        raise UnsafeURL("Port non autorisé (80 et 443 uniquement).")
    try:
        infos = resolver(parsed.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURL(f"Nom d'hôte introuvable : {parsed.hostname}") from exc
    addresses = {info[4][0] for info in infos}
    if not addresses or not all(_is_public(a) for a in addresses):
        raise UnsafeURL("L'URL pointe vers une adresse interne ou réservée.")
    return url.strip()


@dataclass
class FetchedURL:
    url: str
    mime_type: str
    content: bytes


def fetch_url(url: str) -> FetchedURL:
    settings = get_settings()
    limit = settings.url_max_mb * 1024 * 1024
    current = validate_url(url)
    with httpx.Client(timeout=settings.url_fetch_timeout_seconds, follow_redirects=False) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            with client.stream("GET", current, headers={"User-Agent": f"ContextHub/{settings.app_version}"}) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise IngestionError("Redirection sans destination.")
                    current = validate_url(urljoin(current, location))
                    continue
                if resp.status_code >= 400:
                    raise IngestionError(f"Le serveur distant a répondu {resp.status_code}.")
                content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                mime = _CONTENT_TYPES.get(content_type)
                if mime is None:
                    raise IngestionError(f"Type de contenu non supporté : {content_type or 'inconnu'}.")
                body = bytearray()
                for block in resp.iter_bytes():
                    body.extend(block)
                    if len(body) > limit:
                        raise IngestionError(f"Contenu distant supérieur à {settings.url_max_mb} Mo.")
                return FetchedURL(url=current, mime_type=mime, content=bytes(body))
    raise IngestionError("Trop de redirections.")
