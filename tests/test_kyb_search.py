"""KYB Lab company name search tests, one group per item of spec section 14.8.

The upstream is always a fake. Nothing here calls Companies House. Most tests go
through the real application (main.app) so the CORS layer, the path scoped
control and routing are the production stack in the production order.
"""

import asyncio
import copy
import gzip
import json
from datetime import UTC, datetime

import pytest
import requests
from fastapi.testclient import TestClient

import main
import routes_kyb_lab as kyb
import routes_kyb_search as ks

TEST_KEY = "test-key-not-a-real-key"
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
ORIGIN = "https://fincrimeradar.org"
PATH = "/api/kyb/search"
MISSING = object()  # key left out of the upstream envelope
TC = "testclient"  # the client key TestClient requests get
ASGI_CLIENT = "203.0.113.5"  # the client key of the direct ASGI requests below


# ------------------------------------------------------------------ fakes and helpers


class FakeResponse:
    def __init__(self, status, body, headers=None, bad_json=False):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._body


class Upstream:
    """Replaces routes_kyb_lab._http_get and records every call."""

    def __init__(self, result):
        self.result = result
        self.calls = []

    def __call__(self, url, params, auth, timeout):
        assert url.startswith(kyb.UPSTREAM_BASE), url
        self.calls.append(
            {"path": url[len(kyb.UPSTREAM_BASE) :], "params": params, "auth": auth}
        )
        result = self.result
        if isinstance(result, Exception):
            raise result
        if isinstance(result, FakeResponse):
            return result
        status, body = result if isinstance(result, tuple) else (200, result)
        return FakeResponse(status, body)

    @property
    def paths(self):
        return [c["path"] for c in self.calls]


PII = {
    "address": {"address_line_1": "PII_ADDRESS_LINE", "postal_code": "PII_POSTCODE"},
    "address_snippet": "PII_ADDRESS_SNIPPET",
    "snippet": "PII_SNIPPET",
    "description": "PII_DESCRIPTION",
    "description_identifier": ["incorporated-on"],
    "matches": {"title": [1, 3], "snippet": [2, 4]},
    "links": {"self": "/company/PII_LINK"},
    "kind": "searchresults#company",
    "date_of_cessation": "2020-01-01",
}


def item(i=0, **kw):
    d = {
        "company_number": f"{i:08d}",
        "title": f"EXAMPLE {i} LIMITED",
        "company_status": "active",
        "company_type": "ltd",
        "date_of_creation": "2019-01-01",
        **copy.deepcopy(PII),
    }
    d.update(kw)
    return d


def envelope(items, total=MISSING, **extra):
    env = {
        "kind": "search#companies",
        "items_per_page": 20,
        "start_index": 0,
        "page_number": 1,
        "etag": "PII_ETAG",
        **extra,
    }
    if items is not MISSING:
        env["items"] = items
    if total is not MISSING:
        env["total_results"] = total
    return env


def good(n=3, total=None):
    return envelope([item(i) for i in range(n)], n if total is None else total)


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    kyb.reset_state_for_tests()
    ks.reset_search_state_for_tests()
    monkeypatch.setattr(kyb, "COMPANIES_HOUSE_API_KEY", TEST_KEY)
    monkeypatch.setattr(kyb, "_utcnow", lambda: NOW)
    monkeypatch.setattr(kyb.time, "sleep", lambda s: None)
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)
    yield
    kyb.reset_state_for_tests()
    ks.reset_search_state_for_tests()


@pytest.fixture
def up(monkeypatch):
    def install(result=None):
        fake = Upstream(good() if result is None else result)
        monkeypatch.setattr(kyb, "_http_get", fake)
        return fake

    return install


def post(client, q, **kw):
    return client.post(PATH, json={"q": q}, **kw)


def keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from keys(v)


FORBIDDEN_KEYS = {
    "address",
    "address_snippet",
    "snippet",
    "description",
    "description_identifier",
    "matches",
    "links",
    "date_of_cessation",
    "etag",
    "kind",
    "page_number",
    "start_index",
    "total_results",
    "items_per_page",
    "items",
    "officers",
    "officer",
    "pscs",
    "psc",
    "person",
    "date_of_birth",
    "nationality",
}


def burst_count(key):
    return len(kyb._ip_log.get(key, ()))


def nothing_charged(upstream):
    return (
        not upstream.calls
        and not kyb._client_calls
        and not kyb._budget
        and not ks._cache
    )


async def asgi(
    method="POST",
    headers=None,
    chunks=(b"",),
    delays=None,
    client=(ASGI_CLIENT, 4321),
    path=PATH,
):
    """Drive main.app directly so the body can be declared, understated, chunked or slow."""
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": hdrs,
        "client": client,
        "server": ("test", 443),
    }
    delays = delays or [0] * len(chunks)
    state = {"i": 0}

    async def receive():
        i = state["i"]
        if i >= len(chunks):
            await asyncio.Event().wait()  # no more body and no disconnect
        state["i"] += 1
        if delays[i]:
            await asyncio.sleep(delays[i])
        return {
            "type": "http.request",
            "body": chunks[i],
            "more_body": i < len(chunks) - 1,
        }

    sent = []

    async def send(message):
        sent.append(message)

    await main.app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    resp_headers = {
        k.decode().lower(): v.decode()
        for k, v in next(
            m["headers"] for m in sent if m["type"] == "http.response.start"
        )
    }
    body = b"".join(
        m.get("body", b"") for m in sent if m["type"] == "http.response.body"
    )
    return status, resp_headers, body


def run(coro):
    return asyncio.run(coro)


VALID_BODY = b'{"q":"Acme Ltd"}'


# ------------------------------------------------------------------ 1. method and body


def test_get_is_405_nothing_is_forwarded_and_it_counts_toward_the_burst(client, up):
    upstream = up()
    r = client.get(PATH + "?q=acme")
    assert r.status_code == 405 and "POST" in r.headers["allow"]
    assert "acme" not in r.text
    assert upstream.calls == []
    assert burst_count(TC) == 1


def test_query_string_q_is_ignored_and_never_forwarded(client, up):
    upstream = up()
    r = client.post(
        PATH + "?q=from-the-url&items_per_page=99", json={"q": "from the body"}
    )
    assert r.status_code == 200
    assert upstream.calls[0]["params"] == {"q": "from the body", "items_per_page": 20}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"json": {"q": "zzMARKERzz", "start_index": 3}},
        {"json": {"q": "zzMARKERzz", "items_per_page": 5}},
        {"json": {"q": "zzMARKERzz", "page": 2}},
        {"json": {"q": "zzMARKERzz", "restrictions": "active-companies"}},
        {"json": {"q": "zzMARKERzz", "other": 1}},
        {"json": {"q": 12345}},
        {"json": {"q": ["zzMARKERzz"]}},
        {"json": {"q": None}},
        {"json": {}},
        {"json": []},
        {"json": "zzMARKERzz"},
        {
            "content": b"zzMARKERzz not json",
            "headers": {"Content-Type": "application/json"},
        },
        {"content": b"", "headers": {"Content-Type": "application/json"}},
    ],
)
def test_bad_bodies_give_a_fixed_422_that_echoes_nothing(client, up, kwargs):
    upstream = up()
    r = client.post(PATH, **kwargs)
    assert r.status_code == 422
    assert r.json() == {"detail": ks.DETAIL_INVALID}
    assert "zzMARKERzz" not in r.text
    assert upstream.calls == [] and not kyb._client_calls and not kyb._budget


# ------------------------------------------------------------------ 2. input validation and order


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Acme", "Acme"),
        ("  Acme   Ltd  ", "Acme Ltd"),
        ("ab", "ab"),
        ("x" * 160, "x" * 160),
        ("Café Straße", "Café Straße"),
        ("中文公司", "中文公司"),
    ],
)
def test_valid_search_strings_are_normalised_on_u0020_only(raw, expected):
    assert ks.normalise_search_text(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "a",
        "",
        " ",
        "   ",
        " a ",
        "x" * 161,
        "Acme\nLtd",
        "Acme\tLtd",
        "Acme\rLtd",
        "Acme\x00Ltd",
        "Acme\x1fLtd",
        "Acme\u2028Ltd",
        "Acme\u2029Ltd",
        "Acme\u00a0Ltd",
        "Acme\u200bLtd",
        "Acme\u202eLtd",
        "Acme\u2066Ltd",
        "Acme\ufeffLtd",
        "\nAcme",
        "Acme\n",
    ],
)
def test_invalid_search_strings_are_rejected_before_any_normalisation(raw):
    assert ks.normalise_search_text(raw) is None


def test_newline_inside_the_term_is_422_not_normalised_to_a_space(client, up):
    upstream = up()
    r = post(client, "Acme\nLtd")
    assert r.status_code == 422 and r.json() == {"detail": ks.DETAIL_INVALID}
    assert upstream.calls == []


def test_length_boundaries_through_the_endpoint(client, up):
    upstream = up()
    assert post(client, "x").status_code == 422
    assert post(client, "xy").status_code == 200
    assert post(client, "x" * 160).status_code == 200
    assert post(client, "x" * 161).status_code == 422
    assert post(client, "      ").status_code == 422
    assert len(upstream.calls) == 2


# ------------------------------------------------------------------ 3. logging


def test_the_term_never_appears_in_any_log_line(client, up, capsys):
    up()
    term = "Zq Needle Corp"
    assert post(client, term).status_code == 200
    assert post(client, term).status_code == 200  # cache hit
    assert post(client, term + "\n").status_code == 422
    out = capsys.readouterr()
    text = out.out + out.err
    assert "Needle" not in text and "needle" not in text.lower()
    assert f'"query_length": {len(term)}' in text and '"cache": "hit"' in text


def test_the_term_is_not_logged_on_failure_paths(client, up, capsys):
    up((500, {"detail": "boom"}))
    assert post(client, "Zq Needle Corp").status_code == 502
    out = capsys.readouterr()
    assert "Needle" not in out.out + out.err


# ------------------------------------------------------------------ 4. the upstream call


def test_exactly_one_upstream_call_with_exact_params(client, up):
    upstream = up()
    r = post(client, "  Acme   Ltd ")
    assert r.status_code == 200
    assert upstream.paths == ["/search/companies"]
    assert upstream.calls[0]["params"] == {"q": "Acme Ltd", "items_per_page": 20}
    assert upstream.calls[0]["auth"] == (TEST_KEY, "")


# ------------------------------------------------------------------ 5. no paging


def test_fifty_upstream_items_give_twenty_candidates_and_truncated(client, up):
    up(envelope([item(i) for i in range(50)], 50))
    body = post(client, "Acme").json()
    assert len(body["candidates"]) == 20
    assert body["truncated"] is True and body["reported_total"] == 50
    assert set(body) == {
        "retrieved_at",
        "source",
        "candidates",
        "truncated",
        "reported_total",
        "search_notice",
        "attribution",
    }


# ------------------------------------------------------------------ 6. minimisation


def test_no_dropped_field_or_marker_reaches_the_response_the_cache_or_the_logs(
    client, up, capsys
):
    up(envelope([item(i) for i in range(3)], 3))
    r = post(client, "Acme")
    body = r.json()
    assert not FORBIDDEN_KEYS & set(keys(body))
    for cand in body["candidates"]:
        assert set(cand) == {
            "company_number",
            "title",
            "company_status",
            "company_type",
            "date_of_creation",
            "scope",
        }
    cached = json.dumps([v[1] for v in ks._cache.values()])
    logs = capsys.readouterr()
    for blob in (r.text, cached, logs.out + logs.err):
        assert "PII_" not in blob
    for entry in ks._cache.values():
        assert not FORBIDDEN_KEYS & set(keys(entry[1]))


# ------------------------------------------------------------------ 7. scope flag


@pytest.mark.parametrize("type_", sorted(kyb.IN_SCOPE_TYPES))
def test_allowlisted_types_are_interpreted(client, up, type_):
    up(envelope([item(0, company_type=type_)], 1))
    assert post(client, "Acme").json()["candidates"][0]["scope"] == "interpreted"


@pytest.mark.parametrize(
    "type_",
    [
        "registered-overseas-entity",
        "royal-charter",
        "limited-partnership",
        "a-type-nobody-has-seen",
        None,
    ],
)
def test_other_types_are_unsupported(client, up, type_):
    up(envelope([item(0, company_type=type_)], 1))
    assert (
        post(client, "Acme").json()["candidates"][0]["scope"]
        == "unsupported_entity_type"
    )


# ------------------------------------------------------------------ 8. quota


def test_a_miss_charges_one_allowance_unit_and_one_global_unit_a_hit_none(client, up):
    upstream = up()
    assert post(client, "Acme").status_code == 200
    assert len(kyb._client_calls[TC]) == 1 and len(kyb._budget) == 1
    assert post(client, "Acme").status_code == 200
    assert len(upstream.calls) == 1
    assert len(kyb._client_calls[TC]) == 1 and len(kyb._budget) == 1
    assert burst_count(TC) == 2  # a hit still counts toward the burst limit


def test_a_client_over_its_allowance_gets_429_with_retry_after(client, up):
    upstream = up()
    now = kyb._monotonic()
    kyb._client_calls[TC] = kyb.deque([now] * kyb.CLIENT_ALLOWANCE_MAX)
    r = post(client, "Acme")
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert upstream.calls == []


def test_an_exhausted_global_budget_gives_503_with_retry_after(client, up):
    upstream = up()
    now = kyb._monotonic()
    kyb._budget.extend([now] * kyb.UPSTREAM_BUDGET_MAX)
    r = post(client, "Acme")
    assert r.status_code == 503 and int(r.headers["retry-after"]) >= 1
    assert upstream.calls == []


def test_the_burst_counter_is_shared_with_the_company_lookup(client, monkeypatch):
    def routed(url, params, auth, timeout):
        if url.endswith("/search/companies"):
            return FakeResponse(200, good())
        return FakeResponse(404, {})

    monkeypatch.setattr(kyb, "_http_get", routed)
    for _ in range(5):
        assert client.get("/api/kyb/company/ZZ000001").status_code == 404
    for i in range(5):
        assert post(client, f"Acme {i}").status_code == 200
    r = post(client, "Acme again")
    assert r.status_code == 429 and r.headers["retry-after"] == str(
        kyb.IP_LIMIT_WINDOW_SECONDS
    )


def test_an_upstream_429_starts_the_shared_block(client, up):
    upstream = up((429, {}))
    assert post(client, "Acme").status_code == 503
    n = len(upstream.calls)
    r = post(client, "Other")
    assert (
        r.status_code == 503 and len(upstream.calls) == n
    )  # blocked, no new upstream call


# ------------------------------------------------------------------ 9. cache key


def test_the_cache_key_is_the_exact_string_sent_upstream(client, up):
    upstream = up()
    post(client, "Acme Ltd")
    post(client, "acme ltd")
    assert len(upstream.calls) == 2  # no case folding
    post(client, "Straße")
    post(client, "STRASSE")
    assert len(upstream.calls) == 4
    post(client, "Acme  Ltd")  # two spaces normalise to the first entry
    post(client, " Acme Ltd ")
    assert len(upstream.calls) == 4
    assert set(ks._cache) == {"Acme Ltd", "acme ltd", "Straße", "STRASSE"}


def test_cache_ttl_and_bound(client, up, monkeypatch):
    assert ks.CACHE_TTL_SECONDS == 300 and ks.CACHE_MAX_ENTRIES == 1000
    upstream = up()
    clock = {"t": 1000.0}
    monkeypatch.setattr(kyb, "_monotonic", lambda: clock["t"])
    post(client, "Acme")
    clock["t"] += 299
    post(client, "Acme")
    assert len(upstream.calls) == 1
    clock["t"] += 2
    post(client, "Acme")
    assert len(upstream.calls) == 2
    monkeypatch.setattr(ks, "CACHE_MAX_ENTRIES", 3)
    for q in ("aa", "bb", "cc", "dd", "ee"):
        kyb.reset_state_for_tests()
        post(client, q)
    assert len(ks._cache) == 3 and "aa" not in ks._cache and "ee" in ks._cache


# ------------------------------------------------------------------ 10. envelope


def _env(items_n, total):
    return envelope([item(i) for i in range(items_n)], total)


@pytest.mark.parametrize(
    "env,status,truncated,reported",
    [
        (_env(0, 0), 200, False, 0),
        (_env(0, 5), 502, None, None),
        (_env(0, MISSING), 502, None, None),
        (_env(0, -1), 502, None, None),
        (_env(0, True), 502, None, None),
        (_env(0, "0"), 502, None, None),
        (_env(3, MISSING), 200, True, None),
        (_env(3, 2), 502, None, None),
        (_env(3, -1), 502, None, None),
        (_env(3, True), 502, None, None),
        (_env(3, 3.0), 502, None, None),
        (_env(3, "3"), 502, None, None),
        (_env(3, None), 502, None, None),
        (_env(3, 3), 200, False, 3),
        (_env(3, 10), 200, True, 10),
        (
            _env(5, 7),
            200,
            True,
            7,
        ),  # fewer than 20 shown, larger total: truncated, not 502
        (_env(20, 356), 200, True, 356),
        (_env(20, 20), 200, False, 20),
        (_env(50, 50), 200, True, 50),
        (envelope(MISSING, 3), 502, None, None),
        (envelope(None, 3), 502, None, None),
        (envelope("not a list", 3), 502, None, None),
        (["not", "an", "object"], 502, None, None),
    ],
)
def test_envelope_rules_fail_closed(client, up, env, status, truncated, reported):
    up(env)
    r = post(client, "Acme")
    assert r.status_code == status
    if status == 200:
        body = r.json()
        assert body["truncated"] is truncated and body["reported_total"] == reported
        assert len(body["candidates"]) <= 20
    else:
        assert r.json() == {"detail": "Upstream error."}


# ------------------------------------------------------------------ 11. failure mapping


def test_404_gives_502_and_is_not_cached(client, up):
    upstream = up((404, {}))
    assert post(client, "Acme").status_code == 502
    assert not ks._cache
    assert post(client, "Acme").status_code == 502
    assert len(upstream.calls) == 2


@pytest.mark.parametrize(
    "result,status",
    [
        ((401, {}), 503),
        ((403, {}), 503),
        ((500, {}), 502),
        (requests.Timeout("slow"), 504),
        (requests.ConnectionError("down"), 502),
        (FakeResponse(200, None, bad_json=True), 502),
    ],
)
def test_upstream_failures_map_like_the_lookup(client, up, result, status):
    up(result)
    assert post(client, "Acme").status_code == status
    assert not ks._cache


@pytest.mark.parametrize(
    "bad",
    [
        item(0, company_number="1234"),
        item(0, company_number=12345678),
        item(0, company_number="ABCD123!"),
        item(0, company_number="ſ1234567"),
        item(0, title="x" * 501),
        item(0, title=5),
        item(0, company_status=5),
        item(0, date_of_creation=["2019"]),
    ],
)
def test_a_malformed_candidate_fails_the_whole_response(client, up, bad):
    up(envelope([item(1), bad], 2))
    assert post(client, "Acme").status_code == 502


def test_a_status_outside_the_documented_enum_is_passed_through(client, up):
    up(envelope([item(0, company_status="open")], 1))
    assert post(client, "Acme").json()["candidates"][0]["company_status"] == "open"


def test_a_missing_key_is_503_with_no_upstream_call(client, up, monkeypatch):
    upstream = up()
    monkeypatch.setattr(kyb, "COMPANIES_HOUSE_API_KEY", "")
    assert post(client, "Acme").status_code == 503
    assert upstream.calls == []


# ------------------------------------------------------------------ 12. hostile titles


def test_hostile_titles_are_returned_byte_for_byte(client, up):
    titles = [
        "<script>alert(1)</script>",
        '<img src=x onerror="alert(1)">',
        "quotes \" and ' and \\ and </script>",
        "ctrl \x00 \x1f \x7f chars",
        "A" * 500,
        "\u202eevil RLO \u2066 isolate \u2069",
    ]
    up(envelope([item(i, title=t) for i, t in enumerate(titles)], len(titles)))
    r = post(client, "Acme")
    assert r.status_code == 200
    assert [c["title"] for c in r.json()["candidates"]] == titles
    assert json.loads(r.text)["candidates"][0]["title"] == titles[0]


# ------------------------------------------------------------------ 13. burst first, then the body limit


def _nothing_but_burst(upstream):
    assert nothing_charged(upstream)
    assert burst_count(ASGI_CLIENT) == 1


@pytest.mark.parametrize(
    "headers,chunks",
    [
        ({"content-length": "5000"}, [b'{"q":"a"}']),  # declared over the limit
        ({}, [b"x" * 3000, b"x" * 3000]),  # no Content-Length
        ({"content-length": "10"}, [b'{"q":"' + b"a" * 5000 + b'"}']),  # understated
        ({"transfer-encoding": "chunked"}, [b"x" * 500] * 10),  # chunked or streamed
    ],
    ids=["declared", "absent", "understated", "chunked"],
)
def test_oversized_bodies_are_413_and_touch_nothing_but_the_burst_counter(
    up, capsys, headers, chunks
):
    upstream = up()
    status, _resp_headers, body = run(asgi(headers=headers, chunks=chunks))
    assert status == 413 and json.loads(body) == {"detail": ks.DETAIL_TOO_LARGE}
    assert b"xxx" not in body and b"aaa" not in body
    _nothing_but_burst(upstream)
    logs = capsys.readouterr()
    assert "xxx" not in logs.out + logs.err and "aaa" not in logs.out + logs.err


def test_the_limit_is_4096_bytes_exactly(up):
    upstream = up()
    base = b'{"q":"Acme Ltd"}'
    ok = base + b" " * (4096 - len(base))
    assert len(ok) == 4096
    status, _, _ = run(asgi(chunks=[ok]))
    assert status == 200 and len(upstream.calls) == 1
    status, _, _body = run(asgi(chunks=[ok + b" "]))
    assert status == 413


def test_an_oversized_flood_hits_429_at_the_burst_limit(up):
    upstream = up()
    codes = [
        run(asgi(headers={"content-length": "9999"}, chunks=[b"x"]))[0]
        for _ in range(11)
    ]
    assert codes == [413] * 10 + [429]
    assert nothing_charged(upstream)


def test_a_client_already_over_the_burst_limit_gets_429_for_an_oversized_body(up):
    up()
    for _ in range(10):
        kyb._ip_limited(ASGI_CLIENT)
    status, headers, _ = run(asgi(headers={"content-length": "9999"}, chunks=[b"x"]))
    assert status == 429 and headers["retry-after"] == str(kyb.IP_LIMIT_WINDOW_SECONDS)


def test_a_flood_of_422_and_405_reaches_429_the_same_way(client, up):
    up()
    for _ in range(5):
        assert client.get(PATH).status_code == 405
    for _ in range(5):
        assert post(client, "x").status_code == 422
    assert post(client, "Acme").status_code == 429


def test_other_routes_are_unaffected_by_the_limit(client):
    r = client.post(
        "/api/sar-sandbox/extract",
        content=b"x" * 6000,
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code != 413


# ------------------------------------------------------------------ 14. CORS


PREFLIGHT = {
    "Origin": ORIGIN,
    "Access-Control-Request-Method": "POST",
    "Access-Control-Request-Headers": "content-type",
}


def test_the_production_origin_can_preflight_post_with_content_type(client):
    r = client.options(PATH, headers=PREFLIGHT)
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ORIGIN
    assert "POST" in r.headers["access-control-allow-methods"]
    assert "content-type" in r.headers["access-control-allow-headers"].lower()


def test_a_disallowed_origin_gets_no_allow_origin(client, up):
    up()
    evil = {"Origin": "https://evil.example"}
    assert (
        "access-control-allow-origin"
        not in client.options(PATH, headers={**PREFLIGHT, **evil}).headers
    )
    assert (
        "access-control-allow-origin" not in post(client, "Acme", headers=evil).headers
    )


def test_a_preflight_consumes_no_burst_and_makes_no_upstream_call(client, up):
    upstream = up()
    for _ in range(10):
        assert client.options(PATH, headers=PREFLIGHT).status_code == 200
    assert burst_count(TC) == 0 and upstream.calls == []
    for i in range(10):
        assert post(client, f"Acme {i}", headers={"Origin": ORIGIN}).status_code == 200


def test_errors_for_an_allowed_origin_carry_allow_origin(client, up):
    up()
    assert (
        client.get(PATH, headers={"Origin": ORIGIN}).headers[
            "access-control-allow-origin"
        ]
        == ORIGIN
    )  # 405
    assert (
        post(client, "x", headers={"Origin": ORIGIN}).headers[
            "access-control-allow-origin"
        ]
        == ORIGIN
    )  # 422
    for _ in range(10):
        kyb._ip_limited(TC)
    r = post(client, "Acme", headers={"Origin": ORIGIN})
    assert r.status_code == 429 and r.headers["access-control-allow-origin"] == ORIGIN


# ------------------------------------------------------------------ 15. slow body


def test_the_default_deadline_is_five_seconds():
    assert ks.BODY_DEADLINE_SECONDS == 5 and ks.BODY_LIMIT_BYTES == 4096


def test_a_slow_body_times_out_with_408_and_touches_nothing_but_the_burst_counter(
    up, monkeypatch, capsys
):
    upstream = up()
    monkeypatch.setattr(ks, "BODY_DEADLINE_SECONDS", 0.3)
    status, headers, body = run(
        asgi(
            headers={"Origin": ORIGIN},
            chunks=[b'{"q":', b'"Zq Needle"}'],
            delays=[0, 0.8],
        )
    )
    assert status == 408 and json.loads(body) == {"detail": ks.DETAIL_TIMEOUT}
    assert headers["access-control-allow-origin"] == ORIGIN
    _nothing_but_burst(upstream)
    logs = capsys.readouterr()
    assert "Needle" not in logs.out + logs.err and "Needle" not in body.decode()


def test_the_same_body_inside_the_deadline_is_accepted(up, monkeypatch):
    upstream = up()
    monkeypatch.setattr(ks, "BODY_DEADLINE_SECONDS", 0.5)
    status, _, _ = run(asgi(chunks=[b'{"q":', b'"Acme Ltd"}'], delays=[0, 0.05]))
    assert status == 200 and len(upstream.calls) == 1


def test_a_flood_of_slow_bodies_reaches_429(up, monkeypatch):
    up()
    monkeypatch.setattr(ks, "BODY_DEADLINE_SECONDS", 0.05)
    codes = [run(asgi(chunks=[b"{", b"}"], delays=[0, 0.2]))[0] for _ in range(11)]
    assert codes == [408] * 10 + [429]


# ------------------------------------------------------------------ 16. compression


@pytest.mark.parametrize(
    "encoding", ["gzip", "deflate", "br", "GZIP", "gzip, identity"]
)
def test_a_non_identity_content_encoding_is_415_before_the_body_is_read(
    client, up, encoding
):
    upstream = up()
    r = client.post(
        PATH,
        content=gzip.compress(VALID_BODY),
        headers={
            "Content-Type": "application/json",
            "Content-Encoding": encoding,
            "Origin": ORIGIN,
        },
    )
    assert r.status_code == 415 and r.json() == {"detail": ks.DETAIL_ENCODING}
    assert r.headers["access-control-allow-origin"] == ORIGIN
    assert nothing_charged(upstream) and burst_count(TC) == 1


def test_identity_and_no_header_are_accepted(client, up):
    upstream = up()
    assert (
        client.post(
            PATH,
            content=VALID_BODY,
            headers={
                "Content-Type": "application/json",
                "Content-Encoding": "identity",
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            PATH,
            content=VALID_BODY.replace(b"Acme", b"Beta"),
            headers={"Content-Type": "application/json"},
        ).status_code
        == 200
    )
    assert len(upstream.calls) == 2


# ------------------------------------------------------------------ 17. order of the control


def test_17a_a_get_from_a_client_over_the_burst_limit_is_429_not_405(client, up):
    up()
    for _ in range(10):
        kyb._ip_limited(TC)
    assert client.get(PATH).status_code == 429


def test_17b_ten_gets_then_a_post_gives_429_on_the_post(client, up):
    up()
    assert [client.get(PATH).status_code for _ in range(10)] == [405] * 10
    assert post(client, "Acme").status_code == 429


def test_17c_ten_preflights_then_ten_searches_all_succeed(client, up):
    up()
    for _ in range(10):
        assert client.options(PATH, headers=PREFLIGHT).status_code == 200
    assert all(post(client, f"Acme {i}").status_code == 200 for i in range(10))


def test_17d_control_responses_for_an_allowed_origin_carry_allow_origin(
    up, monkeypatch
):
    up()
    monkeypatch.setattr(ks, "BODY_DEADLINE_SECONDS", 0.2)
    h = {"Origin": ORIGIN}
    cases = [
        (run(asgi(method="GET", headers=h, chunks=[b""])), 405),
        (run(asgi(headers={**h, "content-length": "9999"}, chunks=[b"x"])), 413),
        (
            run(asgi(headers={**h, "content-encoding": "gzip"}, chunks=[VALID_BODY])),
            415,
        ),
        (run(asgi(headers=h, chunks=[b"{", b"}"], delays=[0, 0.6])), 408),
    ]
    for (status, headers, _), expected in cases:
        assert status == expected, (status, expected)
        assert headers.get("access-control-allow-origin") == ORIGIN, expected
