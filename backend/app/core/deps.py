"""Dependencias de FastAPI para auth y RBAC de operadores."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rbac
from app.core.security import read_token
from app.db import get_session
from app.models import Operator, OperatorRole

_bearer = HTTPBearer(auto_error=False)

# endpoints permitidos mientras el operador tiene must_change_password pendiente
_PWD_CHANGE_ALLOWED = {"/auth/me", "/auth/change-password"}


async def current_operator(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> Operator:
    if creds is None:
        raise HTTPException(
            status_code=401, detail="falta el token de sesión (Authorization: Bearer)"
        )
    # el middleware de rate-limit de lecturas (app.core.read_rate_limit) ya
    # decodifica el token para requests GET no exentas -- reusarlo evita un
    # segundo jwt.decode redundante (ver docs/specs/done/api-read-rate-limit.md).
    cached = getattr(request.state, "auth_token_claims", None)
    if cached is not None:
        operator_id, token_version = cached
    else:
        try:
            operator_id, token_version = read_token(creds.credentials)
        except ValueError as e:
            raise HTTPException(status_code=401, detail=str(e))
    op = await session.get(Operator, operator_id)
    if op is None or not op.is_active:
        raise HTTPException(status_code=401, detail="operador inexistente o deshabilitado")
    if op.token_version != token_version:
        raise HTTPException(status_code=401, detail="token inválido o expirado")
    if op.must_change_password and request.url.path not in _PWD_CHANGE_ALLOWED:
        raise HTTPException(status_code=403, detail="password_change_required")
    return op


def require_role(minimum: OperatorRole):
    """Exige un rol de PLATAFORMA mínimo (Operator.role)."""

    async def _dep(op: Operator = Depends(current_operator)) -> Operator:
        if not rbac.role_meets(op.role, minimum):
            raise HTTPException(
                status_code=403, detail=f"requiere rol '{minimum.value}' o superior"
            )
        return op

    return _dep


def require_tenant_access(minimum: OperatorRole = OperatorRole.viewer):
    """Exige rol EFECTIVO mínimo sobre el tenant del path (`tenant_id`)."""

    async def _dep(
        tenant_id: str,
        op: Operator = Depends(current_operator),
        session: AsyncSession = Depends(get_session),
    ) -> Operator:
        role = await rbac.effective_role(op, tenant_id, session)
        if role is None or not rbac.role_meets(role, minimum):
            raise HTTPException(status_code=403, detail=f"sin acceso al tenant '{tenant_id}'")
        return op

    return _dep


def require_any_tenant_role(minimum: OperatorRole):
    """Exige rol EFECTIVO mínimo en AL MENOS UN tenant — para recursos
    globales de PAM Web Console sin `tenant_id` en el path (ej. base de
    conocimiento)."""

    async def _dep(
        op: Operator = Depends(current_operator),
        session: AsyncSession = Depends(get_session),
    ) -> Operator:
        if not await rbac.has_role_on_any_tenant(op, minimum, session):
            raise HTTPException(
                status_code=403,
                detail=f"requiere rol '{minimum.value}' o superior en al menos un tenant",
            )
        return op

    return _dep


async def require_audit_reader(
    op: Operator = Depends(current_operator),
    session: AsyncSession = Depends(get_session),
) -> Operator:
    """`/audit`: platform_admin global, o tenant_admin+ efectivo en algún tenant."""
    if op.role is OperatorRole.platform_admin:
        return op
    scope = await rbac.audit_tenant_ids(op, session)
    if scope is None or scope:
        return op
    raise HTTPException(status_code=403, detail="requiere rol 'tenant_admin' o superior")
