"""token_version: invalidar sesiones sin esperar la expiración natural del
JWT (logout, cambio de contraseña). Ver docs/specs/done/login-hardening.md."""

from __future__ import annotations

import pyotp
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import router as auth_router
from app.core import security
from app.db import get_session
from app.models import Operator

PASSWORD = "una-contrasena-larga"


@pytest_asyncio.fixture
async def ctx(session, monkeypatch):
    # el test client pega a /auth/* (sin el prefijo /api que agrega nginx)
    monkeypatch.setenv("PAM_REFRESH_COOKIE_PATH", "/auth")
    from app.config import get_settings

    get_settings.cache_clear()
    op = Operator(
        username="alex",
        password_hash=security.hash_password(PASSWORD),
        totp_enrolled=True,
        totp_secret=security.new_totp_secret(),
        is_active=True,
    )
    session.add(op)
    await session.flush()

    app = FastAPI()
    app.include_router(auth_router)
    app.state.totp_secret = op.totp_secret

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    client = TestClient(app, base_url="https://testserver")

    access = security.make_token(op.id, op.token_version)
    return {"client": client, "op": op, "session": session, "access": access}


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_logout_invalida_el_access_token_actual(ctx):
    c, access = ctx["client"], ctx["access"]
    assert c.get("/auth/me", headers=_auth(access)).status_code == 200
    r = c.post("/auth/logout", headers=_auth(access))
    assert r.status_code == 200
    assert c.get("/auth/me", headers=_auth(access)).status_code == 401


def test_logout_invalida_todas_las_sesiones_no_solo_la_actual(ctx):
    c, op = ctx["client"], ctx["op"]
    otra_sesion = security.make_token(op.id, op.token_version)
    r = c.post("/auth/logout", headers=_auth(ctx["access"]))
    assert r.status_code == 200
    assert c.get("/auth/me", headers=_auth(otra_sesion)).status_code == 401


def test_cambio_de_password_invalida_sesiones_viejas_pero_devuelve_una_nueva(ctx):
    c, access = ctx["client"], ctx["access"]
    r = c.post(
        "/auth/change-password",
        headers=_auth(access),
        json={"current_password": PASSWORD, "new_password": "otra-contrasena-larga-2"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body
    # el refresh token viaja sólo en la cookie HttpOnly, nunca en el body
    assert "refresh_token" not in body
    assert c.cookies.get("pam_refresh")

    # el token viejo (emitido antes del cambio) ya no sirve
    assert c.get("/auth/me", headers=_auth(access)).status_code == 401
    # el token nuevo que devolvió la respuesta sí
    assert c.get("/auth/me", headers=_auth(body["access_token"])).status_code == 200


def test_refresh_despues_de_logout_es_rechazado(ctx):
    c = ctx["client"]
    login = _login(c)
    assert _refresh(c).status_code == 200
    c.post("/auth/logout", headers=_auth(login["access_token"]))
    assert _refresh(c).status_code == 401


def _login(c) -> dict:
    code = pyotp.TOTP(ctx_secret(c)).now()
    r = c.post("/auth/login", json={"username": "alex", "password": PASSWORD, "totp_code": code})
    assert r.status_code == 200, r.text
    return r.json()


def ctx_secret(c) -> str:
    return c.app.state.totp_secret


def _refresh(c):
    return c.post("/auth/refresh", headers={"X-PAM-Refresh": "1"})
