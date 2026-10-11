"""Config de tenant sin operar CyberArk: compliance-settings y logo (solo
platform_admin escribe; ver app/api/tenants.py). Sin feature-flags de
escritura -- el gating de una operación es sólo el rol RBAC del endpoint."""

from __future__ import annotations

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.tenants import router as tenants_router
from app.core import security
from app.core.deps import current_operator
from app.db import get_session
from app.models import AuditLog, Deployment, Operator, OperatorRole, RoleAssignment, Tenant


def _tenant() -> Tenant:
    return Tenant(
        id="lab",
        name="Lab",
        client="Laboratorio",
        deployment=Deployment.privilege_cloud,
        pvwa_url="https://x",
        secret_ref="lab",
    )


@pytest_asyncio.fixture
async def env(session):
    session.add(_tenant())
    await session.flush()

    app = FastAPI()
    app.include_router(tenants_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session

    async def mk_operator(
        role: OperatorRole, *, tenant_role: OperatorRole | None = None
    ) -> Operator:
        op = Operator(
            username=f"op-{role.value}-{id(role)}",
            password_hash=security.hash_password("x"),
            role=role,
        )
        session.add(op)
        await session.flush()
        if tenant_role is not None:
            session.add(RoleAssignment(operator_id=op.id, tenant_id="lab", role=tenant_role))
            await session.flush()
        return op

    def client_as(op: Operator) -> TestClient:
        app.dependency_overrides[current_operator] = lambda: op
        return TestClient(app)

    return {"session": session, "mk_operator": mk_operator, "client_as": client_as}


async def test_get_compliance_settings_default_30(env):
    op = await env["mk_operator"](OperatorRole.tenant_admin, tenant_role=OperatorRole.tenant_admin)
    r = env["client_as"](op).get("/tenants/lab/compliance-settings")
    assert r.status_code == 200
    assert r.json() == {"tenant": "lab", "compliance_stale_days": 30}


async def test_put_compliance_settings_solo_platform_admin(env):
    op = await env["mk_operator"](OperatorRole.tenant_admin, tenant_role=OperatorRole.tenant_admin)
    r = env["client_as"](op).put(
        "/tenants/lab/compliance-settings", json={"compliance_stale_days": 90}
    )
    assert r.status_code == 403


async def test_put_compliance_settings_ok_y_audita(env):
    session = env["session"]
    op = await env["mk_operator"](OperatorRole.platform_admin)
    r = env["client_as"](op).put(
        "/tenants/lab/compliance-settings", json={"compliance_stale_days": 90}
    )
    assert r.status_code == 200
    assert r.json()["compliance_stale_days"] == 90

    t = await session.get(Tenant, "lab")
    assert t.compliance_stale_days == 90
    rows = (await session.execute(select(AuditLog))).scalars().all()
    assert any(a.action == "tenant.compliance_settings.update" and a.target == "lab" for a in rows)


async def test_put_compliance_settings_rechaza_fuera_de_rango(env):
    op = await env["mk_operator"](OperatorRole.platform_admin)
    r = env["client_as"](op).put(
        "/tenants/lab/compliance-settings", json={"compliance_stale_days": 0}
    )
    assert r.status_code == 422


async def test_get_logo_404_sin_logo(env):
    op = await env["mk_operator"](OperatorRole.viewer, tenant_role=OperatorRole.viewer)
    r = env["client_as"](op).get("/tenants/lab/logo")
    assert r.status_code == 404


async def test_put_logo_solo_platform_admin(env):
    op = await env["mk_operator"](OperatorRole.tenant_admin, tenant_role=OperatorRole.tenant_admin)
    r = env["client_as"](op).post(
        "/tenants/lab/logo", files={"file": ("logo.png", b"fake-png-bytes", "image/png")}
    )
    assert r.status_code == 403


async def test_put_logo_rechaza_content_type_invalido(env):
    op = await env["mk_operator"](OperatorRole.platform_admin)
    r = env["client_as"](op).post(
        "/tenants/lab/logo", files={"file": ("logo.pdf", b"%PDF-1.4", "application/pdf")}
    )
    assert r.status_code == 422


async def test_put_logo_rechaza_demasiado_grande(env):
    op = await env["mk_operator"](OperatorRole.platform_admin)
    big = b"x" * (2 * 1024 * 1024 + 1)
    r = env["client_as"](op).post(
        "/tenants/lab/logo", files={"file": ("logo.png", big, "image/png")}
    )
    assert r.status_code == 422


async def test_put_logo_ok_luego_get_y_delete(env):
    session = env["session"]
    op = await env["mk_operator"](OperatorRole.platform_admin)
    content = b"fake-png-bytes"

    r = env["client_as"](op).post(
        "/tenants/lab/logo", files={"file": ("logo.png", content, "image/png")}
    )
    assert r.status_code == 200
    assert r.json() == {"tenant": "lab", "has_logo": True}

    t = await session.get(Tenant, "lab")
    assert t.client_logo == content
    assert t.client_logo_content_type == "image/png"
    rows = (await session.execute(select(AuditLog))).scalars().all()
    assert any(a.action == "tenant.logo.update" and a.target == "lab" for a in rows)

    viewer = await env["mk_operator"](OperatorRole.viewer, tenant_role=OperatorRole.viewer)
    got = env["client_as"](viewer).get("/tenants/lab/logo")
    assert got.status_code == 200
    assert got.content == content
    assert got.headers["content-type"] == "image/png"

    r2 = env["client_as"](op).delete("/tenants/lab/logo")
    assert r2.status_code == 200
    assert r2.json() == {"tenant": "lab", "has_logo": False}

    t2 = await session.get(Tenant, "lab")
    assert t2.client_logo is None
    assert t2.client_logo_content_type is None
    rows2 = (await session.execute(select(AuditLog))).scalars().all()
    assert any(a.action == "tenant.logo.delete" and a.target == "lab" for a in rows2)

    assert env["client_as"](viewer).get("/tenants/lab/logo").status_code == 404
