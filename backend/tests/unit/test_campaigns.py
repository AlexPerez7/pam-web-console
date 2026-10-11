"""Campañas de rotación/remediación (Fase 4).

Ver docs/specs/done/rotation-campaigns.md. Avance calculado en vivo (una sola
list_accounts() por request), sin feature-flag — RBAC operator+ para
crear/borrar, viewer+ para leer.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import campaigns as campaigns_mod
from app.api.campaigns import router as campaigns_router
from app.core import security
from app.core.deps import current_operator
from app.db import get_session
from app.domain.campaigns import campaign_progress, campaign_summary
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant

# --- dominio (puro) ------------------------------------------------------ #


def test_campaign_progress_estados():
    members = [
        {"account_id": "1"},
        {"account_id": "2"},
        {"account_id": "3"},
    ]
    accounts_by_id = {"1": {}, "2": {}}  # "3" ya no existe
    failed_ids = {"1"}  # "1" sigue fallando, "2" se resolvió
    rows = campaign_progress(members, accounts_by_id, failed_ids)
    by_id = {r["account_id"]: r["status"] for r in rows}
    assert by_id == {"1": "pending", "2": "resolved", "3": "not_found"}


def test_campaign_summary():
    rows = [{"status": "resolved"}, {"status": "pending"}, {"status": "not_found"}]
    assert campaign_summary(rows) == {
        "total": 3,
        "resolved": 1,
        "pending": 1,
        "not_found": 1,
    }


# --- endpoints ------------------------------------------------------------ #


class FakeClient:
    def __init__(self, accounts: list[dict]) -> None:
        self.accounts = accounts

    async def list_accounts(self) -> list[dict]:
        return self.accounts


def _acc(id_: str, status: str = "failure") -> dict:
    return {
        "id": id_,
        "name": f"acc-{id_}",
        "safeName": "CajaFuerte",
        "platformId": "WinDomain",
        "secretManagement": {"status": status},
    }


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    fake = FakeClient([_acc("1"), _acc("2", status="success")])

    @asynccontextmanager
    async def fake_tc(tenant_id: str, sess):
        yield (None, fake)

    monkeypatch.setattr(campaigns_mod, "tenant_client", fake_tc)

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
    op = Operator(
        username="op", password_hash=security.hash_password("x"), role=OperatorRole.operator
    )
    viewer = Operator(
        username="v", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    session.add_all([op, viewer])
    await session.flush()
    session.add_all(
        [
            RoleAssignment(operator_id=op.id, tenant_id="lab", role=OperatorRole.operator),
            RoleAssignment(operator_id=viewer.id, tenant_id="lab", role=OperatorRole.viewer),
        ]
    )
    await session.flush()

    app = FastAPI()
    app.include_router(campaigns_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: op
    return {"app": app, "client": TestClient(app), "fake": fake, "viewer": viewer}


REASON = "cuentas afectadas por la migración de SolarWinds, ticket OPS-30"


def test_crear_campana_ok(env):
    r = env["client"].post(
        "/tenants/lab/campaigns",
        json={"name": "Migración SolarWinds", "reason": REASON, "account_ids": ["1", "2"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["members"] == 2
    assert body["missing"] == []


def test_crear_campana_con_cuentas_faltantes(env):
    r = env["client"].post(
        "/tenants/lab/campaigns",
        json={"name": "X", "reason": REASON, "account_ids": ["1", "no-existe"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["members"] == 1
    assert body["missing"] == ["no-existe"]


def test_crear_campana_todas_faltantes_422(env):
    r = env["client"].post(
        "/tenants/lab/campaigns",
        json={"name": "X", "reason": REASON, "account_ids": ["no-existe"]},
    )
    assert r.status_code == 422


def test_crear_campana_reason_corta_422(env):
    r = env["client"].post(
        "/tenants/lab/campaigns", json={"name": "X", "reason": "corto", "account_ids": ["1"]}
    )
    assert r.status_code == 422


def test_listar_con_progreso(env):
    env["client"].post(
        "/tenants/lab/campaigns",
        json={"name": "X", "reason": REASON, "account_ids": ["1", "2"]},
    )
    r = env["client"].get("/tenants/lab/campaigns")
    assert r.status_code == 200
    rows = r.json()["campaigns"]
    assert len(rows) == 1
    assert rows[0]["total"] == 2
    assert rows[0]["resolved"] == 1  # "2" está en success
    assert rows[0]["pending"] == 1  # "1" sigue en failure


def test_detalle_con_cuenta_borrada(env):
    created = (
        env["client"]
        .post(
            "/tenants/lab/campaigns",
            json={"name": "X", "reason": REASON, "account_ids": ["1", "2"]},
        )
        .json()
    )
    env["fake"].accounts = [_acc("1")]  # "2" ya no existe en CyberArk
    r = env["client"].get(f"/tenants/lab/campaigns/{created['id']}")
    assert r.status_code == 200
    statuses = {a["account_id"]: a["status"] for a in r.json()["accounts"]}
    assert statuses == {"1": "pending", "2": "not_found"}


def test_borrar_campana(env):
    created = (
        env["client"]
        .post(
            "/tenants/lab/campaigns",
            json={"name": "X", "reason": REASON, "account_ids": ["1"]},
        )
        .json()
    )
    r = env["client"].delete(f"/tenants/lab/campaigns/{created['id']}")
    assert r.status_code == 200
    r2 = env["client"].get(f"/tenants/lab/campaigns/{created['id']}")
    assert r2.status_code == 404


def test_rbac_viewer_no_puede_crear(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["viewer"]
    r = env["client"].post(
        "/tenants/lab/campaigns",
        json={"name": "X", "reason": REASON, "account_ids": ["1"]},
    )
    assert r.status_code == 403


def test_rbac_viewer_puede_listar(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["viewer"]
    r = env["client"].get("/tenants/lab/campaigns")
    assert r.status_code == 200
