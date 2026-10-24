"""Kill switch for /api/screen: fail closed, no upstream call, no rate limit bookkeeping."""

import importlib

import pytest
from fastapi.testclient import TestClient

import main

DETAIL = "Screening is temporarily unavailable while the service is under maintenance."


class ExplodingEngine:
    def screen(self, *args, **kwargs):
        raise AssertionError("upstream screen() called while screening is disabled")

    def search(self, *args, **kwargs):
        raise AssertionError("upstream search() called while screening is disabled")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "screening_engine", ExplodingEngine())
    monkeypatch.setattr(main, "adverse_engine", ExplodingEngine())
    main._screen_request_log.clear()
    return TestClient(main.app)


def test_disabled_when_env_unset(monkeypatch):
    monkeypatch.delenv("SCREENING_ENABLED", raising=False)
    try:
        assert importlib.reload(main).SCREENING_ENABLED is False
    finally:
        importlib.reload(main)


@pytest.mark.parametrize("kind", ["all", "sanctions", "pep", "adverse"])
def test_screen_returns_503_without_upstream_call(client, kind):
    r = client.get("/api/screen", params={"q": "test", "type": kind})
    assert r.status_code == 503
    assert r.headers["Retry-After"] == "86400"
    assert r.json() == {"detail": DETAIL, "maintenance": True}


def test_disabled_requests_skip_rate_limiter(client):
    for _ in range(main.SCREEN_RATE_LIMIT_MAX + 5):
        assert client.get("/api/screen", params={"q": "test"}).status_code == 503
    assert main._screen_request_log == {}


def test_health_reports_screening_disabled(client):
    assert client.get("/api/health").json()["screening_enabled"] is False


def _startup_log(monkeypatch, capsys, enabled, key):
    import asyncio

    monkeypatch.setattr(main, "SCREENING_ENABLED", enabled)
    monkeypatch.setattr(main, "OPENSANCTIONS_API_KEY", key)
    asyncio.run(main.startup())
    return capsys.readouterr().out


def test_startup_disabled_with_key_says_disabled_not_ready(monkeypatch, capsys):
    out = _startup_log(monkeypatch, capsys, False, "k")
    assert "DISABLED" in out
    assert "ready" not in out


def test_startup_enabled_with_key_says_ready(monkeypatch, capsys):
    assert "screening ready" in _startup_log(monkeypatch, capsys, True, "k")


def test_startup_enabled_without_key_warns_unavailable(monkeypatch, capsys):
    out = _startup_log(monkeypatch, capsys, True, "")
    assert "WARNING: OPENSANCTIONS_API_KEY is not set" in out
    assert "sanctions/PEP screening will return no results" in out
    assert "ready" not in out


def test_status_disabled_does_not_claim_live(client):
    data = client.get("/api/status").json()["data"]
    assert data["screening_enabled"] is False
    assert "live per-request" not in data["screening_backend"]


def test_status_enabled_reports_live(client, monkeypatch):
    monkeypatch.setattr(main, "SCREENING_ENABLED", True)
    data = client.get("/api/status").json()["data"]
    assert data["screening_enabled"] is True
    assert "live per-request" in data["screening_backend"]
