"""account.create (wizard estilo PVWA) y safe.create.

Ver `docs/specs/plantillas-alta-cuenta-safe.md`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.writes import router as writes_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.cyberark import Conflict, NotFound
from app.db import get_session
from app.domain.accounts import build_account_create_body, plan_account_create
from app.domain.safes import build_safe_create_body, plan_safe_create, suggest_safe_defaults
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant

# "Get Platform Details" real (no expone Required/Optional, ver la nota en
# app/domain/accounts.py) — sólo se usa para simular la validación de
# existencia de la plataforma.
PLATFORM_DETAILS = {"Details": {"PolicyID": "WinDomain"}, "Active": True}


# --- dominio, puro ------------------------------------------------------- #


def test_build_account_create_body_incluye_secreto():
    body = build_account_create_body(
        {
            "safe_name": "WIN-Prod",
            "platform_id": "WinDomain",
            "user_name": "svc-app",
            "address": "10.1.2.30",
            "secret": "s3cr3t!",
            "properties": {"LogonDomain": "CORP"},
        }
    )
    assert body["secret"] == "s3cr3t!"
    assert body["secretType"] == "password"
    assert body["platformAccountProperties"] == {"LogonDomain": "CORP"}


def test_build_account_create_body_sin_secreto():
    body = build_account_create_body(
        {
            "safe_name": "WIN-Prod",
            "platform_id": "WinDomain",
            "user_name": "svc-app",
            "address": "10.1.2.30",
        }
    )
    assert "secret" not in body
    assert "secretType" not in body
    assert "platformAccountProperties" not in body


def test_plan_account_create_nunca_expone_el_secreto():
    plan = plan_account_create(
        {},
        {
            "safe_name": "WIN-Prod",
            "platform_id": "WinDomain",
            "user_name": "svc-app",
            "address": "10.1.2.30",
            "secret": "s3cr3t!",
        },
    )
    field = next(d for d in plan.diff if d["field"] == "contraseña")
    assert field["to"] == "(definida)"
    assert "s3cr3t!" not in str(plan.diff)
    assert "s3cr3t!" not in (plan.effect or "")
    assert plan.warnings == []


def test_build_account_create_body_gestion_manual():
    body = build_account_create_body(
        {
            "safe_name": "S",
            "platform_id": "P",
            "user_name": "u",
            "address": "a",
            "automatic_management_enabled": False,
            "manual_management_reason": "cuenta compartida con el cliente",
        }
    )
    assert body["secretManagement"] == {
        "automaticManagementEnabled": False,
        "manualManagementReason": "cuenta compartida con el cliente",
    }


def test_build_account_create_body_gestion_automatica_no_se_manda():
    body = build_account_create_body(
        {"safe_name": "S", "platform_id": "P", "user_name": "u", "address": "a"}
    )
    assert "secretManagement" not in body


def test_build_account_create_body_remote_machines():
    body = build_account_create_body(
        {
            "safe_name": "S",
            "platform_id": "P",
            "user_name": "u",
            "address": "a",
            "remote_machines": "10.1.2.1;10.1.2.2",
            "access_restricted_to_remote_machines": True,
        }
    )
    assert body["remoteMachinesAccess"] == {
        "remoteMachines": "10.1.2.1;10.1.2.2",
        "accessRestrictedToRemoteMachines": True,
    }


def test_plan_account_create_gestion_manual_y_remote_machines_en_diff():
    plan = plan_account_create(
        {},
        {
            "safe_name": "S",
            "platform_id": "P",
            "user_name": "u",
            "address": "a",
            "automatic_management_enabled": False,
            "manual_management_reason": "motivo",
            "remote_machines": "10.1.2.1;10.1.2.2",
            "access_restricted_to_remote_machines": True,
        },
    )
    fields = {d["field"]: d["to"] for d in plan.diff}
    assert fields["gestión automática"] == "deshabilitada — motivo"
    assert fields["remote machine access"] == "10.1.2.1;10.1.2.2"
    assert fields["acceso restringido a estas máquinas"] is True


def test_plan_account_create_sin_secreto_advierte():
    plan = plan_account_create(
        {},
        {"safe_name": "S", "platform_id": "P", "user_name": "u", "address": "a"},
    )
    assert not any(d["field"] == "contraseña" for d in plan.diff)
    assert any("sin secreto" in w for w in plan.warnings)


def test_suggest_safe_defaults():
    safes = [
        {"managingCPM": "PasswordManager", "numberOfDaysRetention": 90},
        {"managingCPM": "PasswordManager", "numberOfDaysRetention": 90},
        {"managingCPM": "PasswordManager2", "numberOfVersionsRetention": 5},
    ]
    out = suggest_safe_defaults(safes)
    assert out == {
        "managing_cpm": "PasswordManager",
        "retention_type": "days",
        "retention_value": 90,
    }


def test_suggest_safe_defaults_vacio():
    assert suggest_safe_defaults([]) == {}


def test_build_safe_create_body():
    body = build_safe_create_body(
        {
            "safe_name": "WIN-Backup",
            "managing_cpm": "PasswordManager",
            "retention_type": "days",
            "retention_value": 90,
            "olac_enabled": True,
        }
    )
    assert body == {
        "safeName": "WIN-Backup",
        "managingCPM": "PasswordManager",
        "numberOfDaysRetention": 90,
        "olacEnabled": True,
    }


def test_plan_safe_create_effect():
    plan = plan_safe_create({}, {"safe_name": "WIN-Backup", "managing_cpm": "PasswordManager"})
    assert "WIN-Backup" in plan.effect
    assert "PasswordManager" in plan.effect
    assert plan.object_count == 1


# --- endpoints ------------------------------------------------------------ #


class FakeClient:
    def __init__(self) -> None:
        self.created_accounts: list[dict] = []
        self.created_safes: list[dict] = []
        self.safe_exists = True
        self.platform_exists = True
        self.duplicate_safe = False

    async def get_safe(self, safe_id: str) -> dict:
        if not self.safe_exists:
            raise NotFound(f"safe '{safe_id}' no existe")
        return {"safeName": safe_id, "managingCPM": "PasswordManager"}

    async def get_platform_details(self, platform_id: str) -> dict:
        if not self.platform_exists:
            raise NotFound(f"plataforma '{platform_id}' no existe")
        return PLATFORM_DETAILS

    async def create_account(self, body: dict) -> dict:
        self.created_accounts.append(body)
        return {"id": "123_456", **body}

    async def create_safe(self, body: dict) -> dict:
        if self.duplicate_safe:
            raise Conflict("la safe ya existe")
        self.created_safes.append(body)
        return body

    async def list_safes(self) -> list:
        return []


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


ACCOUNT_CHANGES = {
    "safe_name": "WIN-Prod",
    "platform_id": "WinDomain",
    "user_name": "svc-app",
    "address": "10.1.2.30",
    "properties": {"LogonDomain": "CORP"},
}


def test_account_create_dry_run(env):
    r = env["client"].post("/tenants/lab/accounts", json={"changes": ACCOUNT_CHANGES})
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert env["fake"].created_accounts == []
    assert any(d["field"] == "safe" for d in body["diff"])


def test_account_create_dry_run_con_secreto_no_expone_valor(env):
    r = env["client"].post(
        "/tenants/lab/accounts",
        json={"changes": {**ACCOUNT_CHANGES, "secret": "s3cr3t!"}},
    )
    assert r.status_code == 200
    text = r.text
    assert "s3cr3t!" not in text
    assert "(definida)" in text


def test_account_create_ejecucion(env):
    r = env["client"].post(
        "/tenants/lab/accounts",
        json={
            "changes": ACCOUNT_CHANGES,
            "dry_run": False,
            "reason": "onboarding servidor nuevo, ticket OPS-1",
        },
    )
    assert r.status_code == 200
    assert len(env["fake"].created_accounts) == 1
    assert env["fake"].created_accounts[0]["safeName"] == "WIN-Prod"


def test_account_create_safe_inexistente(env):
    env["fake"].safe_exists = False
    r = env["client"].post("/tenants/lab/accounts", json={"changes": ACCOUNT_CHANGES})
    assert r.status_code == 404


def test_account_create_plataforma_inexistente(env):
    env["fake"].platform_exists = False
    r = env["client"].post("/tenants/lab/accounts", json={"changes": ACCOUNT_CHANGES})
    assert r.status_code == 404


def test_account_create_viewer_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["viewer"]
    r = env["client"].post("/tenants/lab/accounts", json={"changes": ACCOUNT_CHANGES})
    assert r.status_code == 403


def test_account_create_campos_obligatorios(env):
    r = env["client"].post("/tenants/lab/accounts", json={"changes": {"safe_name": "S"}})
    assert r.status_code == 422


def test_account_create_gestion_manual_sin_motivo(env):
    changes = {**ACCOUNT_CHANGES, "automatic_management_enabled": False}
    r = env["client"].post("/tenants/lab/accounts", json={"changes": changes})
    assert r.status_code == 422
    assert "manual_management_reason" in r.json()["detail"]


def test_account_create_gestion_manual_con_motivo(env):
    changes = {
        **ACCOUNT_CHANGES,
        "automatic_management_enabled": False,
        "manual_management_reason": "cuenta compartida con el cliente",
    }
    r = env["client"].post("/tenants/lab/accounts", json={"changes": changes})
    assert r.status_code == 200


SAFE_CHANGES = {"safe_name": "WIN-Backup", "managing_cpm": "PasswordManager"}


def test_safe_create_dry_run(env):
    r = env["client"].post("/tenants/lab/safes", json={"changes": SAFE_CHANGES})
    assert r.status_code == 200
    assert r.json()["dry_run"] is True
    assert env["fake"].created_safes == []


def test_safe_create_ejecucion(env):
    r = env["client"].post(
        "/tenants/lab/safes",
        json={"changes": SAFE_CHANGES, "dry_run": False, "reason": "onboarding cliente nuevo"},
    )
    assert r.status_code == 200
    assert env["fake"].created_safes == [
        {"safeName": "WIN-Backup", "managingCPM": "PasswordManager", "olacEnabled": False}
    ]


def test_safe_create_duplicada(env):
    env["fake"].duplicate_safe = True
    r = env["client"].post(
        "/tenants/lab/safes",
        json={"changes": SAFE_CHANGES, "dry_run": False, "reason": "onboarding cliente nuevo"},
    )
    assert r.status_code == 409


def test_safe_create_viewer_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["viewer"]
    r = env["client"].post("/tenants/lab/safes", json={"changes": SAFE_CHANGES})
    assert r.status_code == 403
