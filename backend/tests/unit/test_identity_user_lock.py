"""identity_user.lock — bloquear/desbloquear un usuario de Identity/SSO."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.writes import router as writes_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.db import get_session
from app.domain.identity_users import plan_identity_user_lock
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant


def test_plan_bloquear():
    plan = plan_identity_user_lock(True)({"username": "jdoe", "locked": False}, {})
    assert plan.patch == [] and plan.diff == []
    assert "jdoe" in plan.effect and "bloquea" in plan.effect
    assert plan.warnings == []


def test_plan_desbloquear():
    plan = plan_identity_user_lock(False)({"username": "jdoe", "locked": True}, {})
    assert "desbloquea" in plan.effect
    assert plan.warnings == []


def test_plan_ya_bloqueado():
    plan = plan_identity_user_lock(True)({"username": "jdoe", "locked": True}, {})
    assert plan.effect is None
    assert any("ya está bloqueado" in w for w in plan.warnings)


def test_plan_ya_activo():
    plan = plan_identity_user_lock(False)({"username": "jdoe", "locked": False}, {})
    assert plan.effect is None
    assert any("ya está activo" in w for w in plan.warnings)


class FakeClient:
    def __init__(self, locked: bool = False) -> None:
        self.locked = locked
        self.lock_calls: list[tuple[str, bool]] = []

    async def identity_get_user_info(self, user_id: str) -> dict:
        return {"Username": "jdoe", "ID": user_id}

    async def identity_is_user_locked(self, user: str) -> bool:
        return self.locked

    async def identity_set_cloud_lock(self, user_id: str, lock: bool) -> None:
        self.lock_calls.append((user_id, lock))
        self.locked = lock


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


def test_dry_run_lock(env):
    r = env["client"].post("/tenants/lab/identity-users/u-1/lock", json={"dry_run": True})
    assert r.status_code == 200
    assert r.json()["effect"] and env["fake"].lock_calls == []


def test_ejecucion_lock(env):
    r = env["client"].post(
        "/tenants/lab/identity-users/u-1/lock",
        json={"dry_run": False, "reason": "ticket OPS-12, dispositivo comprometido"},
    )
    assert r.status_code == 200
    assert env["fake"].lock_calls == [("u-1", True)]


def test_ejecucion_unlock(env):
    env["fake"].locked = True
    r = env["client"].post(
        "/tenants/lab/identity-users/u-1/unlock",
        json={"dry_run": False, "reason": "usuario recuperó el dispositivo, ticket OPS-13"},
    )
    assert r.status_code == 200
    assert env["fake"].lock_calls == [("u-1", False)]


def test_viewer_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["viewer"]
    r = env["client"].post("/tenants/lab/identity-users/u-1/lock", json={"dry_run": True})
    assert r.status_code == 403
