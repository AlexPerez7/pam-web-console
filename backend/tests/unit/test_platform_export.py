"""GET /tenants/{id}/platforms/{platform_id}/export — descarga el paquete
de "Export Platform" en cualquier momento (no sólo antes de borrar).
Read-only, viewer+. Ver docs/specs/platform-lifecycle.md (client.export_platform
ya existía para el backup de platform.delete)."""

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


class FakeClient:
    def __init__(self) -> None:
        self.exported: list[str] = []

    async def export_platform(self, platform_id: str) -> bytes:
        self.exported.append(platform_id)
        return b"PK\x03\x04fake-zip"


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
    viewer = Operator(
        username="v", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    outsider = Operator(
        username="o", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    session.add_all([viewer, outsider])
    await session.flush()
    session.add(RoleAssignment(operator_id=viewer.id, tenant_id="lab", role=OperatorRole.viewer))
    await session.flush()

    app = FastAPI()
    app.include_router(inventory_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: viewer
    return {"app": app, "client": TestClient(app), "fake": fake, "outsider": outsider}


def test_platform_export_ok(env):
    r = env["client"].get("/tenants/lab/platforms/WinDomain/export")
    assert r.status_code == 200
    assert r.content == b"PK\x03\x04fake-zip"
    assert "WinDomain.zip" in r.headers["content-disposition"]
    assert env["fake"].exported == ["WinDomain"]


def test_platform_export_sin_acceso_al_tenant_403(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["outsider"]
    r = env["client"].get("/tenants/lab/platforms/WinDomain/export")
    assert r.status_code == 403
