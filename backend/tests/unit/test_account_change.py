"""account.change / verify / reconcile — acciones on-demand del CPM."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.writes import router as writes_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.db import get_session
from app.domain.accounts import plan_cpm
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant


def test_plan_cpm_effect_y_warnings():
    plan = plan_cpm("change")(
        {"secretManagement": {"automaticManagementEnabled": False, "status": "InProcess"}}, {}
    )
    assert plan.patch == [] and plan.diff == []
    assert "cambiar" in plan.effect.lower()
    assert any("gestión automática" in w for w in plan.warnings)
    assert any("en curso" in w for w in plan.warnings)
    assert plan.is_cpm_action is True


def test_plan_cpm_verify_sin_warnings():
    plan = plan_cpm("verify")({"secretManagement": {"automaticManagementEnabled": False}}, {})
    assert plan.warnings == []  # verify no rota, no importa la gestión automática
    assert "verificar" in plan.effect.lower()
    assert plan.is_cpm_action is True


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_account(self, account_id: str) -> dict:
        return {"id": account_id, "secretManagement": {"automaticManagementEnabled": True}}

    async def change_account(self, account_id: str) -> dict:
        self.calls.append("change")
        return {}

    async def verify_account(self, account_id: str) -> dict:
        self.calls.append("verify")
        return {}

    async def reconcile_account(self, account_id: str) -> dict:
        self.calls.append("reconcile")
        return {}


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
        username="op", password_hash=security.hash_password("x"), role=OperatorRole.operator
    )
    session.add(op)
    await session.flush()
    session.add(RoleAssignment(operator_id=op.id, tenant_id="lab", role=OperatorRole.operator))
    await session.flush()

    app = FastAPI()
    app.include_router(writes_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: op
    return {"client": TestClient(app), "fake": fake, "session": session}


def test_verify_dry_run_no_llama_al_cpm(env):
    r = env["client"].post("/tenants/lab/accounts/42/verify", json={"dry_run": True})
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True and body["no_op"] is False
    assert "verificar" in body["effect"].lower()
    assert env["fake"].calls == []


def test_verify_ejecucion(env):
    r = env["client"].post(
        "/tenants/lab/accounts/42/verify",
        json={"dry_run": False, "reason": "chequeo de rutina"},
    )
    assert r.status_code == 200
    assert env["fake"].calls == ["verify"]


async def test_change_y_reconcile(env):
    c = env["client"]
    assert (
        c.post(
            "/tenants/lab/accounts/42/change",
            json={"dry_run": False, "reason": "cuenta en Failure"},
        ).status_code
        == 200
    )
    assert (
        c.post(
            "/tenants/lab/accounts/42/reconcile",
            json={"dry_run": False, "reason": "secreto desincronizado"},
        ).status_code
        == 200
    )
    assert env["fake"].calls == ["change", "reconcile"]
