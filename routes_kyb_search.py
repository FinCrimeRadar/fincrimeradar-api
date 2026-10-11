"""KYB Lab company name search.

Spec: docs/specs/kyb-lab-api-spec.md section 14 (v1.9). Where this file and the
spec disagree, the spec wins and this file is the bug.

POST /api/kyb/search with a JSON body {"q": "<text>"} returns up to 20 candidate
companies (company number, title, status, type, date of creation and a scope
flag) from one Companies House /search/companies call. It never returns a
readout, an officer, a PSC or any person field, and the search term is never
written to an application log.

A path scoped ASGI control (SearchGuard) sits behind CORS and in front of
routing. For this one path it counts the per client burst limit, rejects a
non identity Content-Encoding (415), enforces a 4 KiB received bytes limit
(413) and a 5 second body receive deadline (408), all with generic bodies that
echo nothing. Other routes are untouched.
"""

import asyncio
import json
import re
import threading
import uuid
from collections import OrderedDict
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

import routes_kyb_lab as kyb
from client_ip import client_ip

SEARCH_PATH = "/api/kyb/search"
UPSTREAM_SEARCH_PATH = "/search/companies"
ITEMS_PER_PAGE = 20
MAX_CANDIDATES = 20

QUERY_MIN_CHARS = 2
QUERY_MAX_CHARS = 160
TITLE_MAX_CHARS = 500

BODY_LIMIT_BYTES = 4096
BODY_DEADLINE_SECONDS = 5

CACHE_TTL_SECONDS = 300
CACHE_MAX_ENTRIES = 1000

SEARCH_NOTICE = (
    "Several companies can share a similar name, and a name match is not an "
    "identity check. Confirm the company number before relying on a result."
)

# Fixed generic bodies: nothing from the request is ever echoed.
DETAIL_INVALID = "Invalid search text."
DETAIL_TOO_LARGE = "Request too large."
DETAIL_TIMEOUT = "Request timed out."
DETAIL_ENCODING = "Unsupported content encoding."
DETAIL_BURST = "Lookup limit reached. Try again shortly."

router = APIRouter()

_cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
_cache_lock = threading.Lock()


def reset_search_state_for_tests() -> None:
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------- upstream models
# extra="ignore": address, snippet, matches, links, description, kind and every
# other field are never declared, so they cannot reach the response, a log or
# the cache. No officer, PSC or person field exists upstream of this endpoint.


class _Up(BaseModel):
    model_config = ConfigDict(extra="ignore")


class UpSearchItem(_Up):
    company_number: str
    title: str = Field(max_length=TITLE_MAX_CHARS)
    company_status: str | None = Field(default=None, max_length=100)
    company_type: str | None = Field(default=None, max_length=100)
    date_of_creation: str | None = Field(default=None, max_length=40)


class UpSearchList(_Up):
    items: list[UpSearchItem] | None = None
    # Validated by hand: an invalid or inconsistent total is a 502.
    total_results: Any = None


class _Out(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class CandidateOut(_Out):
    company_number: str
    title: str
    company_status: str | None
    company_type: str | None
    date_of_creation: str | None
    scope: Literal["interpreted", "unsupported_entity_type"]


class SearchOut(_Out):
    retrieved_at: str
    source: str
    candidates: list[CandidateOut]
    truncated: bool
    reported_total: int | None
    search_notice: str
    attribution: str


# ---------------------------------------------------------------- input


def normalise_search_text(raw: Any) -> str | None:
    """Spec 14.3 item 1. None means reject with 422.

    (a) the raw value must be a printable string (tab, newline, U+2028 and every
    other control or non ASCII space is rejected before any normalisation),
    (b) then U+0020 only normalisation, (c) then the 2 to 160 code point check.
    """
    if not isinstance(raw, str) or not raw.isprintable():
        return None
    text = re.sub(" +", " ", raw.strip(" "))
    if QUERY_MIN_CHARS <= len(text) <= QUERY_MAX_CHARS:
        return text
    return None


# ---------------------------------------------------------------- cache


def _purge_expired(now: float) -> None:
    """Drop every expired entry. Caller holds the lock. At most 1,000 entries."""
    for key in [k for k, (expires, _) in _cache.items() if expires <= now]:
        del _cache[key]


def _cache_get(key: str) -> dict | None:
    now = kyb._monotonic()
    with _cache_lock:
        _purge_expired(now)
        hit = _cache.get(key)
        if hit is None:
            return None
        if hit[0] <= now:
            del _cache[key]
            return None
        _cache.move_to_end(key)
        return hit[1]


def _cache_put(key: str, body: dict) -> None:
    now = kyb._monotonic()
    with _cache_lock:
        _purge_expired(now)
        _cache[key] = (now + CACHE_TTL_SECONDS, body)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)


# ---------------------------------------------------------------- logging


def _log(
    request_id: str,
    outcome: str,
    query_length: int | None,
    call: kyb._Call,
    cache: str,
) -> None:
    """One structured line. The search term and anything derived from it never appear."""
    print(
        "kyb_search "
        + json.dumps(
            {
                "request_id": request_id,
                "outcome": outcome,
                "query_length": query_length,
                "upstream_calls": call.count,
                "upstream_seconds": round(call.seconds, 3),
                "cache": cache,
                "quota_remaining": kyb.quota_remaining(),
                "upstream_ratelimit_headers": call.ratelimit_headers or None,
            }
        ),
        flush=True,
    )


# ---------------------------------------------------------------- search


def _candidates_and_totals(raw: Any) -> tuple[list[CandidateOut], bool, int | None]:
    """Spec 14.4 items 2 to 4 and 14.5. Raises _Fail(502) on any bad envelope."""
    env = kyb._parse(UpSearchList, raw, "bad_search")
    if env.items is None:
        raise kyb._Fail(502, "Upstream error.", "bad_search_items")
    n = len(env.items)
    has_total = "total_results" in env.model_fields_set
    total = env.total_results
    if has_total and (
        isinstance(total, bool) or not isinstance(total, int) or total < 0 or total < n
    ):
        raise kyb._Fail(502, "Upstream error.", "bad_search_total")
    if n == 0 and not (has_total and total == 0):
        raise kyb._Fail(502, "Upstream error.", "bad_search_empty")

    candidates: list[CandidateOut] = []
    for item in env.items[:MAX_CANDIDATES]:
        number = item.company_number
        if not number.isascii():
            raise kyb._Fail(502, "Upstream error.", "bad_search_number")
        number = number.upper()
        if not re.fullmatch(r"[A-Z0-9]{8}", number):
            raise kyb._Fail(502, "Upstream error.", "bad_search_number")
        candidates.append(
            CandidateOut(
                company_number=number,
                title=item.title,
                company_status=item.company_status,
                company_type=item.company_type,
                date_of_creation=item.date_of_creation,
                scope=(
                    "interpreted"
                    if item.company_type in kyb.IN_SCOPE_TYPES
                    else "unsupported_entity_type"
                ),
            )
        )
    shown = len(candidates)
    if not has_total:
        return candidates, True, None
    return candidates, total > shown, total


def _run_search(search_string: str, ip: str, request_id: str) -> dict:
    """Cache, quota and the single upstream call. Synchronous, runs in a worker thread."""
    call = kyb._Call(ip)
    length = len(search_string)
    cached = _cache_get(search_string)
    if cached is not None:
        _log(request_id, "ok", length, call, "hit")
        return cached
    try:
        raw = kyb._fetch(
            UPSTREAM_SEARCH_PATH,
            call,
            {"q": search_string, "items_per_page": ITEMS_PER_PAGE},
        )
        if raw is None:
            # A 404 is an unexpected status for a search, never an empty list.
            raise kyb._Fail(502, "Upstream error.", "search_404")
        candidates, truncated, reported_total = _candidates_and_totals(raw)
    except kyb._Fail as fail:
        _log(request_id, fail.outcome, length, call, "miss")
        raise fail.to_http() from None
    retrieved_iso = (
        kyb._utcnow()
        .astimezone(kyb.UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    body = SearchOut(
        retrieved_at=retrieved_iso,
        source="Companies House Public Data API",
        candidates=candidates,
        truncated=truncated,
        reported_total=reported_total,
        search_notice=SEARCH_NOTICE,
        attribution=kyb.ATTRIBUTION.format(retrieved_at=retrieved_iso),
    ).model_dump()
    _cache_put(search_string, body)
    _log(request_id, "ok", length, call, "miss")
    return body


def _generic(status: int, detail: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status, headers=headers)


async def _read_bounded(request: Request) -> bytes | None:
    """The request body, or None if it passes BODY_LIMIT_BYTES.

    SearchGuard has normally bounded the body already; this is an independent
    bound so the handler never reads an unbounded stream if the guard does not
    match the path (for example behind a root path).
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > BODY_LIMIT_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(SEARCH_PATH)
async def kyb_search(request: Request):
    request_id = uuid.uuid4().hex[:12]
    ip = client_ip(request)
    call = kyb._Call(ip)
    raw_body = await _read_bounded(request)
    if raw_body is None:
        _log(request_id, "body_too_large", None, call, "n/a")
        return _generic(413, DETAIL_TOO_LARGE)
    # Only a JSON request is parsed: a cross site simple request (text/plain, a form
    # post) is rejected here, before any quota is touched, and the body is decoded as
    # strict UTF-8 (json.loads on bytes would also accept UTF-16 and UTF-32).
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    payload = None
    if media_type == "application/json":
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except ValueError:
            payload = None
    text = None
    if isinstance(payload, dict) and set(payload) == {"q"}:
        text = normalise_search_text(payload["q"])
    if text is None:
        _log(request_id, "invalid_input", None, call, "n/a")
        return _generic(422, DETAIL_INVALID)
    if not kyb.COMPANIES_HOUSE_API_KEY:
        _log(request_id, "not_configured", len(text), call, "n/a")
        return _generic(503, "Service not configured.")
    return await run_in_threadpool(_run_search, text, ip, request_id)


# ---------------------------------------------------------------- the path scoped control


class _TooLarge(Exception):
    pass


class SearchGuard:
    """Pure ASGI control for exactly one path (spec 14.3 items 7 and 8).

    Placed behind CORS (the CORS layer answers preflights, so OPTIONS never
    reaches it and is exempt) and in front of routing (so a wrong method is
    counted before it is answered 405).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("path") != SEARCH_PATH
            or scope.get("method") == "OPTIONS"
        ):
            await self.app(scope, receive, send)
            return

        call = kyb._Call()
        request_id = uuid.uuid4().hex[:12]
        request = Request(scope)

        async def reject(status: int, detail: str, outcome: str, headers=None):
            _log(request_id, outcome, None, call, "n/a")
            await _generic(status, detail, headers)(scope, receive, send)

        # 1. The per client burst check runs before anything about the request is read.
        if kyb._ip_limited(client_ip(request)):
            await reject(
                429,
                DETAIL_BURST,
                "ip_rate_limited",
                {"Retry-After": str(kyb.IP_LIMIT_WINDOW_SECONDS)},
            )
            return

        # Only a POST has a body this control reads. Any other method goes to
        # routing, which answers 405 (it was counted above).
        if scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope["headers"]
        }
        # 2. Compression is never decompressed or parsed.
        encoding = headers.get("content-encoding", "identity").strip().lower()
        if encoding != "identity":
            await reject(415, DETAIL_ENCODING, "unsupported_encoding")
            return
        # 3. A declared length over the limit is rejected before reading anything.
        try:
            declared = int(headers.get("content-length", ""))
        except ValueError:
            declared = None
        if declared is not None and declared > BODY_LIMIT_BYTES:
            await reject(413, DETAIL_TOO_LARGE, "body_too_large")
            return

        # 4. Count the bytes actually received, never trusting Content-Length, under
        #    one overall deadline. Nothing past the limit is kept or passed on.
        chunks: list[bytes] = []
        disconnected = False

        async def read_body() -> None:
            nonlocal disconnected
            total = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    disconnected = True
                    return
                chunk = message.get("body", b"")
                total += len(chunk)
                if total > BODY_LIMIT_BYTES:
                    raise _TooLarge
                chunks.append(chunk)
                if not message.get("more_body", False):
                    return

        try:
            await asyncio.wait_for(read_body(), timeout=BODY_DEADLINE_SECONDS)
        except _TooLarge:
            await reject(413, DETAIL_TOO_LARGE, "body_too_large")
            return
        except TimeoutError:
            await reject(408, DETAIL_TIMEOUT, "body_timeout")
            return
        if disconnected:
            _log(request_id, "client_disconnected", None, call, "n/a")
            return

        body = b"".join(chunks)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def install_search_controls(app: FastAPI) -> None:
    """Add SearchGuard. Call it BEFORE adding CORSMiddleware so CORS wraps it."""
    app.add_middleware(SearchGuard)
