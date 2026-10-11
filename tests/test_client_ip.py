"""Spec section 8, item 16: trusted-hop client IP helper."""

import pytest
from starlette.requests import Request

from client_ip import DEFAULT_TRUSTED_PROXY_HOPS, client_ip, trusted_proxy_hops


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
