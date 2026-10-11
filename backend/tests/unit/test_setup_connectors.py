"""Setup > Connector management. Ver docs/specs/setup-connector-management.md."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import setup as setup_mod
from app.api.setup import router as setup_router
from app.core import security
from app.core.deps import current_operator
from app.cyberark import Forbidden
from app.db import get_session
from app.domain.connectors import connector_rows, pool_rows
from app.models import AuditLog, Deployment, Operator, OperatorRole, RoleAssignment, Tenant

POOLS = [{"poolId": "p1", "name": "Globex_GCP", "description": "GCP"}]


def _conn(cid: str, statuses: list[str], **extra) -> dict:
    return {
        "connectorId": cid,
        "host": {"hostname": f"host-{cid}", "privateIp": "10.0.0.1", "os": "Windows"},
        "platformType": "AWS",
        "connectorStatus": "Active",
        "version": "1.0",
        "components": [
            {"name": f"c{i}", "componentStatus": s, "usedCpu": -1, "updatedAt": 1_700_000_000_000}
            for i, s in enumerate(statuses)
        ],
        **extra,
    }


def test_filas_resuelven_pool_y_componentes():
    rows = connector_rows([_conn("a", ["Active"] * 4, poolId="p1")], POOLS)
    r = rows[0]
    assert (r["componentsActive"], r["componentsTotal"], r["degraded"]) == (4, 4, False)
    assert r["pool"] == "Globex_GCP"
    assert r["components"][0]["usedCpu"] is None  # centinela -1
    assert r["components"][0]["updatedAt"].startswith("2023-11-14")


def test_ultimo_heartbeat_sale_de_updated_at_del_conector():
    # Valores reales de CYBERARK-CON02 del lab (portal: Oct 7, 2026, 8:21 AM, UTC-3).
    r = connector_rows([_conn("a", ["Active"], updatedAt=1_791_372_068_857)], POOLS)[0]
    assert r["lastHeartbeat"].startswith("2026-10-07T11:21")
    assert connector_rows([_conn("b", ["Active"])], POOLS)[0]["lastHeartbeat"] is None


def test_componente_caido_marca_fila_degradada_y_upgrade_visible():
    conn = _conn("a", ["Active", "Not Found"])
    conn["components"][0]["upgradeVersions"] = ["2.0"]
    r = connector_rows([conn], POOLS)[0]
    assert r["degraded"] is True
    assert (r["componentsActive"], r["componentsTotal"]) == (1, 2)
    assert r["upgradeAvailable"] is True and r["upgradeVersions"] == ["2.0"]


def test_pool_se_resuelve_desde_la_lista_de_miembros_del_pool():
    pools = [{"poolId": "p1", "name": "P", "connectors": [{"connectorId": "a"}]}]
    assert connector_rows([_conn("a", ["Active"])], pools)[0]["pool"] == "P"
    out = pool_rows(pools, [_conn("a", ["Active"])])
    assert out[0]["connectorCount"] == 1


class FakeClient:
    def __init__(self, *, cm=True, sia=True, sia_error: Exception | None = None):
        self.connector_management_url = "https://cm" if cm else None
        self.sia_connector_url = "https://sia" if sia else None
        self._sia_error = sia_error

    async def list_connectors(self):
        return [_conn("a", ["Active"] * 4, poolId="p1"), _conn("b", ["Active"] * 4, poolId="p1")]

    async def list_connector_pools(self):
        return POOLS

    async def list_sia_connectors(self):
        if self._sia_error:
            raise self._sia_error
        # Forma real de SIA Connector API (confirmada contra Globex).
        return [
            {
                "id": "s1",
                "hostName": "sia-1",
                "hostIp": "10.0.0.9",
                "os": "windows",
                "status": "Active",
                "isLatestVersion": True,
            }
        ]


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    holder: dict = {"client": FakeClient()}

    @asynccontextmanager
    async def fake_tc(tenant_id: str, sess):
        yield (None, holder["client"])

    monkeypatch.setattr(setup_mod, "tenant_client", fake_tc)

    async def get_json(key: str):
        return None

    async def set_json(key: str, value: dict, ttl: int) -> None:
        return None

    monkeypatch.setattr(setup_mod.cache, "get_json", get_json)
    monkeypatch.setattr(setup_mod.cache, "set_json", set_json)

    session.add(
        Tenant(
            id="t1",
            name="T",
            client="A",
            deployment=Deployment.privilege_cloud,
            pvwa_url="https://x",
            secret_ref="t1",
        )
    )
    viewer = Operator(
        username="v", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    session.add(viewer)
    await session.flush()
    session.add(RoleAssignment(operator_id=viewer.id, tenant_id="t1", role=OperatorRole.viewer))
    await session.flush()

    app = FastAPI()
    app.include_router(setup_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: viewer
    return {"client": TestClient(app), "holder": holder, "session": session}


async def test_lista_conectores_y_audita_con_conteo(env):
    r = env["client"].get("/tenants/t1/setup/connectors")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2 and body["connectors"][0]["pool"] == "Globex_GCP"
    log = (await env["session"].execute(select(AuditLog))).scalars().all()
    assert [(a.action, a.result, a.detail["count"]) for a in log] == [
        ("setup.connectors.list", "ok", 2)
    ]


async def test_lista_pools(env):
    r = env["client"].get("/tenants/t1/setup/connector-pools")
    assert r.status_code == 200
    assert r.json()["pools"][0]["connectorCount"] == 2


def test_tenant_sin_url_responde_409(env):
    env["holder"]["client"] = FakeClient(cm=False, sia=False)
    for path in ("connectors", "connector-pools", "sia-connectors"):
        r = env["client"].get(f"/tenants/t1/setup/{path}")
        assert r.status_code == 409
        assert "configurada" in r.json()["detail"]


def test_sia_403_se_traduce_a_mensaje_claro(env):
    env["holder"]["client"] = FakeClient(sia_error=Forbidden("403"))
    r = env["client"].get("/tenants/t1/setup/sia-connectors")
    assert r.status_code == 403
    assert "SIA Admin" in r.json()["detail"]


def test_lista_sia(env):
    r = env["client"].get("/tenants/t1/setup/sia-connectors")
    assert r.status_code == 200
    c = r.json()["connectors"][0]
    assert (c["connectorId"], c["hostname"], c["ip"]) == ("s1", "sia-1", "10.0.0.9")


def test_operador_sin_acceso_al_tenant_no_pasa(env):
    r = env["client"].get("/tenants/otro/setup/connectors")
    assert r.status_code in (403, 404)
