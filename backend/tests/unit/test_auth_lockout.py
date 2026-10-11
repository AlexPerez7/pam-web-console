"""Anti-fuerza-bruta de /auth/login (R4 de hardening-pre-escritura).

El contador de fallos vive en Redis; acá se sustituye por un dict en memoria
(`fake_cache`) para probar la lógica sin Redis.
"""

from __future__ import annotations

import pyotp
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import router as auth_router
from app.core import cache, security
from app.db import get_session
from app.models import Operator

PASSWORD = "una-contrasena-larga"


@pytest.fixture
def fake_cache(monkeypatch: pytest.MonkeyPatch) -> dict:
    store: dict[str, int] = {}

    async def get_int(k: str) -> int | None:
        return store.get(k)

    async def incr_with_ttl(k: str, ttl: int) -> int:
        store[k] = store.get(k, 0) + 1
        return store[k]

    async def delete(k: str) -> None:
        store.pop(k, None)

    monkeypatch.setattr(cache, "get_int", get_int)
    monkeypatch.setattr(cache, "incr_with_ttl", incr_with_ttl)
    monkeypatch.setattr(cache, "delete", delete)
    return store


@pytest_asyncio.fixture
async def ctx(session, fake_cache):
    secret = security.new_totp_secret()
    session.add(
        Operator(
            username="alex",
            password_hash=security.hash_password(PASSWORD),
            totp_secret=secret,
            totp_enrolled=True,
            is_active=True,
        )
    )
    await session.flush()

    app = FastAPI()
    app.include_router(auth_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    return {"client": TestClient(app), "secret": secret, "store": fake_cache}


def test_lockout_tras_5_fallos(ctx):
    c = ctx["client"]
    for _ in range(5):
        assert (
            c.post("/auth/login", json={"username": "alex", "password": "mal"}).status_code == 401
        )
    r = c.post("/auth/login", json={"username": "alex", "password": "mal"})
    assert r.status_code == 429
    assert r.headers.get("Retry-After")
    # ni con la contraseña correcta entra mientras dura el lockout
    r = c.post("/auth/login", json={"username": "alex", "password": PASSWORD})
    assert r.status_code == 429


def test_usuario_inexistente_tambien_cuenta(ctx):
    c = ctx["client"]
    for _ in range(5):
        c.post("/auth/login", json={"username": "fantasma", "password": "x"})
    r = c.post("/auth/login", json={"username": "fantasma", "password": "x"})
    assert r.status_code == 429


def _fail_keys(store: dict) -> dict:
    """El store del fake_cache también acumula el contador global por IP
    (`pam:login:attempts:ip:*`, ver docs/specs/done/login-hardening.md), que no
    se borra en un login exitoso -- estos tests sólo verifican el contador
    de fallos por (usuario, IP)."""
    return {k: v for k, v in store.items() if k.startswith("pam:login:fail:")}


def test_login_exitoso_limpia_el_contador(ctx):
    c = ctx["client"]
    for _ in range(3):
        c.post("/auth/login", json={"username": "alex", "password": "mal"})
    code = pyotp.TOTP(ctx["secret"]).now()
    r = c.post("/auth/login", json={"username": "alex", "password": PASSWORD, "totp_code": code})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert _fail_keys(ctx["store"]) == {}  # contador borrado
    # y se puede volver a fallar sin quedar bloqueado de entrada
    assert c.post("/auth/login", json={"username": "alex", "password": "mal"}).status_code == 401


def test_password_correcta_sin_totp_no_cuenta_como_fallo(ctx):
    c = ctx["client"]
    r = c.post("/auth/login", json={"username": "alex", "password": PASSWORD})
    assert r.status_code == 200
    assert r.json()["status"] == "totp_required"
    assert _fail_keys(ctx["store"]) == {}


def test_lockout_global_por_ip_independiente_del_usuario(ctx, monkeypatch):
    """Muchos usernames distintos desde la misma IP disparan el techo global
    por IP, aunque cada combinación (usuario, IP) individual nunca llegue a
    `login_max_fails`."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "login_ip_max_attempts", 3)
    c = ctx["client"]
    for i in range(3):
        r = c.post("/auth/login", json={"username": f"fantasma-{i}", "password": "x"})
        assert r.status_code == 401
    r = c.post("/auth/login", json={"username": "otro-mas", "password": "x"})
    assert r.status_code == 429
    assert r.headers.get("Retry-After")
