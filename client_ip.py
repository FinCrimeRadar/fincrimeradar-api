"""Trusted-hop client IP for the KYB Lab endpoint.

The three older limiters in this service key on the first X-Forwarded-For entry,
which a caller can prepend to rotate keys. This helper takes the Nth entry from
the RIGHT instead, where N is the number of trusted proxies that append to the
header (TRUSTED_PROXY_HOPS, default 1 for Render). Entries to the left of that
point are caller controlled and ignored.

Spec: docs/specs/kyb-lab-api-spec.md, "Quota and abuse protection", item 3a.

CF-Connecting-IP wins when present and valid (v1.2). Every header derived key must
parse as an IP address, so a limiter key is never longer than 45 characters (v1.5).
IPv4-mapped IPv6 keys as IPv4 and any other IPv6 keys by its /64 prefix, because a
single subscriber normally holds a whole /64 (v1.6). The socket fallback is
normalised the same way.
"""

import ipaddress
import os

from fastapi import Request

DEFAULT_TRUSTED_PROXY_HOPS = 1
MAX_KEY_LENGTH = 45  # longest textual IPv6 form, an IPv4 mapped address


def trusted_proxy_hops() -> int:
    """N from TRUSTED_PROXY_HOPS. Missing or invalid falls back to the default.

    0 is accepted and means no trusted proxy: the header is ignored entirely.
    """
    raw = os.environ.get("TRUSTED_PROXY_HOPS", "").strip()
    if not raw:
        return DEFAULT_TRUSTED_PROXY_HOPS
    try:
        hops = int(raw)
    except ValueError:
        return DEFAULT_TRUSTED_PROXY_HOPS
    return hops if hops >= 0 else DEFAULT_TRUSTED_PROXY_HOPS


def _parse_ip(value: str) -> str | None:
    """Normalised address string, or None if the value is not an IP address."""
    value = value.strip()
    # A scoped IPv6 literal ("::1%a") parses and keeps its suffix, so a caller
    # could mint unlimited distinct limiter keys for one address.
    if not value or len(value) > MAX_KEY_LENGTH or "%" in value:
        return None
    return _key(value)


def _key(value: str) -> str | None:
    """Limiter key for an address string, or None if it is not an IP address."""
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return None
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def client_ip(request: Request) -> str:
    # Spec v1.2: Render fronts the service with Cloudflare, which overwrites
    # CF-Connecting-IP. Without Cloudflare in front a caller could set it, so
    # this header must be ignored in any deployment that is not behind it.
    cf = _parse_ip(request.headers.get("cf-connecting-ip", ""))
    if cf:
        return cf
    hops = trusted_proxy_hops()
    header = request.headers.get("x-forwarded-for", "")
    if hops > 0 and header:
        entries = [e.strip() for e in header.split(",") if e.strip()]
        if len(entries) >= hops:
            xff = _parse_ip(entries[-hops])
            if xff:
                return xff
    if not request.client:
        return "unknown"
    host = request.client.host
    return _key(host) or host
