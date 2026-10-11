"""Helpers puros de app.cyberark.client + regresión de un bug real
(2026-09-23): `accounts_page`/`safes_page` asumían que la respuesta
siempre era un dict y explotaban con `AttributeError` si CyberArk
devolvía un body vacío en una 2xx -- se disparó de verdad corriendo el
sync nocturno contra un tenant real (ver docs/specs/jobs-programados.md).
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from app.cyberark.client import CyberArkClient, accounts_filter
from app.cyberark.errors import UpstreamError


class _Auth:
    async def login(self, client):  # pragma: no cover - no se usa
        return "t"

    async def logout(self, client):  # pragma: no cover
        return None


class _EmptyResp:
    """Simula una 200 con body vacío -- `_request` la traduce a `None`."""

    status_code = 200
    content = b""
    text = ""
    headers: ClassVar[dict] = {}


def _client() -> CyberArkClient:
    c = CyberArkClient("https://pvwa", _Auth(), max_concurrency=2)
    c._authed = True
    return c


async def test_accounts_page_body_vacio_levanta_upstream_error(monkeypatch):
    c = _client()

    async def fake_request(method, url, **kw):
        return _EmptyResp()

    monkeypatch.setattr(c._http, "request", fake_request)
    with pytest.raises(UpstreamError):
        await c.accounts_page()


async def test_safes_page_body_vacio_levanta_upstream_error(monkeypatch):
    c = _client()

    async def fake_request(method, url, **kw):
        return _EmptyResp()

    monkeypatch.setattr(c._http, "request", fake_request)
    with pytest.raises(UpstreamError):
        await c.safes_page()


def test_accounts_filter_sin_nada():
    assert accounts_filter(None, None) is None
    assert accounts_filter("", 0) is None


def test_accounts_filter_solo_safe():
    assert accounts_filter("SQL-PROD", None) == "safeName eq SQL-PROD"


def test_accounts_filter_solo_fecha():
    assert accounts_filter(None, 1704067200) == "modificationTime gt 1704067200"


def test_accounts_filter_combinado():
    assert (
        accounts_filter("SQL-PROD", 1704067200)
        == "safeName eq SQL-PROD AND modificationTime gt 1704067200"
    )
