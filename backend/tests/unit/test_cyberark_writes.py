"""Política de reintentos del cliente CyberArk para escrituras (retry_safe)."""

from __future__ import annotations

import httpx
import pytest

from app.cyberark.client import CyberArkClient
from app.cyberark.errors import UpstreamError


class _Auth:
    async def login(self, client):  # pragma: no cover - no se usa
        return "t"

    async def logout(self, client):  # pragma: no cover
        return None


def _client() -> CyberArkClient:
    c = CyberArkClient("https://pvwa", _Auth(), max_concurrency=2)
    c._authed = True
    return c


class _Resp:
    def __init__(self, status: int, body: dict | None = None):
        self.status_code = status
        self._body = body or {}
        self.content = b"{}"
        self.text = "{}"

    def json(self):
        return self._body


async def test_retry_safe_true_reintenta_5xx(monkeypatch):
    c = _client()
    calls = {"n": 0}

    async def fake_request(method, url, **kw):
        calls["n"] += 1
        return _Resp(503) if calls["n"] < 3 else _Resp(200, {"ok": True})

    monkeypatch.setattr(c._http, "request", fake_request)
    monkeypatch.setattr("asyncio.sleep", lambda *_: _noop())
    assert await c.get("/API/x") == {"ok": True}
    assert calls["n"] == 3


async def test_retry_safe_false_no_reintenta_5xx(monkeypatch):
    c = _client()
    calls = {"n": 0}

    async def fake_request(method, url, **kw):
        calls["n"] += 1
        return _Resp(503)

    monkeypatch.setattr(c._http, "request", fake_request)
    with pytest.raises(UpstreamError):
        await c.post("/API/Accounts", json={})
    assert calls["n"] == 1  # una sola llamada, sin reintentos


async def test_retry_safe_false_no_reintenta_timeout(monkeypatch):
    c = _client()
    calls = {"n": 0}

    async def fake_request(method, url, **kw):
        calls["n"] += 1
        raise httpx.ConnectTimeout("boom")

    monkeypatch.setattr(c._http, "request", fake_request)
    with pytest.raises(UpstreamError, match="pudo haberse ejecutado"):
        await c.post("/API/Accounts", json={})
    assert calls["n"] == 1


async def test_update_account_manda_json_patch(monkeypatch):
    c = _client()
    seen = {}

    async def fake_request(method, url, **kw):
        seen["method"] = method
        seen["json"] = kw.get("json")
        return _Resp(200, {"id": "42", "name": "nuevo"})

    monkeypatch.setattr(c._http, "request", fake_request)
    patch = [{"op": "replace", "path": "/name", "value": "nuevo"}]
    out = await c.update_account("42", patch)
    assert seen["method"] == "PATCH"
    assert seen["json"] == patch
    assert out["name"] == "nuevo"


async def _noop():
    return None
