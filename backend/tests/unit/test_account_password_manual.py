"""account.change.vault_only / account.change.next — fijar un valor de
contraseña específico (no aleatorio del CPM), replicando las opciones
"Change credentials in the Vault" y "Set next password" del PVWA. Ver
docs/specs/account-password-manual.md."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.writes import router as writes_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.db import get_session
from app.domain.accounts import plan_password_set_next, plan_password_vault_only
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant

ACCOUNT = {"id": "42_1", "name": "db-prod-01", "safeName": "SQL-PROD"}


def test_plan_password_vault_only_no_expone_valor():
    plan = plan_password_vault_only(ACCOUNT, {"new_password": "s3cr3t!"})
    assert "s3cr3t!" not in str(plan.diff)
    assert plan.diff[0]["to"] == "(definida)"
    assert plan.confirm_phrase is None
    assert any("no coincide" in w for w in plan.warnings)


def test_plan_password_set_next_no_expone_valor():
    plan = plan_password_set_next(ACCOUNT, {"new_password": "s3cr3t!"})
    assert "s3cr3t!" not in str(plan.diff)
    assert "próxima" in plan.effect
    assert plan.warnings == []


class FakeClient:
    def __init__(self) -> None:
        self.account: dict = dict(ACCOUNT)
        self.vault_updates: list[tuple[str, str]] = []
        self.next_updates: list[tuple[str, str]] = []

    async def get_account(self, account_id: str) -> dict:
        return self.account

    async def update_password_in_vault(self, account_id: str, new_password: str) -> None:
        self.vault_updates.append((account_id, new_password))

    async def set_next_password(self, account_id: str, new_password: str) -> None:
        self.next_updates.append((account_id, new_password))


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
    tenant_admin = Operator(
        username="ta", password_hash=security.hash_password("x"), role=OperatorRole.tenant_admin
    )
    approver = Operator(
        username="ap", password_hash=security.hash_password("x"), role=OperatorRole.approver
    )
    session.add_all([tenant_admin, approver])
    await session.flush()
    session.add_all(
        [
            RoleAssignment(
                operator_id=tenant_admin.id, tenant_id="lab", role=OperatorRole.tenant_admin
            ),
            RoleAssignment(operator_id=approver.id, tenant_id="lab", role=OperatorRole.approver),
        ]
    )
    await session.flush()

    app = FastAPI()
    app.include_router(writes_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: tenant_admin
    return {
        "app": app,
        "client": TestClient(app),
        "fake": fake,
        "tenant_admin": tenant_admin,
        "approver": approver,
        "session": session,
    }


REASON = "la password real ya se cambió a mano en el server, OPS-1"


def test_change_in_vault_dry_run_no_toca_nada(env):
    r = env["client"].post(
        "/tenants/lab/accounts/42_1/change-in-vault",
        json={"changes": {"new_password": "s3cr3t!"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert "s3cr3t!" not in r.text
    assert env["fake"].vault_updates == []


def test_change_in_vault_ejecucion(env):
    r = env["client"].post(
        "/tenants/lab/accounts/42_1/change-in-vault",
        json={"changes": {"new_password": "s3cr3t!"}, "dry_run": False, "reason": REASON},
    )
    assert r.status_code == 200
    assert env["fake"].vault_updates == [("42_1", "s3cr3t!")]
    assert "s3cr3t!" not in r.text


def test_change_in_vault_rbac_approver_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["approver"]
    r = env["client"].post(
        "/tenants/lab/accounts/42_1/change-in-vault",
        json={"changes": {"new_password": "s3cr3t!"}},
    )
    assert r.status_code == 403


def test_set_next_password_ejecucion(env):
    r = env["client"].post(
        "/tenants/lab/accounts/42_1/set-next-password",
        json={"changes": {"new_password": "n3xt!"}, "dry_run": False, "reason": REASON},
    )
    assert r.status_code == 200
    assert env["fake"].next_updates == [("42_1", "n3xt!")]
    assert env["fake"].vault_updates == []
    assert "n3xt!" not in r.text


def test_set_next_password_rbac_approver_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["approver"]
    r = env["client"].post(
        "/tenants/lab/accounts/42_1/set-next-password",
        json={"changes": {"new_password": "n3xt!"}},
    )
    assert r.status_code == 403


def test_manual_password_campo_obligatorio(env):
    r = env["client"].post(
        "/tenants/lab/accounts/42_1/change-in-vault", json={"changes": {"new_password": ""}}
    )
    assert r.status_code == 422
