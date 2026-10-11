"""
RBAC scopeado por tenant. Deny-by-default: fuera de `platform_admin` global,
el acceso a un tenant viene solo de `role_assignments`.

Ver docs/specs/rbac-por-tenant.md.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Operator, OperatorRole, RoleAssignment

# tenant_id que representa "todos los tenants" en role_assignments
ALL_TENANTS = "*"

# jerarquía: cada rol incluye las capacidades del anterior.
ROLE_RANK = {
    OperatorRole.viewer: 0,
    OperatorRole.operator: 1,
    OperatorRole.approver: 2,
    OperatorRole.tenant_admin: 3,
    OperatorRole.platform_admin: 4,
}


def role_meets(role: OperatorRole, minimum: OperatorRole) -> bool:
    return ROLE_RANK[role] >= ROLE_RANK[minimum]


async def effective_role(
    op: Operator, tenant_id: str, session: AsyncSession
) -> OperatorRole | None:
    """Rol efectivo del operador sobre un tenant, o None si no tiene acceso."""
    if op.role is OperatorRole.platform_admin:
        return OperatorRole.platform_admin
    rows = (
        await session.execute(
            select(RoleAssignment.tenant_id, RoleAssignment.role).where(
                RoleAssignment.operator_id == op.id
            )
        )
    ).all()
    by_tenant: dict[str, OperatorRole] = {row.tenant_id: row.role for row in rows}
    return by_tenant.get(tenant_id) or by_tenant.get(ALL_TENANTS)


async def accessible_tenant_ids(op: Operator, session: AsyncSession) -> set[str] | None:
    """Ids de tenants que el operador puede ver. None = todos (sin restricción)."""
    if op.role is OperatorRole.platform_admin:
        return None
    rows = (
        (
            await session.execute(
                select(RoleAssignment.tenant_id).where(RoleAssignment.operator_id == op.id)
            )
        )
        .scalars()
        .all()
    )
    if ALL_TENANTS in rows:
        return None
    return set(rows)


async def has_role_on_any_tenant(
    op: Operator, minimum: OperatorRole, session: AsyncSession
) -> bool:
    """True si el operador tiene rol EFECTIVO >= `minimum` en al menos un
    tenant (o es `platform_admin`). Para recursos globales de PAM Web Console, no
    scopeados por tenant (ej. base de conocimiento) — mismo criterio que
    `useAnyTenantAdmin()` del frontend, server-side."""
    if op.role is OperatorRole.platform_admin:
        return True
    rows = (
        (
            await session.execute(
                select(RoleAssignment.role).where(RoleAssignment.operator_id == op.id)
            )
        )
        .scalars()
        .all()
    )
    return any(role_meets(r, minimum) for r in rows)


async def audit_tenant_ids(op: Operator, session: AsyncSession) -> set[str] | None:
    """Tenants cuyo audit puede ver (rol efectivo tenant_admin+). None = todos."""
    if op.role is OperatorRole.platform_admin:
        return None
    rows = (
        await session.execute(
            select(RoleAssignment.tenant_id, RoleAssignment.role).where(
                RoleAssignment.operator_id == op.id
            )
        )
    ).all()
    admin = {t for t, r in rows if role_meets(r, OperatorRole.tenant_admin)}
    if ALL_TENANTS in admin:
        return None
    return admin
