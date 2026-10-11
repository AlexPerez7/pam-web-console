"""account.update en lote — tildar varias cuentas y editarlas con el mismo
valor (ej. 5 cuentas de firewall al mismo address nuevo), como el
tilde-múltiple + Editar de la vista clásica del PVWA."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.writes import router as writes_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.cyberark import NotFound
from app.db import get_session
from app.domain.accounts import build_account_bulk_plan
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant

ACCOUNTS = {
    "1": {"id": "1", "name": "fw-01", "address": "10.1.1.1", "userName": "svc-fw"},
    "2": {"id": "2", "name": "fw-02", "address": "10.1.1.2", "userName": "svc-fw"},
    "3": {"id": "3", "name": "fw-03", "address": "10.2.2.2", "userName": "svc-fw"},  # ya en destino
}


def test_build_account_bulk_plan_diff_por_cuenta():
    plan = build_account_bulk_plan(ACCOUNTS, {"address": "10.2.2.2"})
    fields = {d["field"]: (d["from"], d["to"]) for d in plan.diff}
    assert fields["fw-01 — address"] == ("10.1.1.1", "10.2.2.2")
    assert fields["fw-02 — address"] == ("10.1.1.2", "10.2.2.2")
    assert "fw-03 — address" not in fields  # ya tenía el valor -> no aparece
    assert plan.object_count == 2
    assert "2 cuenta" in plan.effect


def test_build_account_bulk_plan_sin_cambios_es_no_op():
    plan = build_account_bulk_plan(ACCOUNTS, {"address": "10.1.1.1"})
    changed = [d for d in plan.diff if d["field"].startswith("fw-01")]
    assert changed == []


ACCOUNTS_MGMT = {
    "1": {"id": "1", "name": "db-01", "secretManagement": {"automaticManagementEnabled": False}},
    "2": {"id": "2", "name": "db-02", "secretManagement": {"automaticManagementEnabled": False}},
    "3": {"id": "3", "name": "db-03", "secretManagement": {"automaticManagementEnabled": True}},
}


def test_build_account_bulk_plan_habilitar_gestion_automatica():
    """El caso real que lo pidió Alex: varias cuentas de DB en Globex con
    la rotación automática apagada, reactivarlas todas juntas."""
    plan = build_account_bulk_plan(ACCOUNTS_MGMT, {"automaticManagementEnabled": True})
    fields = {d["field"]: (d["from"], d["to"]) for d in plan.diff}
    assert fields["db-01 — secretManagement.automaticManagementEnabled"] == (False, True)
    assert fields["db-02 — secretManagement.automaticManagementEnabled"] == (False, True)
    assert "db-03 — secretManagement.automaticManagementEnabled" not in fields  # ya habilitada
    assert plan.object_count == 2


class FakeClient:
    def __init__(self) -> None:
        self.accounts: dict[str, dict] = dict(ACCOUNTS)
        self.updated: list[tuple[str, list]] = []
        self.missing: set[str] = set()

    async def get_account(self, account_id: str) -> dict:
        if account_id in self.missing:
            raise NotFound(f"cuenta '{account_id}' no existe")
        return self.accounts[account_id]

    async def update_account(self, account_id: str, patch: list[dict]) -> dict:
        self.updated.append((account_id, patch))
        return self.accounts[account_id]


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    fake = FakeClient()

    @asynccontextmanager
    async def fake_tc(tenant_id: str, sess):
        yield (None, fake)

    monkeypatch.setattr(writeop, "tenant_client", fake_tc)

    async def incr(k: str, ttl: int) -> int:
        return 1

    monkeypatch.setattr(writeop.cache, "incr_with_ttl", incr)

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
    operator_op = Operator(
        username="op", password_hash=security.hash_password("x"), role=OperatorRole.operator
    )
    viewer = Operator(
        username="v", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    session.add_all([operator_op, viewer])
    await session.flush()
    session.add_all(
        [
            RoleAssignment(operator_id=operator_op.id, tenant_id="lab", role=OperatorRole.operator),
            RoleAssignment(operator_id=viewer.id, tenant_id="lab", role=OperatorRole.viewer),
        ]
    )
    await session.flush()

    app = FastAPI()
    app.include_router(writes_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: operator_op
    return {"app": app, "client": TestClient(app), "fake": fake, "viewer": viewer}


def test_bulk_update_dry_run(env):
    r = env["client"].post(
        "/tenants/lab/accounts/bulk-update",
        json={"account_ids": ["1", "2", "3"], "changes": {"address": "10.2.2.2"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert len(body["diff"]) == 2  # sólo las que cambian
    assert env["fake"].updated == []


def test_bulk_update_ejecucion_solo_las_que_cambian(env):
    r = env["client"].post(
        "/tenants/lab/accounts/bulk-update",
        json={
            "account_ids": ["1", "2", "3"],
            "changes": {"address": "10.2.2.2"},
            "dry_run": False,
            "reason": "estandarizar firewalls al nuevo segmento, OPS-1",
        },
    )
    assert r.status_code == 200
    updated_ids = {aid for aid, _ in env["fake"].updated}
    assert updated_ids == {"1", "2"}


def test_bulk_update_no_op_no_llama_a_cyberark(env):
    r = env["client"].post(
        "/tenants/lab/accounts/bulk-update",
        json={
            "account_ids": ["3"],
            "changes": {"address": "10.2.2.2"},
            "dry_run": False,
            "reason": "ya estaba en el valor correcto",
        },
    )
    assert r.status_code == 200
    assert r.json()["no_op"] is True
    assert env["fake"].updated == []


def test_bulk_update_dedup_ids(env):
    r = env["client"].post(
        "/tenants/lab/accounts/bulk-update",
        json={
            "account_ids": ["1", "1", "1"],
            "changes": {"address": "10.2.2.2"},
            "dry_run": False,
            "reason": "id repetida en la selección",
        },
    )
    assert r.status_code == 200
    assert len(env["fake"].updated) == 1


def test_bulk_update_cuenta_inexistente_aborta_todo(env):
    env["fake"].missing.add("2")
    r = env["client"].post(
        "/tenants/lab/accounts/bulk-update",
        json={"account_ids": ["1", "2"], "changes": {"address": "10.2.2.2"}},
    )
    assert r.status_code == 404
    assert env["fake"].updated == []


def test_bulk_update_viewer_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["viewer"]
    r = env["client"].post(
        "/tenants/lab/accounts/bulk-update",
        json={"account_ids": ["1"], "changes": {"address": "10.2.2.2"}},
    )
    assert r.status_code == 403


def test_bulk_update_account_ids_vacio_422(env):
    r = env["client"].post(
        "/tenants/lab/accounts/bulk-update",
        json={"account_ids": [], "changes": {"address": "10.2.2.2"}},
    )
    assert r.status_code == 422
