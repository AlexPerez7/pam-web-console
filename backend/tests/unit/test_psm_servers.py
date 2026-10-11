"""GET /tenants/{id}/psm-servers — nombres de PSM para el selector de
platform.psm.update. Regresión: el campo real de "Get PSM Servers" es
`ID`/`id`, no `PSMServerID` (que nunca existió) — el bug dejaba `names`
siempre vacío y el selector de PsmServerField.tsx caía siempre al modo
"ingresar a mano". Ver app/reports/psm_balance.py (mismo campo, ya
probado en el server) para la fuente de verdad del casing real."""

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
    async def list_psm_servers(self) -> list:
        return [
            {"ID": "PSMServer_LAX", "Name": "psm-lax-01"},
            {"ID": "PSMServer_SCL", "Name": "psm-scl-01"},
        ]


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
    session.add(viewer)
    await session.flush()
    session.add(RoleAssignment(operator_id=viewer.id, tenant_id="lab", role=OperatorRole.viewer))
    await session.flush()

    app = FastAPI()
    app.include_router(inventory_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: viewer
    return {"app": app, "client": TestClient(app)}


def test_psm_servers_usa_el_campo_ID_real(env):
    r = env["client"].get("/tenants/lab/psm-servers")
    assert r.status_code == 200
    assert r.json()["names"] == ["PSMServer_LAX", "PSMServer_SCL"]
