"""Tests de app.core.security: Argon2, TOTP y tokens JWT."""

from __future__ import annotations

import datetime as dt

import pyotp
import pytest
from freezegun import freeze_time

from app.core import security


def test_hash_password_roundtrip():
    h = security.hash_password("una-contrasena-larga")
    assert h != "una-contrasena-larga"
    assert security.verify_password("una-contrasena-larga", h) is True
    assert security.verify_password("otra-cosa", h) is False


def test_password_policy():
    assert security.password_policy_error("corta") is not None
    assert security.password_policy_error("x" * 12) is None


def test_totp_verify():
    secret = security.new_totp_secret()
    code = pyotp.TOTP(secret).now()
    assert security.verify_totp(secret, code) is True
    assert security.verify_totp(secret, "000000") is False
    assert security.verify_totp(secret, "") is False


def test_access_token_roundtrip():
    token = security.make_token(42, 0)
    assert security.read_token(token) == (42, 0)


def test_access_token_lleva_token_version():
    token = security.make_token(42, 3)
    assert security.read_token(token) == (42, 3)


def test_refresh_token_no_sirve_como_access():
    refresh = security.make_refresh_token(7, 0, "abc", dt.timedelta(minutes=5))
    assert security.read_refresh_token(refresh) == (7, 0, "abc")
    with pytest.raises(ValueError):
        security.read_token(refresh)


def test_access_token_no_sirve_como_refresh():
    access = security.make_token(7, 0)
    with pytest.raises(ValueError):
        security.read_refresh_token(access)


def test_token_firma_invalida():
    token = security.make_token(1, 0)
    with pytest.raises(ValueError):
        security.read_token(token + "tampered")


def test_access_token_expira():
    with freeze_time("2026-01-01 00:00:00"):
        token = security.make_token(99, 0)
    with freeze_time("2026-01-01 00:16:00"), pytest.raises(ValueError):  # jwt_ttl_minutes = 15
        security.read_token(token)


def test_iat_exp_coherentes():
    with freeze_time("2026-01-01 00:00:00"):
        token = security.make_token(1, 0)
    import jwt

    payload = jwt.decode(token, options={"verify_signature": False})
    delta = payload["exp"] - payload["iat"]
    assert delta == int(dt.timedelta(minutes=15).total_seconds())


def test_token_sin_claim_tv_se_trata_como_0():
    """Compatibilidad con tokens emitidos antes de sumar `tv` (ver
    docs/specs/done/login-hardening.md) -- no debe invalidar sesiones activas al
    desplegar este cambio."""
    import jwt

    from app.config import get_settings

    s = get_settings()
    now = dt.datetime.now(dt.UTC)
    legacy_token = jwt.encode(
        {
            "sub": "5",
            "typ": "access",
            "iat": now,
            "exp": now + dt.timedelta(hours=1),
            "iss": security.JWT_ISSUER,
            "aud": security.JWT_AUDIENCE,
        },
        s.secret_key,
        algorithm="HS256",
    )
    assert security.read_token(legacy_token) == (5, 0)


def test_token_con_audience_incorrecta_es_invalido():
    import jwt

    from app.config import get_settings

    s = get_settings()
    now = dt.datetime.now(dt.UTC)
    token = jwt.encode(
        {
            "sub": "1",
            "tv": 0,
            "typ": "access",
            "iat": now,
            "exp": now + dt.timedelta(hours=1),
            "iss": security.JWT_ISSUER,
            "aud": "otra-app",
        },
        s.secret_key,
        algorithm="HS256",
    )
    with pytest.raises(ValueError):
        security.read_token(token)


def test_totp_step_rechaza_codigo_ya_usado():
    secret = security.new_totp_secret()
    now = 1_800_000_000.0
    code = pyotp.TOTP(secret).at(now)
    step = security.verify_totp_step(secret, code, None, now=now)
    assert step == int(now // 30)
    # el mismo código, con ese paso ya consumido, no vuelve a pasar
    assert security.verify_totp_step(secret, code, step, now=now) is None
    # el código del paso siguiente sí
    nxt = pyotp.TOTP(secret).at(now + 30)
    assert security.verify_totp_step(secret, nxt, step, now=now + 30) == step + 1


def test_totp_step_tolera_un_paso_de_desfase():
    secret = security.new_totp_secret()
    now = 1_800_000_000.0
    prev = pyotp.TOTP(secret).at(now - 30)
    assert security.verify_totp_step(secret, prev, None, now=now) == int(now // 30) - 1
    viejo = pyotp.TOTP(secret).at(now - 90)
    assert security.verify_totp_step(secret, viejo, None, now=now) is None


def test_refresh_token_sin_jti_es_rechazado():
    legacy = security._encode(7, 0, typ="refresh", ttl=dt.timedelta(minutes=5))
    with pytest.raises(ValueError):
        security.read_refresh_token(legacy)
