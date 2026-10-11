"""GET /tenants/{id}/accounts/{id}/password-versions — metadata de
versiones históricas (sin el valor). Ver
docs/specs/done/account-password-versions.md."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import inventory as inventory_mod
from app.api.inventory import router as inventory_router
from app.core import security
from app.core.deps import current_operator
from app.db import get_session
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant

VERSIONS = [
    {"VersionID": 3, "ModificationTime": 1700000300, "ModifiedBy": "GRGCYBERARK01"},
    {"VersionID": 2, "ModificationTime": 1700000200, "ModifiedBy": "GRGCYBERARK01"},
]


class FakeClient:
    async def list_password_versions(self, account_id: str) -> list:
        return list(VERSIONS)


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    fake = FakeClient()

    @asynccontextmanager
    async def fake_tc(tenant_id: str, sess):
        yield (None, fake)

    monkeypatch.setattr(inventory_mod, "tenant_client", fake_tc)

    session.add(
        Tenant(
            id="lab",
            name="Lab",
            client="A",
            deployment=Deployment.privilege_cloud,
            pvwa_url="https://x",
            secret_ref="lab",
        )
    )
    tenant_admin = Operator(
        username="ta", password_hash=security.hash_password("x"), role=OperatorRole.tenant_admin
    )
    viewer = Operator(
        username="v", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    session.add_all([tenant_admin, viewer])
    await session.flush()
    session.add_all(
        [
            RoleAssignment(
                operator_id=tenant_admin.id, tenant_id="lab", role=OperatorRole.tenant_admin
            ),
            RoleAssignment(operator_id=viewer.id, tenant_id="lab", role=OperatorRole.viewer),
        ]
    )
    await session.flush()

    app = FastAPI()
    app.include_router(inventory_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: tenant_admin
    return {"app": app, "client": TestClient(app), "tenant_admin": tenant_admin, "viewer": viewer}


def test_lista_versiones_tenant_admin_ok(env):
    r = env["client"].get("/tenants/lab/accounts/42/password-versions")
    assert r.status_code == 200
    body = r.json()
    assert len(body["versions"]) == 2
    assert body["versions"][0]["VersionID"] == 3


def test_lista_versiones_viewer_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["viewer"]
    r = env["client"].get("/tenants/lab/accounts/42/password-versions")
    assert r.status_code == 403
