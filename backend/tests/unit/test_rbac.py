"""Tests de app.core.rbac: rol efectivo y scoping por tenant (deny-by-default)."""

from __future__ import annotations

import pytest_asyncio

from app.core import rbac
from app.models import Operator, OperatorRole, RoleAssignment


async def _mk_operator(session, role: OperatorRole) -> Operator:
    op = Operator(username=f"op-{role.value}", password_hash="x", role=role)
    session.add(op)
    await session.flush()
    return op


@pytest_asyncio.fixture
async def platform_admin(session):
    return await _mk_operator(session, OperatorRole.platform_admin)


@pytest_asyncio.fixture
async def plain_operator(session):
    return await _mk_operator(session, OperatorRole.viewer)


def test_role_meets_jerarquia():
    assert rbac.role_meets(OperatorRole.tenant_admin, OperatorRole.operator) is True
    assert rbac.role_meets(OperatorRole.viewer, OperatorRole.operator) is False
    assert rbac.role_meets(OperatorRole.viewer, OperatorRole.viewer) is True


async def test_platform_admin_acceso_total(session, platform_admin):
    assert (
        await rbac.effective_role(platform_admin, "globex", session) is OperatorRole.platform_admin
    )
    assert await rbac.accessible_tenant_ids(platform_admin, session) is None
    assert await rbac.audit_tenant_ids(platform_admin, session) is None


async def test_deny_by_default_sin_asignacion(session, plain_operator):
    assert await rbac.effective_role(plain_operator, "globex", session) is None
    assert await rbac.accessible_tenant_ids(plain_operator, session) == set()
    assert await rbac.audit_tenant_ids(plain_operator, session) == set()


async def test_asignacion_por_tenant(session, plain_operator):
    session.add(
        RoleAssignment(
            operator_id=plain_operator.id, tenant_id="globex", role=OperatorRole.operator
        )
    )
    await session.flush()

    assert await rbac.effective_role(plain_operator, "globex", session) is OperatorRole.operator
    assert await rbac.effective_role(plain_operator, "acme", session) is None
    assert await rbac.accessible_tenant_ids(plain_operator, session) == {"globex"}
    # operator no llega a tenant_admin -> no ve audit
    assert await rbac.audit_tenant_ids(plain_operator, session) == set()


async def test_asignacion_wildcard(session, plain_operator):
    session.add(
        RoleAssignment(
            operator_id=plain_operator.id,
            tenant_id=rbac.ALL_TENANTS,
            role=OperatorRole.tenant_admin,
        )
    )
    await session.flush()

    assert (
        await rbac.effective_role(plain_operator, "cualquiera", session)
        is OperatorRole.tenant_admin
    )
    assert await rbac.accessible_tenant_ids(plain_operator, session) is None
    assert await rbac.audit_tenant_ids(plain_operator, session) is None
