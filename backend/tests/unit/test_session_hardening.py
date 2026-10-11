"""Refresh token rotativo de un solo uso en cookie HttpOnly, detección de
reutilización, vencimiento por inactividad y anti-reutilización de TOTP.
Ver docs/specs/done/session-hardening.md."""

from __future__ import annotations

import datetime as dt

import pyotp
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.auth import router as auth_router
from app.core import security
from app.db import get_session
from app.models import AuditLog, Operator, RefreshToken

PASSWORD = "una-contrasena-larga"
COOKIE = "pam_refresh"


@pytest_asyncio.fixture
async def ctx(session, monkeypatch):
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

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    return {"client": TestClient(app, base_url="https://testserver"), "op": op, "session": session}


def _login(c, op, code: str | None = None):
    code = code or pyotp.TOTP(op.totp_secret).now()
    return c.post("/auth/login", json={"username": "alex", "password": PASSWORD, "totp_code": code})


def _refresh(c, cookie: str | None = None, csrf: bool = True):
    headers = {"X-PAM-Refresh": "1"} if csrf else {}
    if cookie is not None:
        c.cookies.clear()
        c.cookies.set(COOKIE, cookie)
    return c.post("/auth/refresh", headers=headers)


def _age_used_at(rows, seconds: int) -> None:
    for r in rows:
        if r.used_at is not None:
            r.used_at = r.used_at - dt.timedelta(seconds=seconds)


def test_login_deja_refresh_en_cookie_httponly_y_no_en_el_body(ctx):
    r = _login(ctx["client"], ctx["op"])
    assert r.status_code == 200
    assert "refresh_token" not in r.json()
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=strict" in set_cookie and "secure" in set_cookie


def test_refresh_rota_la_cookie(ctx):
    c = ctx["client"]
    _login(c, ctx["op"])
    before = c.cookies.get(COOKIE)
    r = _refresh(c)
    assert r.status_code == 200 and r.json()["access_token"]
    assert c.cookies.get(COOKIE) != before


def test_refresh_exige_header_anti_csrf(ctx):
    c = ctx["client"]
    _login(c, ctx["op"])
    assert _refresh(c, csrf=False).status_code == 403


def test_refresh_sin_cookie_es_401(ctx):
    c = ctx["client"]
    assert _refresh(c).status_code == 401


async def test_reutilizar_un_refresh_ya_rotado_revoca_la_familia(ctx):
    c, session = ctx["client"], ctx["session"]
    _login(c, ctx["op"])
    viejo = c.cookies.get(COOKIE)
    assert _refresh(c).status_code == 200
    nuevo = c.cookies.get(COOKIE)

    # fuera de la ventana de gracia
    rows = (await session.execute(select(RefreshToken))).scalars().all()
    _age_used_at(rows, 120)
    await session.commit()

    assert _refresh(c, cookie=viejo).status_code == 401
    # la familia entera quedó revocada: tampoco sirve el token legítimo
    assert _refresh(c, cookie=nuevo).status_code == 401
    acciones = (await session.execute(select(AuditLog.action))).scalars().all()
    assert "auth.refresh.reuse" in acciones


def test_reutilizar_dentro_de_la_gracia_no_revoca(ctx):
    c = ctx["client"]
    _login(c, ctx["op"])
    viejo = c.cookies.get(COOKIE)
    assert _refresh(c).status_code == 200
    # otra pestaña con la cookie vieja, al mismo tiempo
    r = _refresh(c, cookie=viejo)
    assert r.status_code == 200, (r.json(), r.request.headers.get("cookie"))


async def test_refresh_vencido_por_inactividad(ctx):
    c, session = ctx["client"], ctx["session"]
    _login(c, ctx["op"])
    row = (await session.execute(select(RefreshToken))).scalar_one()
    row.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)
    await session.commit()
    r = _refresh(c)
    assert r.status_code == 401
    assert "inactividad" in r.json()["detail"]


async def test_la_rotacion_no_extiende_el_tope_absoluto(ctx):
    c, session = ctx["client"], ctx["session"]
    _login(c, ctx["op"])
    row = (await session.execute(select(RefreshToken))).scalar_one()
    # la sesión empezó hace casi 12 h
    row.family_started_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=11, minutes=59)
    await session.commit()
    assert _refresh(c).status_code == 200
    rows = (await session.execute(select(RefreshToken))).scalars().all()
    nuevo = max(rows, key=lambda r: r.issued_at)
    restante = nuevo.expires_at.replace(tzinfo=dt.UTC) - dt.datetime.now(dt.UTC)
    assert restante <= dt.timedelta(minutes=1, seconds=5)


def test_logout_current_corta_solo_esta_sesion(ctx):
    c, op = ctx["client"], ctx["op"]
    other = TestClient(c.app, base_url="https://testserver")
    a = _login(c, op).json()
    # segunda sesión: otro código (el del paso anterior ya se consumió)
    op.totp_last_step = None
    b = _login(other, op)
    assert b.status_code == 200

    r = c.post(
        "/auth/logout?scope=current", headers={"Authorization": f"Bearer {a['access_token']}"}
    )
    assert r.status_code == 200
    assert _refresh(c, cookie=None).status_code == 401
    assert _refresh(other).status_code == 200


def test_mismo_codigo_totp_no_sirve_dos_veces(ctx):
    c, op = ctx["client"], ctx["op"]
    code = pyotp.TOTP(op.totp_secret).now()
    assert _login(c, op, code).status_code == 200
    r = _login(c, op, code)
    assert r.status_code == 401
    assert "TOTP" in r.json()["detail"]


async def _ultimo_refresh_rechazado(session) -> AuditLog:
    rows = (
        (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.action == "auth.refresh", AuditLog.result == "error")
                .order_by(AuditLog.id.desc())
            )
        )
        .scalars()
        .all()
    )
    return rows[0]


async def test_refresh_sin_cookie_se_audita_como_sin_sesion(ctx):
    """Antes quedaba con operador "?": ahora dice qué es."""
    assert _refresh(ctx["client"]).status_code == 401
    row = await _ultimo_refresh_rechazado(ctx["session"])
    assert row.operator == "(sin sesión)"
    assert row.detail == {"motivo": "sin cookie"}


async def test_refresh_con_token_vencido_se_atribuye_a_su_dueno(ctx):
    """Un refresh vencido pero con firma válida de PAM Web Console dice de quién es."""
    vencido = security._encode(
        ctx["op"].id, 0, typ="refresh", ttl=dt.timedelta(seconds=-60), extra={"jti": "x"}
    )
    assert _refresh(ctx["client"], cookie=vencido).status_code == 401
    row = await _ultimo_refresh_rechazado(ctx["session"])
    assert row.operator == "alex"
    assert "inválido o expirado" in row.detail["motivo"]


async def test_refresh_con_token_falsificado_no_se_atribuye_a_nadie(ctx):
    assert _refresh(ctx["client"], cookie="no.es.un-jwt").status_code == 401
    row = await _ultimo_refresh_rechazado(ctx["session"])
    assert row.operator == "(sin sesión)"
