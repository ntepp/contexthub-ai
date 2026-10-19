import socket

import pytest

from src.ingestion.url_fetch import UnsafeURL, validate_url


def resolver_for(ip: str):
    def resolve(host, port, proto=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, proto, "", (ip, port))]

    return resolve


@pytest.mark.parametrize(
    "ip",
    ["127.0.0.1", "10.1.2.3", "172.16.0.9", "192.168.1.1", "169.254.169.254", "0.0.0.0", "::1", "fd00::1"],
)
def test_internal_addresses_are_blocked(ip):
    with pytest.raises(UnsafeURL):
        validate_url("https://example.org/doc", resolver=resolver_for(ip))


def test_public_address_is_allowed():
    assert validate_url("https://eur-lex.europa.eu/x", resolver=resolver_for("147.67.210.10"))


@pytest.mark.parametrize(
    "url",
    ["ftp://example.org/a", "file:///etc/passwd", "https://user:pw@example.org/", "https://example.org:8443/"],
)
def test_unsafe_url_shapes(url):
    with pytest.raises(UnsafeURL):
        validate_url(url, resolver=resolver_for("147.67.210.10"))
