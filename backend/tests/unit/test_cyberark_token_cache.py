"""Caché en memoria del token OAuth2 de ISPSS por tenant. Ver
docs/specs/cyberark-token-cache.md."""

from __future__ import annotations

import httpx
from freezegun import freeze_time

from app.cyberark.auth import ISPSSAuth
from app.cyberark.client import CyberArkClient


def _transport(calls: list[str], *, expires_in: int = 3600, fail_first_api: bool = False):
    state = {"api_401_sent": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/platformtoken"):
            calls.append("token")
            return httpx.Response(
                200, json={"access_token": f"tok-{len(calls)}", "expires_in": expires_in}
            )
        calls.append(f"api:{request.headers.get('authorization')}")
        if fail_first_api and not state["api_401_sent"]:
            state["api_401_sent"] = True
            return httpx.Response(401)
        return httpx.Response(200, json={"ok": True})

    return httpx.MockTransport(handler)


def _client(transport, *, secret="s1", cache=600, identity="https://id.example"):  # noqa: S107
    auth = ISPSSAuth(identity, "cid", secret, cache_max_seconds=cache)
    c = CyberArkClient("https://pvwa.example", auth)
    c._http = httpx.AsyncClient(transport=transport, headers={"Accept": "application/json"})
    return c


async def test_reusa_el_token_entre_clientes_del_mismo_tenant():
    calls: list[str] = []
    t = _transport(calls)
    for _ in range(3):
        c = _client(t)
        await c.authenticate()
        await c.get("/API/Server")
        await c.close()
    assert calls.count("token") == 1
    assert all(x == "api:Bearer tok-1" for x in calls if x.startswith("api"))


async def test_credenciales_distintas_no_comparten_token():
    calls: list[str] = []
    t = _transport(calls)
    a = _client(t, secret="s1")
    b = _client(t, secret="s2")
    await a.authenticate()
    await b.authenticate()
    assert calls.count("token") == 2
    assert a._http.headers["authorization"] != b._http.headers["authorization"]


async def test_sin_cache_hace_login_siempre():
    calls: list[str] = []
    t = _transport(calls)
    for _ in range(2):
        c = _client(t, cache=0)
        await c.authenticate()
    assert calls.count("token") == 2


async def test_vence_segun_expires_in_menos_margen():
    calls: list[str] = []
    t = _transport(calls, expires_in=120)  # ttl efectivo = 60 s
    with freeze_time("2026-01-01 00:00:00") as frozen:
        await _client(t).authenticate()
        frozen.tick(30)
        await _client(t).authenticate()
        assert calls.count("token") == 1
        frozen.tick(40)
        await _client(t).authenticate()
        assert calls.count("token") == 2


async def test_401_fuerza_token_nuevo_y_reemplaza_el_cacheado():
    calls: list[str] = []
    t = _transport(calls, fail_first_api=True)
    c = _client(t)
    await c.authenticate()
    assert await c.get("/API/Server") == {"ok": True}
    assert calls.count("token") == 2
    # el siguiente cliente usa el token nuevo, no el rechazado
    c2 = _client(t)
    await c2.authenticate()
    assert calls.count("token") == 2
    assert c2._http.headers["authorization"] == "Bearer tok-3"
