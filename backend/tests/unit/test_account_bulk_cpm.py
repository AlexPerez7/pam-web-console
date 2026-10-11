"""Acciones del CPM en lote — tildar varias cuentas en la grilla y mandarlas
a cambiar/verificar/reconciliar de una vez, como el tilde-múltiple del PVWA
clásico."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.writes import router as writes_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.cyberark import NotFound, UpstreamError
from app.db import get_session
from app.domain.accounts import plan_cpm_bulk
from app.models import AuditLog, Deployment, Operator, OperatorRole, RoleAssignment, Tenant

ACCOUNTS = {
    "1": {"id": "1", "name": "srv-01", "secretManagement": {"automaticManagementEnabled": True}},
    "2": {"id": "2", "name": "srv-02", "secretManagement": {"automaticManagementEnabled": False}},
    "3": {"id": "3", "name": "srv-03", "secretManagement": {"status": "inProcess"}},
}
REASON = "rotación por baja de un operador, TICKET-123"


def test_plan_cpm_bulk_effect_y_warnings_por_cuenta():
    plan = plan_cpm_bulk("change")(ACCOUNTS, {})
    assert plan.is_cpm_action is True
    assert plan.object_count == 3
    assert "3 cuenta(s)" in plan.effect and "srv-01" in plan.effect
    assert any(w.startswith("srv-02:") and "desactivada" in w for w in plan.warnings)
    assert any(w.startswith("srv-03:") and "en curso" in w for w in plan.warnings)
    assert not any(w.startswith("srv-01:") for w in plan.warnings)


def test_plan_cpm_bulk_verify_no_avisa_gestion_desactivada():
    plan = plan_cpm_bulk("verify")(ACCOUNTS, {})
    assert not any("desactivada" in w for w in plan.warnings)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.missing: set[str] = set()
        self.reject: set[str] = set()

    async def get_account(self, account_id: str) -> dict:
        if account_id in self.missing:
            raise NotFound(f"cuenta '{account_id}' no existe")
        return ACCOUNTS[account_id]

    async def _trigger(self, action: str, account_id: str) -> dict:
        if account_id in self.reject:
            raise UpstreamError("ya hay una tarea pendiente")
        self.calls.append((action, account_id))
        return {}

    async def change_account(self, account_id: str) -> dict:
        return await self._trigger("change", account_id)

    async def verify_account(self, account_id: str) -> dict:
        return await self._trigger("verify", account_id)

    async def reconcile_account(self, account_id: str) -> dict:
        return await self._trigger("reconcile", account_id)


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
    op = Operator(
        username="op", password_hash=security.hash_password("x"), role=OperatorRole.viewer
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
    app.include_router(writes_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: op
    return {
        "app": app,
        "client": TestClient(app),
        "fake": fake,
        "viewer": viewer,
        "session": session,
    }


def _post(env, **body):
    return env["client"].post("/tenants/lab/accounts/bulk-cpm", json=body)


def test_dry_run_no_toca_cyberark(env):
    r = _post(env, action="change", account_ids=["1", "2"])
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True and body["is_cpm_action"] is True
    assert "2 cuenta(s)" in body["effect"]
    assert env["fake"].calls == []


def test_ejecuta_todas_dedup(env):
    r = _post(env, action="verify", account_ids=["1", "2", "1"], dry_run=False, reason=REASON)
    assert r.status_code == 200
    assert env["fake"].calls == [("verify", "1"), ("verify", "2")]
    assert r.json()["object"] == {"triggered": ["1", "2"], "failed": []}


async def test_falla_parcial_no_frena_al_resto_y_se_audita(env):
    env["fake"].reject.add("2")
    r = _post(env, action="change", account_ids=["1", "2", "3"], dry_run=False, reason=REASON)
    assert r.status_code == 200
    obj = r.json()["object"]
    assert obj["triggered"] == ["1", "3"]
    assert [f["account_id"] for f in obj["failed"]] == ["2"]
    rows = (
        (
            await env["session"].execute(
                select(AuditLog).where(
                    AuditLog.action == "account.change", AuditLog.dry_run.is_(False)
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows[-1].target == "1,2,3"
    assert rows[-1].detail["failed"][0]["account_id"] == "2"


def test_fallan_todas_es_error(env):
    env["fake"].reject.update({"1", "2"})
    r = _post(env, action="reconcile", account_ids=["1", "2"], dry_run=False, reason=REASON)
    assert r.status_code == 502
    assert env["fake"].calls == []


def test_sin_razon_422(env):
    r = _post(env, action="change", account_ids=["1"], dry_run=False)
    assert r.status_code == 422


def test_cuenta_inexistente_aborta(env):
    env["fake"].missing.add("2")
    r = _post(env, action="change", account_ids=["1", "2"], dry_run=False, reason=REASON)
    assert r.status_code == 404
    assert env["fake"].calls == []


def test_accion_invalida_422(env):
    r = _post(env, action="delete", account_ids=["1"])
    assert r.status_code == 422


def test_supera_el_tope(env, monkeypatch):
    monkeypatch.setattr(writeop.get_settings(), "write_object_cap", 2)
    r = _post(env, action="change", account_ids=["1", "2", "3"])
    assert r.status_code == 409


def test_viewer_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["viewer"]
    r = _post(env, action="change", account_ids=["1"])
    assert r.status_code == 403
