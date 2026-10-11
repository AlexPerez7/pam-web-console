"""account.link — vincular una cuenta de logon / reconciliación."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.writes import router as writes_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.db import get_session
from app.domain.accounts import account_link_body, plan_account_link
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant


def test_link_body_indices():
    assert (
        account_link_body({"link_type": "logon", "linked_safe": "S", "linked_name": "a"})[
            "extraPasswordIndex"
        ]
        == 1
    )
    b = account_link_body({"link_type": "reconcile", "linked_safe": "S", "linked_name": "a"})
    assert b["extraPasswordIndex"] == 3
    assert b["folder"] == "Root"


def test_plan_reconcile_diff_y_effect():
    plan = plan_account_link(
        {"name": "target", "safeName": "T", "secretManagement": {}},
        {"link_type": "reconcile", "linked_safe": "LinuxRecon", "linked_name": "svc-recon"},
    )
    assert plan.patch == []
    fields = {d["field"]: d["to"] for d in plan.diff}
    assert "índice 3" in fields["tipo"]
    assert fields["cuenta vinculada"] == "svc-recon"
    assert "reconciliación" in plan.effect
    assert plan.warnings == []


def test_plan_auto_vinculo():
    plan = plan_account_link(
        {"name": "svc", "safeName": "S"},
        {"link_type": "logon", "linked_safe": "S", "linked_name": "svc"},
    )
    assert any("a sí misma" in w for w in plan.warnings)


def test_plan_reconcile_gestion_off():
    plan = plan_account_link(
        {"name": "t", "safeName": "T", "secretManagement": {"automaticManagementEnabled": False}},
        {"link_type": "reconcile", "linked_safe": "R", "linked_name": "r"},
    )
    assert any("gestión automática" in w for w in plan.warnings)


class FakeClient:
    def __init__(self) -> None:
        self.linked: list[dict] = []

    async def get_account(self, account_id: str) -> dict:
        return {"id": account_id, "name": "target", "safeName": "T", "secretManagement": {}}

    async def link_account(self, account_id: str, body: dict) -> dict:
        self.linked.append(body)
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
    r = env["client"].post(
        "/tenants/lab/accounts/42/link",
        json={"changes": {"link_type": "reconcile", "linked_safe": "R", "linked_name": "svc-r"}},
    )
    assert r.status_code == 200
    assert r.json()["dry_run"] is True and env["fake"].linked == []


def test_ejecucion(env):
    r = env["client"].post(
        "/tenants/lab/accounts/42/link",
        json={
            "changes": {"link_type": "logon", "linked_safe": "L", "linked_name": "svc-logon"},
            "dry_run": False,
            "reason": "onboarding, logon para LinuxSSH",
        },
    )
    assert r.status_code == 200
    assert env["fake"].linked == [
        {"safe": "L", "folder": "Root", "name": "svc-logon", "extraPasswordIndex": 1}
    ]


def test_viewer_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["viewer"]
    r = env["client"].post(
        "/tenants/lab/accounts/42/link",
        json={"changes": {"linked_safe": "L", "linked_name": "x"}},
    )
    assert r.status_code == 403


def test_validacion_campos_obligatorios(env):
    r = env["client"].post("/tenants/lab/accounts/42/link", json={"changes": {"linked_safe": "L"}})
    assert r.status_code == 422
