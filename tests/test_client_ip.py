"""Spec section 8, item 16: trusted-hop client IP helper."""

import pytest
from starlette.requests import Request

from client_ip import (
    DEFAULT_TRUSTED_PROXY_HOPS,
    MAX_KEY_LENGTH,
    client_ip,
    trusted_proxy_hops,
)


def make_request(xff=None, host="198.51.100.9", cf=None):
    headers = []
    if cf is not None:
        headers.append((b"cf-connecting-ip", cf.encode()))
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (host, 1234) if host else None,
    }
    return Request(scope)


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    monkeypatch.delenv("TRUSTED_PROXY_HOPS", raising=False)


def test_default_hops_is_one():
    assert DEFAULT_TRUSTED_PROXY_HOPS == 1
    assert trusted_proxy_hops() == 1


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1", 1),
        ("2", 2),
        (" 3 ", 3),
        ("0", 0),
        ("", 1),
        ("abc", 1),
        ("-1", 1),
        ("1.5", 1),
    ],
)
def test_hops_parsing(monkeypatch, value, expected):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", value)
    assert trusted_proxy_hops() == expected


def test_nth_from_right_default_one_hop():
    assert client_ip(make_request("203.0.113.5")) == "203.0.113.5"
    assert client_ip(make_request("10.0.0.1, 203.0.113.5")) == "203.0.113.5"


def test_nth_from_right_two_hops(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    assert client_ip(make_request("1.1.1.1, 203.0.113.5, 10.0.0.2")) == "203.0.113.5"


@pytest.mark.parametrize(
    "fake", ["203.0.113.1", "203.0.113.2, 203.0.113.3", "evil, 10.0.0.1"]
)
def test_spoofed_prepended_entries_do_not_change_key(fake):
    real = "198.51.100.77"
    assert client_ip(make_request(f"{fake}, {real}")) == real
    assert client_ip(make_request(real)) == real


def test_spoofed_entries_with_two_hops(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    keys = {
        client_ip(make_request(f"192.0.2.{i}, 198.51.100.77, 10.0.0.2"))
        for i in range(1, 12)
    }
    assert keys == {"198.51.100.77"}


def test_falls_back_when_header_missing():
    assert client_ip(make_request(None)) == "198.51.100.9"
    assert client_ip(make_request("")) == "198.51.100.9"


def test_falls_back_when_too_few_entries(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "3")
    assert client_ip(make_request("203.0.113.5, 10.0.0.1")) == "198.51.100.9"


def test_zero_hops_ignores_header(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "0")
    assert client_ip(make_request("203.0.113.5")) == "198.51.100.9"


def test_no_client_and_no_header():
    assert client_ip(make_request(None, host=None)) == "unknown"


def test_empty_entries_are_ignored():
    assert client_ip(make_request("203.0.113.5, , ")) == "203.0.113.5"


def test_cf_connecting_ip_wins_when_present(monkeypatch):
    assert client_ip(make_request("203.0.113.5", cf="198.51.100.1")) == "198.51.100.1"
    assert client_ip(make_request(None, cf="198.51.100.1")) == "198.51.100.1"
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "0")
    assert client_ip(make_request("203.0.113.5", cf=" 198.51.100.1 ")) == "198.51.100.1"


def test_empty_cf_connecting_ip_falls_back_to_the_xff_rule():
    assert client_ip(make_request("10.0.0.1, 203.0.113.5", cf="  ")) == "203.0.113.5"
    assert client_ip(make_request(None, cf="")) == "198.51.100.9"


def test_spoofed_xff_is_irrelevant_when_cf_connecting_ip_is_set():
    keys = {
        client_ip(make_request(f"192.0.2.{i}, 198.51.100.77", cf="198.51.100.1"))
        for i in range(1, 12)
    }
    assert keys == {"198.51.100.1"}


# ------------------------------------------------------------------ spec v1.5: keys must parse as IPs


@pytest.mark.parametrize(
    "cf", ["not-an-ip", "1.2.3", "999.1.1.1", "a" * 46, "1.2.3.4" + " x" * 30, "::1%a"]
)
def test_invalid_or_overlong_cf_connecting_ip_falls_through_to_xff(cf):
    assert (
        client_ip(make_request(cf=cf, xff="198.51.100.1, 203.0.113.7")) == "203.0.113.7"
    )


@pytest.mark.parametrize("entry", ["garbage", "1.2.3", "b" * 100, "1.2.3.4:80"])
def test_invalid_xff_entry_falls_through_to_the_socket_address(entry):
    assert client_ip(make_request(xff=f"198.51.100.1, {entry}")) == "198.51.100.9"


def test_invalid_cf_and_invalid_xff_fall_through_to_the_socket_address():
    assert client_ip(make_request(cf="nope", xff="also-nope")) == "198.51.100.9"


def test_ipv6_is_normalised_and_whitespace_stripped():
    assert (
        client_ip(make_request(cf="  2001:0DB8:0000:0000:0000:0000:0000:0001 "))
        == "2001:db8::/64"
    )
    assert client_ip(make_request(xff="x, 2001:DB8::0:1")) == "2001:db8::/64"


def test_ten_thousand_forged_long_values_never_make_a_long_key():
    for i in range(10_000):
        junk = f"{i}" + "9" * (46 + i % 50)
        keys = {
            client_ip(make_request(cf=junk, xff=f"1.1.1.1, {junk}")),
            client_ip(make_request(cf=f"{junk}:{i}", xff=junk)),
        }
        assert all(len(k) <= MAX_KEY_LENGTH for k in keys)
        assert keys == {"198.51.100.9"}


# ------------------------------------------------------------------ spec v1.6: key normalisation


def test_ipv4_mapped_ipv6_keys_as_ipv4():
    assert client_ip(make_request(cf="::ffff:1.2.3.4")) == client_ip(
        make_request(cf="1.2.3.4")
    )
    assert client_ip(make_request(xff="x, ::FFFF:0102:0304")) == "1.2.3.4"
    assert client_ip(make_request(host="::ffff:1.2.3.4")) == "1.2.3.4"


def test_ipv6_keys_by_slash_64_prefix():
    a = client_ip(make_request(cf="2001:db8:1:2::1"))
    b = client_ip(make_request(cf="2001:db8:1:2:ffff:ffff:ffff:ffff"))
    c = client_ip(make_request(cf="2001:db8:1:3::1"))
    assert a == b == "2001:db8:1:2::/64"
    assert c == "2001:db8:1:3::/64" and c != a
    assert client_ip(make_request(xff="x, 2001:db8:1:2::9")) == a


def test_socket_fallback_is_normalised_the_same_way():
    assert client_ip(make_request(host="2001:db8:1:2::77")) == "2001:db8:1:2::/64"
    assert client_ip(make_request(host="198.51.100.9")) == "198.51.100.9"
    assert client_ip(make_request(host="testclient")) == "testclient"
    # an invalid header falls through to the normalised socket address
    assert client_ip(make_request(cf="nope", host="2001:db8::5")) == "2001:db8::/64"
