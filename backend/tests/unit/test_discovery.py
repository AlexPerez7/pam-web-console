"""discovered_account.* — cuentas descubiertas por CyberArk Discovery
(Gen2, Privilege Cloud/ISPSS). Endpoints verificados contra la doc oficial
de CyberArk (docs.cyberark.com/manage/latest/en/content/discovery/
discovery-discoveredaccountsservice-*.htm). Ver docs/specs/done/discovery.md.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.discovery as discovery_mod
from app.api.discovery import router as discovery_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.cyberark import NotFound
from app.db import get_session
from app.domain.discovery import (
    plan_discovered_account_delete,
    plan_discovered_account_ignore,
    plan_discovered_account_onboard,
    plan_discovered_account_restore,
)
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant

DISCOVERED = {
    "id": "da-1",
    "type": "Windows",
    "subType": "loosely",
    "identifiers": {"username": "user1", "address": "test-computer.cyber-ark.com"},
    "source": "EPM",
    "isPrivileged": True,
    "customProperties": {"osFamily": "Workstation"},
}


def test_plan_onboard_effect_y_warning():
    plan = plan_discovered_account_onboard(
        DISCOVERED, {"safe_name": "WinSafe", "platform_id": "WinDomain", "reset_secret": False}
    )
    assert "user1@test-computer.cyber-ark.com" in plan.effect
    assert "WinSafe" in plan.effect
    assert "WinDomain" in plan.effect
    assert plan.diff == []
    assert plan.confirm_phrase is None
    assert any("NO se rota" in w for w in plan.warnings)


def test_plan_onboard_sin_warning_con_reset_secret_default():
    plan = plan_discovered_account_onboard(
        DISCOVERED, {"safe_name": "WinSafe", "platform_id": "WinDomain", "reset_secret": True}
    )
    assert plan.warnings == []


def test_plan_ignore_restore_delete_effect():
    for build_plan in (
        plan_discovered_account_ignore,
        plan_discovered_account_restore,
        plan_discovered_account_delete,
    ):
        plan = build_plan(DISCOVERED, {})
        assert "user1@test-computer.cyber-ark.com" in plan.effect
        assert plan.diff == []
        assert plan.confirm_phrase is None


class FakeClient:
    def __init__(self) -> None:
        self.accounts = {"da-1": dict(DISCOVERED)}
        self.activities = {"da-1": [{"id": "act-1", "activity": "ACTION_SUCCEEDED"}]}
        self.onboarded: list[dict] = []
        self.ignored: list[str] = []
        self.restored: list[str] = []
        self.deleted: list[str] = []

    async def list_discovered_accounts(
        self, *, search: str | None = None, offset: int = 0, limit: int = 50
    ) -> dict:
        return {"items": list(self.accounts.values()), "totalCount": len(self.accounts)}

    async def get_discovered_account(self, discovered_account_id: str, **_kw) -> dict:
        if discovered_account_id not in self.accounts:
            raise NotFound(f"cuenta descubierta '{discovered_account_id}' no existe")
        return self.accounts[discovered_account_id]

    async def get_discovered_account_activities(self, discovered_account_id: str) -> list:
        if discovered_account_id not in self.accounts:
            raise NotFound(f"cuenta descubierta '{discovered_account_id}' no existe")
        return self.activities.get(discovered_account_id, [])

    async def onboard_discovered_account(self, discovered_account_id: str, **kw) -> dict:
        self.onboarded.append({"id": discovered_account_id, **kw})
        return {"id": discovered_account_id, "accountId": "acc-99"}

    async def ignore_discovered_account(self, discovered_account_id: str, **_kw) -> None:
        self.ignored.append(discovered_account_id)

    async def restore_discovered_account(self, discovered_account_id: str) -> None:
        self.restored.append(discovered_account_id)

    async def delete_discovered_account(self, discovered_account_id: str) -> None:
        self.deleted.append(discovered_account_id)


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    fake = FakeClient()

    @asynccontextmanager
    async def fake_tc(tenant_id: str, sess):
        yield (None, fake)

    monkeypatch.setattr(discovery_mod, "tenant_client", fake_tc)
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
    app.include_router(discovery_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: operator_op
    return {"app": app, "client": TestClient(app), "fake": fake, "viewer": viewer}


REASON = "onboarding a pedido del cliente, ticket OPS-7"


def test_list_discovered_accounts_mapea_items_totalcount(env):
    r = env["client"].get("/tenants/lab/discovered-accounts")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["accounts"][0]["id"] == "da-1"


def test_get_activities(env):
    r = env["client"].get("/tenants/lab/discovered-accounts/da-1/activities")
    assert r.status_code == 200
    assert r.json()["activities"][0]["id"] == "act-1"


def test_onboard_dry_run_no_toca_cyberark(env):
    r = env["client"].post(
        "/tenants/lab/discovered-accounts/da-1/onboard",
        json={"changes": {"safe_name": "WinSafe", "platform_id": "WinDomain"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert "WinSafe" in body["effect"]
    assert env["fake"].onboarded == []


def test_onboard_ejecucion_llama_al_endpoint_real(env):
    r = env["client"].post(
        "/tenants/lab/discovered-accounts/da-1/onboard",
        json={
            "changes": {
                "safe_name": "WinSafe",
                "platform_id": "WinDomain",
                "secret": "S3cret!",
            },
            "dry_run": False,
            "reason": REASON,
        },
    )
    assert r.status_code == 200
    assert env["fake"].onboarded == [
        {
            "id": "da-1",
            "safe_name": "WinSafe",
            "platform_id": "WinDomain",
            "additional_properties": None,
            "tags": None,
            "secret": "S3cret!",
            "reset_secret": True,
        }
    ]


def test_onboard_secreto_no_aparece_en_el_diff(env):
    r = env["client"].post(
        "/tenants/lab/discovered-accounts/da-1/onboard",
        json={
            "changes": {
                "safe_name": "WinSafe",
                "platform_id": "WinDomain",
                "secret": "S3cret!",
            }
        },
    )
    body = r.json()
    assert "S3cret!" not in str(body)


def test_onboard_cuenta_inexistente_404(env):
    r = env["client"].post(
        "/tenants/lab/discovered-accounts/no-existe/onboard",
        json={"changes": {"safe_name": "WinSafe", "platform_id": "WinDomain"}},
    )
    assert r.status_code == 404


def test_onboard_rbac_viewer_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["viewer"]
    r = env["client"].post(
        "/tenants/lab/discovered-accounts/da-1/onboard",
        json={"changes": {"safe_name": "WinSafe", "platform_id": "WinDomain"}},
    )
    assert r.status_code == 403


def test_ignore_ejecucion(env):
    r = env["client"].post(
        "/tenants/lab/discovered-accounts/da-1/ignore",
        json={"dry_run": False, "reason": REASON},
    )
    assert r.status_code == 200
    assert env["fake"].ignored == ["da-1"]


def test_restore_ejecucion(env):
    r = env["client"].post(
        "/tenants/lab/discovered-accounts/da-1/restore",
        json={"dry_run": False, "reason": REASON},
    )
    assert r.status_code == 200
    assert env["fake"].restored == ["da-1"]


def test_delete_ejecucion(env):
    r = env["client"].request(
        "DELETE",
        "/tenants/lab/discovered-accounts/da-1",
        json={"dry_run": False, "reason": REASON},
    )
    assert r.status_code == 200
    assert env["fake"].deleted == ["da-1"]


def test_delete_dry_run_no_borra(env):
    r = env["client"].request("DELETE", "/tenants/lab/discovered-accounts/da-1", json={})
    assert r.status_code == 200
    assert r.json()["dry_run"] is True
    assert env["fake"].deleted == []
