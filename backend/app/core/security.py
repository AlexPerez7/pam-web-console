"""
Primitivas de auth de operadores: hashing de contraseñas (Argon2), TOTP
(pyotp) y tokens de sesión (JWT HS256 firmado con PAM_SECRET_KEY).

Sin dependencias de FastAPI ni de la DB — eso vive en app.core.deps.
"""

from __future__ import annotations

import datetime as _dt
import hmac
import secrets
import time

import jwt
import pyotp
import segno
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.config import get_settings

_ph = PasswordHasher()

# hash de referencia para igualar el timing cuando el usuario no existe
# (evita enumeración de usuarios por diferencia de tiempo de respuesta)
DUMMY_HASH = _ph.hash("pam-dummy-password")


# --- contraseñas ---------------------------------------------------------- #
MIN_PASSWORD_LEN = 12


def password_policy_error(pw: str) -> str | None:
    """Devuelve un mensaje si la contraseña no cumple la política, o None."""
    if len(pw) < MIN_PASSWORD_LEN:
        return f"la contraseña debe tener al menos {MIN_PASSWORD_LEN} caracteres"
    return None


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        _ph.verify(hashed, pw)
        return True
    except VerifyMismatchError:
        return False


# --- TOTP --------------------------------------------------------------- #
def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name="PAM Web Console")


def totp_qr_data_uri(secret: str, username: str) -> str:
    """QR del `otpauth://` como data URI SVG, para escanear en el authenticator."""
    return segno.make(totp_uri(secret, username), error="m").svg_data_uri(scale=5, border=2)


TOTP_STEP_SECONDS = 30


def verify_totp_step(
    secret: str, code: str, last_step: int | None = None, *, now: float | None = None
) -> int | None:
    """Valida `code` con tolerancia de ±1 paso (30 s) y devuelve el time-step
    que matcheó, o None. Un paso <= `last_step` (el último aceptado para este
    operador) se rechaza aunque el código sea correcto: evita reutilizar un
    código interceptado dentro de su ventana de validez."""
    if not code or not secret:
        return None
    code = code.strip().replace(" ", "")
    totp = pyotp.TOTP(secret)
    current = int((time.time() if now is None else now) // TOTP_STEP_SECONDS)
    for step in (current - 1, current, current + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(totp.generate_otp(step), code):
            return step
    return None


def verify_totp(secret: str, code: str) -> bool:
    """Validación sin estado (sin anti-reutilización). El login usa
    `verify_totp_step`."""
    return verify_totp_step(secret, code) is not None


# --- tokens de sesión ---------------------------------------------------- #
JWT_ISSUER = "pam"
JWT_AUDIENCE = "pam-web-console-api"


def _encode(
    operator_id: int,
    token_version: int,
    *,
    typ: str,
    ttl: _dt.timedelta,
    extra: dict | None = None,
) -> str:
    s = get_settings()
    now = _dt.datetime.now(_dt.UTC)
    payload = {
        "sub": str(operator_id),
        "tv": token_version,
        "typ": typ,
        "iat": now,
        "exp": now + ttl,
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, s.secret_key, algorithm="HS256")


def make_token(operator_id: int, token_version: int) -> str:
    """Access token (JWT corto). Header: Authorization: Bearer <jwt>."""
    return _encode(
        operator_id,
        token_version,
        typ="access",
        ttl=_dt.timedelta(minutes=get_settings().jwt_ttl_minutes),
    )


def new_jti() -> str:
    return secrets.token_hex(16)


def make_refresh_token(operator_id: int, token_version: int, jti: str, ttl: _dt.timedelta) -> str:
    """Refresh token con `jti` -- el estado (usado/revocado/familia) vive en
    la tabla `refresh_tokens`, el JWT sólo lo identifica."""
    return _encode(operator_id, token_version, typ="refresh", ttl=ttl, extra={"jti": jti})


def _decode(token: str) -> dict:
    return jwt.decode(
        token,
        get_settings().secret_key,
        algorithms=["HS256"],
        issuer=JWT_ISSUER,
        audience=JWT_AUDIENCE,
    )


def read_token(token: str) -> tuple[int, int]:
    """(operator_id, token_version) de un ACCESS token. ValueError si es
    inválido/expirado o es un refresh token. Tokens emitidos antes de sumar
    el claim "tv" (ver docs/specs/done/login-hardening.md) lo tratan como 0, para
    no invalidar sesiones activas al desplegar este cambio."""
    try:
        payload = _decode(token)
        if payload.get("typ") == "refresh":
            raise ValueError("se esperaba un access token")
        return int(payload["sub"]), int(payload.get("tv", 0))
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise ValueError("token inválido o expirado")


def refresh_token_owner(token: str) -> int | None:
    """operator_id de un refresh token aunque esté vencido, SÓLO para
    atribuir en el audit log un intento de renovación rechazado (no da
    acceso a nada). La firma se verifica igual: un token falsificado no
    se atribuye a nadie."""
    try:
        payload = jwt.decode(
            token,
            get_settings().secret_key,
            algorithms=["HS256"],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
            options={"verify_exp": False},
        )
        if payload.get("typ") != "refresh":
            return None
        return int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None


def read_refresh_token(token: str) -> tuple[int, int, str]:
    """(operator_id, token_version, jti) de un REFRESH token. ValueError si es
    inválido/expirado, no es un refresh token o no trae `jti` (emitido antes
    de la rotación de un solo uso -- se exige volver a iniciar sesión)."""
    try:
        payload = _decode(token)
        if payload.get("typ") != "refresh":
            raise ValueError("se esperaba un refresh token")
        jti = payload.get("jti")
        if not isinstance(jti, str) or not jti:
            raise ValueError("refresh token sin jti")
        return int(payload["sub"]), int(payload.get("tv", 0)), jti
    except (jwt.InvalidTokenError, KeyError, ValueError):
        raise ValueError("refresh token inválido o expirado")
