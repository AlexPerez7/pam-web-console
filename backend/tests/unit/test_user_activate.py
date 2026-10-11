"""user.activate — reactivar un usuario suspendido del Vault."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.writes import router as writes_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.db import get_session
from app.domain.users import plan_user_activate
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant


def test_plan_suspendido():
    plan = plan_user_activate({"username": "jdoe", "suspended": True, "enableUser": True}, {})
    assert plan.patch == [] and plan.diff == []
    assert "jdoe" in plan.effect
    assert plan.warnings == []


def test_plan_no_suspendido():
    plan = plan_user_activate({"username": "jdoe", "suspended": False}, {})
    assert any("no está suspendido" in w for w in plan.warnings)


def test_plan_deshabilitado():
    plan = plan_user_activate({"username": "jdoe", "suspended": True, "enableUser": False}, {})
    assert any("deshabilitado" in w for w in plan.warnings)


class FakeClient:
    def __init__(self, suspended: bool = True) -> None:
        self.suspended = suspended
        self.activated: list[str] = []

    async def get_user(self, user_id: str) -> dict:
        return {"id": user_id, "username": "jdoe", "suspended": self.suspended, "enableUser": True}

    async def activate_user(self, user_id: str) -> dict:
        self.activated.append(user_id)
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
    return {"app": app, "client": TestClient(app), "fake": fake, "viewer": viewer}


def test_dry_run(env):
    r = env["client"].post("/tenants/lab/users/7/activate", json={"dry_run": True})
    assert r.status_code == 200
    assert r.json()["effect"] and env["fake"].activated == []


def test_ejecucion(env):
    r = env["client"].post(
        "/tenants/lab/users/7/activate",
        json={"dry_run": False, "reason": "usuario reporta bloqueo OPS-9"},
    )
    assert r.status_code == 200
    assert env["fake"].activated == ["7"]


def test_viewer_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["viewer"]
    r = env["client"].post("/tenants/lab/users/7/activate", json={"dry_run": True})
    assert r.status_code == 403
