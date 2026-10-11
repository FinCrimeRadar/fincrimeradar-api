"""Trusted-hop client IP for the KYB Lab endpoint.

The three older limiters in this service key on the first X-Forwarded-For entry,
which a caller can prepend to rotate keys. This helper takes the Nth entry from
the RIGHT instead, where N is the number of trusted proxies that append to the
header (TRUSTED_PROXY_HOPS, default 1 for Render). Entries to the left of that
point are caller controlled and ignored.

Spec: docs/specs/kyb-lab-api-spec.md, "Quota and abuse protection", item 3.
"""

import os

from fastapi import Request

DEFAULT_TRUSTED_PROXY_HOPS = 1


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


def client_ip(request: Request) -> str:
    hops = trusted_proxy_hops()
    header = request.headers.get("x-forwarded-for", "")
    if hops > 0 and header:
        entries = [e.strip() for e in header.split(",") if e.strip()]
        if len(entries) >= hops:
            return entries[-hops]
    return request.client.host if request.client else "unknown"
