"""
Login de operadores: contraseña Argon2 + TOTP obligatorio.

Primer login (operador sin TOTP): manda solo username+password -> el server
genera el secret, lo guarda y devuelve el `otpauth://` URI. El operador lo
escanea y vuelve a llamar con `totp_code` -> queda enrolado y recibe la
sesión. El admin nunca ve el secret.
"""

from __future__ import annotations

import datetime as dt
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import audit, cache, security
from app.core.deps import current_operator
from app.db import get_session
from app.models import Operator, RefreshToken

router = APIRouter(prefix="/auth", tags=["auth"])


# operador de un intento de auth que no se puede atribuir a nadie: no trae
# cookie, o el token no tiene una firma válida de PAM Web Console (antes "?").
ANON_OPERATOR = "(sin sesión)"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


async def _audit_auth(
    session: AsyncSession,
    *,
    action: str,
    operator: str,
    result: str,
    ip: str,
    detail: dict | None = None,
) -> None:
    """Registra un evento de auth (login/refresh). Nunca guarda la contraseña
    ni el código TOTP."""
    await audit.record(
        session,
        operator=operator or ANON_OPERATOR,
        role="anon",
        action=action,
        result=result,
        source_ip=ip,
        detail=detail or {},
    )
    await session.commit()


def _fail_key(username: str, ip: str) -> str:
    return f"pam:login:fail:{username}:{ip}"


def _ip_attempts_key(ip: str) -> str:
    return f"pam:login:attempts:ip:{ip}"


class LoginIn(BaseModel):
    username: str
    password: str
    totp_code: str | None = None


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


def _operator_dict(op: Operator) -> dict:
    return {
        "id": op.id,
        "username": op.username,
        "display_name": op.display_name,
        "email": op.email,
        "role": op.role.value,
        "must_change_password": op.must_change_password,
    }


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _aware(value: dt.datetime) -> dt.datetime:
    """SQLite (tests) devuelve datetimes naive; Postgres, aware. Todo es UTC."""
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def _set_refresh_cookie(response: Response, token: str, expires_at: dt.datetime) -> None:
    s = get_settings()
    response.set_cookie(
        s.refresh_cookie_name,
        token,
        expires=expires_at,
        path=s.refresh_cookie_path,
        secure=s.refresh_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _clear_refresh_cookie(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(
        s.refresh_cookie_name,
        path=s.refresh_cookie_path,
        secure=s.refresh_cookie_secure,
        httponly=True,
        samesite="strict",
    )


async def _issue_session(
    session: AsyncSession,
    op: Operator,
    response: Response,
    *,
    family_id: str | None = None,
    family_started_at: dt.datetime | None = None,
) -> dict:
    """Emite un access token (va en el body, el frontend lo guarda sólo en
    memoria) y un refresh token nuevo (cookie HttpOnly, de un solo uso).
    Sin `family_id` arranca una sesión nueva (login / cambio de contraseña);
    con `family_id` es una rotación dentro de la misma sesión. Ver
    docs/specs/done/session-hardening.md."""
    s = get_settings()
    now = _now()
    started = family_started_at or now
    hard_limit = started + dt.timedelta(hours=s.session_max_hours)
    expires_at = min(now + dt.timedelta(minutes=s.session_idle_minutes), hard_limit)
    jti = security.new_jti()
    session.add(
        RefreshToken(
            jti=jti,
            operator_id=op.id,
            family_id=family_id or security.new_jti(),
            family_started_at=started,
            issued_at=now,
            expires_at=expires_at,
        )
    )
    # limpieza oportunista: filas de este operador vencidas hace más de un día
    await session.execute(
        delete(RefreshToken)
        .where(
            RefreshToken.operator_id == op.id,
            RefreshToken.expires_at < now - dt.timedelta(days=1),
        )
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    token = security.make_refresh_token(op.id, op.token_version, jti, expires_at - now)
    _set_refresh_cookie(response, token, expires_at)
    return {
        "status": "ok",
        "access_token": security.make_token(op.id, op.token_version),
        "token_type": "bearer",
        "expires_in": s.jwt_ttl_minutes * 60,
        "operator": _operator_dict(op),
    }


async def _revoke_family(session: AsyncSession, family_id: str) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


# header obligatorio en /auth/refresh: un formulario o <img> de otro sitio no
# puede mandarlo sin un preflight CORS (que la API no autoriza) -- defensa en
# profundidad sobre SameSite=Strict de la cookie.
REFRESH_CSRF_HEADER = "x-pam-refresh"


@router.post("/refresh")
async def refresh(
    request: Request, response: Response, session: AsyncSession = Depends(get_session)
) -> dict:
    ip = _client_ip(request)
    s = get_settings()

    owner: str = ANON_OPERATOR  # a quién se le atribuye un rechazo

    async def _deny(detail: str, operator: str = "", extra: dict | None = None) -> NoReturn:
        _clear_refresh_cookie(response)
        await _audit_auth(
            session,
            action="auth.refresh",
            operator=operator or owner,
            result="error",
            ip=ip,
            detail=extra or {"motivo": detail},
        )
        raise HTTPException(
            status_code=401,
            detail=detail,
            headers={"set-cookie": response.headers.get("set-cookie", "")},
        )

    if request.headers.get(REFRESH_CSRF_HEADER) != "1":
        raise HTTPException(status_code=403, detail="falta el header X-PAM-Refresh")
    raw = request.cookies.get(s.refresh_cookie_name)
    if not raw:
        await _deny("no hay sesión activa", extra={"motivo": "sin cookie"})
    owner_id = security.refresh_token_owner(raw)
    if owner_id is not None:
        owner_op = await session.get(Operator, owner_id)
        if owner_op is not None:
            owner = owner_op.username
    try:
        operator_id, token_version, jti = security.read_refresh_token(raw)
    except ValueError as e:
        await _deny(str(e))

    row = await session.get(RefreshToken, jti)
    op = await session.get(Operator, operator_id)
    if row is None or op is None or row.operator_id != operator_id:
        await _deny("refresh token inválido o expirado")
    if not op.is_active:
        await _deny("operador inexistente o deshabilitado", op.username)
    if op.token_version != token_version:
        await _deny("refresh token inválido o expirado", op.username)
    if row.revoked_at is not None:
        await _deny("sesión revocada", op.username)

    now = _now()
    if _aware(row.expires_at) <= now:
        await _deny("sesión vencida por inactividad", op.username)

    if row.used_at is not None:
        grace = dt.timedelta(seconds=s.refresh_reuse_grace_seconds)
        if now - _aware(row.used_at) > grace:
            # un refresh token ya rotado volvió a aparecer: alguien tiene una
            # copia. Se corta la sesión entera (la del ladrón y la legítima).
            await _revoke_family(session, row.family_id)
            await audit.record(
                session,
                operator=op.username,
                role="anon",
                action="auth.refresh.reuse",
                result="error",
                source_ip=ip,
                detail={"family_id": row.family_id},
            )
            await session.commit()
            await _deny("refresh token reutilizado; sesión revocada", op.username)
        # dentro de la gracia: dos pestañas renovando a la vez, no es robo.
    else:
        row.used_at = now

    await _audit_auth(session, action="auth.refresh", operator=op.username, result="ok", ip=ip)
    return await _issue_session(
        session,
        op,
        response,
        family_id=row.family_id,
        family_started_at=_aware(row.family_started_at),
    )


@router.post("/login")
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict:
    settings = get_settings()
    ip = _client_ip(request)

    # techo global por IP -- se evalúa antes que el lockout por usuario para
    # no gastar una consulta a la DB si la IP ya está limitada (ver
    # docs/specs/done/login-hardening.md, requisito 1).
    ip_attempts = await cache.incr_with_ttl(_ip_attempts_key(ip), settings.login_ip_window_seconds)
    if ip_attempts is None:
        await _audit_auth(
            session,
            action="auth.login.lockout_degraded",
            operator=body.username,
            result="error",
            ip=ip,
        )
    elif ip_attempts > settings.login_ip_max_attempts:
        await _audit_auth(
            session,
            action="auth.login",
            operator=body.username,
            result="locked",
            ip=ip,
            detail={"scope": "ip", "attempts": ip_attempts},
        )
        raise HTTPException(
            status_code=429,
            detail="demasiados intentos de login desde esta IP; espere unos minutos",
            headers={"Retry-After": str(settings.login_ip_window_seconds)},
        )

    fail_key = _fail_key(body.username, ip)

    fails = await cache.get_int(fail_key)
    if fails is not None and fails >= settings.login_max_fails:
        await _audit_auth(
            session,
            action="auth.login",
            operator=body.username,
            result="locked",
            ip=ip,
            detail={"username": body.username, "fails": fails},
        )
        raise HTTPException(
            status_code=429,
            detail="demasiados intentos fallidos; espere unos minutos",
            headers={"Retry-After": str(settings.login_lockout_minutes * 60)},
        )

    async def _incr_fail() -> None:
        if await cache.incr_with_ttl(fail_key, settings.login_lockout_minutes * 60) is None:
            await _audit_auth(
                session,
                action="auth.login.lockout_degraded",
                operator=body.username,
                result="error",
                ip=ip,
            )

    async def _reject() -> NoReturn:
        await _incr_fail()
        await _audit_auth(
            session,
            action="auth.login",
            operator=body.username,
            result="error",
            ip=ip,
            detail={"username": body.username},
        )
        raise HTTPException(status_code=401, detail="credenciales inválidas")

    op = (
        await session.execute(
            # FOR UPDATE: serializa dos logins simultáneos del mismo operador,
            # para que un mismo código TOTP no pase dos veces (totp_last_step)
            select(Operator).where(Operator.username == body.username).with_for_update()
        )
    ).scalar_one_or_none()

    if op is None:
        security.verify_password(body.password, security.DUMMY_HASH)  # timing
        await _reject()
    if not op.is_active or not security.verify_password(body.password, op.password_hash):
        await _reject()

    operator: Operator = op

    async def _reject_totp() -> NoReturn:
        await _incr_fail()
        await _audit_auth(
            session,
            action="auth.login",
            operator=body.username,
            result="error",
            ip=ip,
            detail={"username": body.username, "reason": "totp"},
        )
        raise HTTPException(status_code=401, detail="código TOTP inválido")

    async def _grant() -> dict:
        await cache.delete(fail_key)
        await _audit_auth(
            session, action="auth.login", operator=operator.username, result="ok", ip=ip
        )
        return await _issue_session(session, operator, response)

    # --- enrolamiento de TOTP (contraseña ya validada) ---
    if not op.totp_enrolled:
        if op.totp_secret is None:
            op.totp_secret = security.new_totp_secret()
            await session.commit()
        if not body.totp_code:
            return {
                "status": "enroll",
                "otpauth_uri": security.totp_uri(op.totp_secret, op.username),
                "qr": security.totp_qr_data_uri(op.totp_secret, op.username),
                "secret": op.totp_secret,
            }
        step = security.verify_totp_step(op.totp_secret, body.totp_code, op.totp_last_step)
        if step is None:
            await _reject_totp()
        op.totp_enrolled = True
        op.totp_last_step = step
        await session.commit()
        return await _grant()

    # --- login normal ---
    # contraseña correcta pero todavía sin código: se lo pedimos en un 2º paso
    # (no es un error de credenciales).
    if not body.totp_code:
        return {"status": "totp_required"}
    step = security.verify_totp_step(op.totp_secret or "", body.totp_code, op.totp_last_step)
    if step is None:
        await _reject_totp()
    op.totp_last_step = step
    return await _grant()


@router.get("/me")
async def me(op: Operator = Depends(current_operator)) -> dict:
    return _operator_dict(op)


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    scope: str = "all",
    op: Operator = Depends(current_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """`scope=all` (default): invalida TODAS las sesiones activas del
    operador (access y refresh, vía `token_version`). `scope=current`: sólo
    la sesión de este navegador (la familia del refresh token de la cookie)
    -- lo usa el cierre por inactividad de la UI, para no desloguear otros
    equipos del mismo operador. Ver docs/specs/done/session-hardening.md."""
    if scope not in ("all", "current"):
        raise HTTPException(status_code=422, detail="scope debe ser 'all' o 'current'")
    s = get_settings()
    if scope == "all":
        op.token_version += 1
    else:
        raw = request.cookies.get(s.refresh_cookie_name)
        if raw:
            try:
                _, _, jti = security.read_refresh_token(raw)
            except ValueError:
                jti = None
            row = await session.get(RefreshToken, jti) if jti else None
            if row is not None and row.operator_id == op.id:
                await _revoke_family(session, row.family_id)
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="auth.logout",
        result="ok",
        source_ip=_client_ip(request),
        detail={"scope": scope},
    )
    await session.commit()
    _clear_refresh_cookie(response)
    return {"status": "ok"}


@router.post("/change-password")
async def change_password(
    body: ChangePasswordIn,
    request: Request,
    response: Response,
    op: Operator = Depends(current_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if not security.verify_password(body.current_password, op.password_hash):
        raise HTTPException(status_code=400, detail="la contraseña actual no es correcta")
    if body.new_password == body.current_password:
        raise HTTPException(status_code=400, detail="la contraseña nueva debe ser distinta")
    policy = security.password_policy_error(body.new_password)
    if policy:
        raise HTTPException(status_code=400, detail=policy)

    op.password_hash = security.hash_password(body.new_password)
    op.must_change_password = False
    # invalida cualquier otra sesión activa del operador (ver
    # docs/specs/done/login-hardening.md) -- la sesión actual sigue andando porque
    # devolvemos tokens nuevos abajo, ya con el token_version incrementado.
    op.token_version += 1
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="operator.password_change",
        result="ok",
        target=op.username,
        source_ip=request.client.host if request.client else None,
    )
    await session.commit()
    return await _issue_session(session, op, response)
