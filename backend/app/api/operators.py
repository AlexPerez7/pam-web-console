"""
Gestión de operadores de PAM Web Console. Solo `platform_admin`. Todo auditado.

El alta y el reset de contraseña dejan `must_change_password=True`: el
operador debe cambiarla en el próximo login antes de poder operar (el admin
nunca conoce la contraseña definitiva). El TOTP no lo ve nadie: se enrola en
el primer login.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit, rbac, security
from app.core.deps import current_operator, require_role
from app.db import get_session
from app.models import Operator, OperatorRole, RoleAssignment, Tenant

router = APIRouter(
    prefix="/operators",
    tags=["operators"],
    dependencies=[Depends(require_role(OperatorRole.platform_admin))],
)


class OperatorCreateIn(BaseModel):
    username: str
    password: str
    role: OperatorRole = OperatorRole.viewer
    display_name: str | None = None
    email: str | None = None


class OperatorPatchIn(BaseModel):
    username: str | None = None
    role: OperatorRole | None = None
    display_name: str | None = None
    email: str | None = None
    is_active: bool | None = None


class PasswordResetIn(BaseModel):
    new_password: str


def _dict(op: Operator) -> dict:
    return {
        "id": op.id,
        "username": op.username,
        "display_name": op.display_name,
        "email": op.email,
        "role": op.role.value,
        "is_active": op.is_active,
        "totp_enrolled": op.totp_enrolled,
        "must_change_password": op.must_change_password,
        "created_at": op.created_at.isoformat() if op.created_at else None,
    }


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _get(session: AsyncSession, operator_id: int) -> Operator:
    op = await session.get(Operator, operator_id)
    if op is None:
        raise HTTPException(status_code=404, detail="operador no encontrado")
    return op


@router.get("")
async def list_operators(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (await session.execute(select(Operator).order_by(Operator.username))).scalars().all()
    return [_dict(o) for o in rows]


@router.post("", status_code=201)
async def create_operator(
    body: OperatorCreateIn,
    request: Request,
    me: Operator = Depends(current_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="usuario requerido")
    policy = security.password_policy_error(body.password)
    if policy:
        raise HTTPException(status_code=400, detail=policy)
    exists = (
        await session.execute(select(Operator).where(Operator.username == username))
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=409, detail=f"ya existe un operador '{username}'")

    op = Operator(
        username=username,
        password_hash=security.hash_password(body.password),
        role=body.role,
        display_name=body.display_name,
        email=body.email,
        is_active=True,
        totp_enrolled=False,
        must_change_password=True,
    )
    session.add(op)
    await session.flush()
    await audit.record(
        session,
        operator=me.username,
        role=me.role.value,
        action="operator.create",
        result="ok",
        target=username,
        detail={"role": body.role.value},
        source_ip=_ip(request),
    )
    await session.commit()
    await session.refresh(op)
    return _dict(op)


@router.patch("/{operator_id}")
async def patch_operator(
    operator_id: int,
    body: OperatorPatchIn,
    request: Request,
    me: Operator = Depends(current_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    op = await _get(session, operator_id)

    if op.id == me.id and body.is_active is False:
        raise HTTPException(status_code=400, detail="no es posible desactivar la propia cuenta")
    if op.id == me.id and body.role is not None and body.role != op.role:
        raise HTTPException(
            status_code=400, detail="no es posible cambiar el rol de la propia cuenta"
        )

    changes: dict = {}
    if body.username is not None:
        new_username = body.username.strip()
        if not new_username:
            raise HTTPException(status_code=400, detail="usuario requerido")
        if new_username != op.username:
            exists = (
                await session.execute(
                    select(Operator).where(Operator.username == new_username, Operator.id != op.id)
                )
            ).scalar_one_or_none()
            if exists is not None:
                raise HTTPException(
                    status_code=409, detail=f"ya existe un operador '{new_username}'"
                )
            changes["username"] = {"from": op.username, "to": new_username}
            op.username = new_username
    if body.role is not None and body.role != op.role:
        changes["role"] = {"from": op.role.value, "to": body.role.value}
        op.role = body.role
    if body.display_name is not None:
        op.display_name = body.display_name
        changes["display_name"] = body.display_name
    if body.email is not None:
        op.email = body.email
        changes["email"] = body.email
    if body.is_active is not None and body.is_active != op.is_active:
        changes["is_active"] = body.is_active
        op.is_active = body.is_active

    if not changes:
        return _dict(op)

    await audit.record(
        session,
        operator=me.username,
        role=me.role.value,
        action="operator.update",
        result="ok",
        target=op.username,
        detail=changes,
        source_ip=_ip(request),
    )
    await session.commit()
    await session.refresh(op)
    return _dict(op)


@router.post("/{operator_id}/reset-password")
async def reset_password(
    operator_id: int,
    body: PasswordResetIn,
    request: Request,
    me: Operator = Depends(current_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    policy = security.password_policy_error(body.new_password)
    if policy:
        raise HTTPException(status_code=400, detail=policy)
    op = await _get(session, operator_id)
    op.password_hash = security.hash_password(body.new_password)
    op.must_change_password = True
    await audit.record(
        session,
        operator=me.username,
        role=me.role.value,
        action="operator.reset_password",
        result="ok",
        target=op.username,
        source_ip=_ip(request),
    )
    await session.commit()
    return {"status": "ok"}


@router.post("/{operator_id}/reset-totp")
async def reset_totp(
    operator_id: int,
    request: Request,
    me: Operator = Depends(current_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    op = await _get(session, operator_id)
    op.totp_secret = None
    op.totp_enrolled = False
    op.totp_last_step = None
    # un reset de TOTP suele ser por un dispositivo perdido: corta también
    # las sesiones activas del operador (docs/specs/done/session-hardening.md)
    op.token_version += 1
    await audit.record(
        session,
        operator=me.username,
        role=me.role.value,
        action="operator.reset_totp",
        result="ok",
        target=op.username,
        source_ip=_ip(request),
    )
    await session.commit()
    return {"status": "ok"}


# --- asignaciones RBAC por tenant ------------------------------------- #


class AssignmentIn(BaseModel):
    tenant_id: str  # id de tenant o "*" (todos)
    role: OperatorRole


class AssignmentsIn(BaseModel):
    assignments: list[AssignmentIn]


@router.get("/{operator_id}/assignments")
async def get_assignments(
    operator_id: int, session: AsyncSession = Depends(get_session)
) -> list[dict]:
    op = await _get(session, operator_id)
    rows = (
        (await session.execute(select(RoleAssignment).where(RoleAssignment.operator_id == op.id)))
        .scalars()
        .all()
    )
    return [{"tenant_id": r.tenant_id, "role": r.role.value} for r in rows]


@router.put("/{operator_id}/assignments")
async def set_assignments(
    operator_id: int,
    body: AssignmentsIn,
    request: Request,
    me: Operator = Depends(current_operator),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    op = await _get(session, operator_id)

    # dedup por tenant_id (última gana)
    wanted: dict[str, OperatorRole] = {a.tenant_id: a.role for a in body.assignments}

    real_ids = {t for t in wanted if t != rbac.ALL_TENANTS}
    if real_ids:
        found = set(
            (await session.execute(select(Tenant.id).where(Tenant.id.in_(real_ids))))
            .scalars()
            .all()
        )
        missing = real_ids - found
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"tenants inexistentes: {', '.join(sorted(missing))}",
            )

    await session.execute(delete(RoleAssignment).where(RoleAssignment.operator_id == op.id))
    for tid, role in wanted.items():
        session.add(RoleAssignment(operator_id=op.id, tenant_id=tid, role=role))

    await audit.record(
        session,
        operator=me.username,
        role=me.role.value,
        action="operator.assignments",
        result="ok",
        target=op.username,
        detail={"assignments": {t: r.value for t, r in wanted.items()}},
        source_ip=_ip(request),
    )
    await session.commit()
    return [{"tenant_id": t, "role": r.value} for t, r in wanted.items()]
