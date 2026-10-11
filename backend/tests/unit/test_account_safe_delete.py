"""Fase 3: account.delete / safe.delete + backups (list/reveal/restore).

Ver docs/specs/account-safe-delete.md.
"""

from __future__ import annotations

import base64
import types
from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.backups import router as backups_router
from app.api.writes import router as writes_router
from app.core import backups as backups_mod
from app.core import security, writeop
from app.core.backups import decrypt_secret, encrypt_secret, get_backup, save_account_backup
from app.core.deps import current_operator
from app.db import get_session
from app.domain.accounts import account_restore_changes, build_account_delete_plan
from app.domain.safes import build_safe_delete_plan
from app.models import (
    AccountBackup,
    AuditLog,
    Deployment,
    Operator,
    OperatorRole,
    RoleAssignment,
    Tenant,
)

ACCOUNT = {
    "id": "42_1",
    "name": "db-prod-01",
    "userName": "svc-db",
    "address": "10.1.2.30",
    "safeName": "SQL-PROD",
    "platformId": "WinServerLocal",
}

SAFE_EMPTY = {"safeName": "TEMP-MIGRACION", "description": "temporal", "managingCPM": "CPM1"}


def _fake_age_run(cmd, input=None, capture_output=True, check=True, timeout=None):
    """Sustituye al binario `age` real (no está instalado en este entorno de
    test) por un base64 reversible — sólo para probar que encrypt/decrypt
    hacen el roundtrip correcto y usan el cmd esperado, no la seguridad real
    del cifrado (eso lo da `age`)."""
    if cmd[1] == "-r":
        out = base64.b64encode(input)
    elif cmd[1] == "--decrypt":
        out = base64.b64decode(input)
    else:
        raise AssertionError(f"comando age inesperado: {cmd}")
    return types.SimpleNamespace(stdout=out, stderr=b"")


# --- app.core.backups: cifrado -------------------------------------------- #


async def test_encrypt_decrypt_roundtrip(monkeypatch):
    monkeypatch.setattr(backups_mod.subprocess, "run", _fake_age_run)
    ciphertext = await encrypt_secret("s3cr3t!")
    assert ciphertext != b"s3cr3t!"
    assert await decrypt_secret(ciphertext) == "s3cr3t!"


async def test_save_account_backup_roundtrip(session, monkeypatch):
    monkeypatch.setattr(backups_mod.subprocess, "run", _fake_age_run)
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
    await session.flush()

    row = await save_account_backup(
        session,
        tenant_id="lab",
        account=ACCOUNT,
        secret="s3cr3t!",
        deleted_by="ap",
        reason="cuenta dada de baja, ticket OPS-1",
    )
    assert row.account_name == "db-prod-01"
    assert row.safe_name == "SQL-PROD"
    assert row.secret_ciphertext != b"s3cr3t!"

    fetched = await get_backup(session, "lab", row.id)
    assert fetched is not None
    assert await decrypt_secret(fetched.secret_ciphertext) == "s3cr3t!"


async def test_get_backup_scoped_por_tenant(session, monkeypatch):
    monkeypatch.setattr(backups_mod.subprocess, "run", _fake_age_run)
    for tid in ("lab", "otro"):
        session.add(
            Tenant(
                id=tid,
                name=tid,
                client="A",
                deployment=Deployment.privilege_cloud,
                pvwa_url="https://x",
                secret_ref=tid,
            )
        )
    await session.flush()
    row = await save_account_backup(
        session,
        tenant_id="lab",
        account=ACCOUNT,
        secret="x",
        deleted_by="ap",
        reason="motivo largo",
    )
    assert await get_backup(session, "otro", row.id) is None
    assert await get_backup(session, "lab", row.id) is not None


# --- dominio: planes de borrado -------------------------------------------- #


def test_build_account_delete_plan():
    plan = build_account_delete_plan(ACCOUNT, {})
    fields = {d["field"]: (d["from"], d["to"]) for d in plan.diff}
    assert fields["nombre"] == ("db-prod-01", None)
    assert fields["safe"] == ("SQL-PROD", None)
    assert plan.confirm_phrase == "db-prod-01"
    assert "copia de seguridad" in plan.effect


def test_build_safe_delete_plan():
    plan = build_safe_delete_plan({"safe": SAFE_EMPTY}, {})
    fields = {d["field"]: (d["from"], d["to"]) for d in plan.diff}
    assert fields["nombre"] == ("TEMP-MIGRACION", None)
    assert plan.confirm_phrase == "TEMP-MIGRACION"


def test_account_restore_changes_basico():
    changes = account_restore_changes(17, ACCOUNT, "s3cr3t!")
    assert changes["safe_name"] == "SQL-PROD"
    assert changes["platform_id"] == "WinServerLocal"
    assert changes["user_name"] == "svc-db"
    assert changes["secret"] == "s3cr3t!"
    assert changes["automatic_management_enabled"] is True
    assert changes["manual_management_reason"] is None


def test_account_restore_changes_gestion_manual():
    metadata = {**ACCOUNT, "secretManagement": {"automaticManagementEnabled": False}}
    changes = account_restore_changes(17, metadata, "s3cr3t!")
    assert changes["automatic_management_enabled"] is False
    assert "backup #17" in changes["manual_management_reason"]


# --- endpoints -------------------------------------------------------------- #


class FakeClient:
    def __init__(self) -> None:
        self.account: dict = dict(ACCOUNT)
        self.safe: dict = dict(SAFE_EMPTY)
        self.accounts_in_safe = 0
        self.deleted_accounts: list[str] = []
        self.deleted_safes: list[str] = []
        self.retrieved: list[tuple[str, str]] = []
        self.created_accounts: list[dict] = []
        self.password = "s3cr3t!"

    async def get_account(self, account_id: str) -> dict:
        return self.account

    async def get_safe(self, safe_id: str) -> dict:
        return self.safe

    async def accounts_page(
        self, *, safe_name=None, limit=100, offset=0, **_kw
    ) -> tuple[list, int]:
        return [], self.accounts_in_safe

    async def retrieve_password(self, account_id: str, reason: str) -> str:
        self.retrieved.append((account_id, reason))
        return self.password

    async def delete_account(self, account_id: str) -> None:
        self.deleted_accounts.append(account_id)

    async def delete_safe(self, safe_id: str) -> None:
        self.deleted_safes.append(safe_id)

    async def get_platform_details(self, platform_id: str) -> dict:
        return {"Details": {}, "Active": True}

    async def create_account(self, body: dict) -> dict:
        self.created_accounts.append(body)
        return {"id": "99_1", **body}


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(backups_mod.subprocess, "run", _fake_age_run)

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
    approver = Operator(
        username="ap", password_hash=security.hash_password("x"), role=OperatorRole.approver
    )
    operator_op = Operator(
        username="op", password_hash=security.hash_password("x"), role=OperatorRole.operator
    )
    tenant_admin = Operator(
        username="ta", password_hash=security.hash_password("x"), role=OperatorRole.tenant_admin
    )
    session.add_all([approver, operator_op, tenant_admin])
    await session.flush()
    session.add_all(
        [
            RoleAssignment(operator_id=approver.id, tenant_id="lab", role=OperatorRole.approver),
            RoleAssignment(operator_id=operator_op.id, tenant_id="lab", role=OperatorRole.operator),
            RoleAssignment(
                operator_id=tenant_admin.id, tenant_id="lab", role=OperatorRole.tenant_admin
            ),
        ]
    )
    await session.flush()

    app = FastAPI()
    app.include_router(writes_router)
    app.include_router(backups_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: approver
    return {
        "app": app,
        "client": TestClient(app),
        "fake": fake,
        "approver": approver,
        "operator_op": operator_op,
        "tenant_admin": tenant_admin,
        "session": session,
    }


# --- account.delete --------------------------------------------------------- #


def test_account_delete_dry_run_no_toca_nada(env):
    r = env["client"].request("DELETE", "/tenants/lab/accounts/42_1", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert body["confirm_phrase"] == "db-prod-01"
    assert env["fake"].retrieved == []
    assert env["fake"].deleted_accounts == []


def test_account_delete_ejecucion_respalda_antes_de_borrar(env):
    r = env["client"].request(
        "DELETE",
        "/tenants/lab/accounts/42_1",
        json={
            "dry_run": False,
            "reason": "cuenta dada de baja, ticket OPS-1",
            "confirm": "db-prod-01",
        },
    )
    assert r.status_code == 200
    assert env["fake"].retrieved == [("42_1", "cuenta dada de baja, ticket OPS-1")]
    assert env["fake"].deleted_accounts == ["42_1"]


async def test_account_delete_deja_backup_recuperable(env):
    from sqlalchemy import select

    env["client"].request(
        "DELETE",
        "/tenants/lab/accounts/42_1",
        json={
            "dry_run": False,
            "reason": "cuenta dada de baja, ticket OPS-1",
            "confirm": "db-prod-01",
        },
    )
    rows = (await env["session"].execute(select(AccountBackup))).scalars().all()
    assert len(rows) == 1
    assert rows[0].account_name == "db-prod-01"
    assert await decrypt_secret(rows[0].secret_ciphertext) == "s3cr3t!"


def test_account_delete_confirm_mismatch_422(env):
    r = env["client"].request(
        "DELETE",
        "/tenants/lab/accounts/42_1",
        json={"dry_run": False, "reason": "motivo largo suficiente", "confirm": "DB-PROD-01"},
    )
    assert r.status_code == 422
    assert env["fake"].deleted_accounts == []
    assert env["fake"].retrieved == []


def test_account_delete_rbac_operator_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["operator_op"]
    r = env["client"].request("DELETE", "/tenants/lab/accounts/42_1", json={})
    assert r.status_code == 403


# --- safe.delete -------------------------------------------------------------- #


def test_safe_delete_vacia_ok(env):
    r = env["client"].request(
        "DELETE",
        "/tenants/lab/safes/TEMP-MIGRACION",
        json={"dry_run": False, "reason": "safe temporal sin uso", "confirm": "TEMP-MIGRACION"},
    )
    assert r.status_code == 200
    assert env["fake"].deleted_safes == ["TEMP-MIGRACION"]


def test_safe_delete_con_cuentas_bloqueada(env):
    env["fake"].accounts_in_safe = 12
    r = env["client"].request("DELETE", "/tenants/lab/safes/TEMP-MIGRACION", json={})
    assert r.status_code == 422
    assert "12 cuenta" in r.json()["detail"]
    assert env["fake"].deleted_safes == []


def test_safe_delete_confirm_mismatch(env):
    r = env["client"].request(
        "DELETE",
        "/tenants/lab/safes/TEMP-MIGRACION",
        json={"dry_run": False, "reason": "safe temporal sin uso", "confirm": "otra-cosa"},
    )
    assert r.status_code == 422
    assert env["fake"].deleted_safes == []


def test_safe_delete_rbac_operator_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["operator_op"]
    r = env["client"].request("DELETE", "/tenants/lab/safes/TEMP-MIGRACION", json={})
    assert r.status_code == 403


# --- backups: list / reveal / restore ---------------------------------------- #


async def _make_backup(env) -> int:
    row = await save_account_backup(
        env["session"],
        tenant_id="lab",
        account=ACCOUNT,
        secret="s3cr3t!",
        deleted_by="ap",
        reason="cuenta dada de baja, ticket OPS-1",
    )
    return row.id


async def test_backups_list(env):
    await _make_backup(env)
    env["app"].dependency_overrides[current_operator] = lambda: env["tenant_admin"]
    r = env["client"].get("/tenants/lab/account-backups")
    assert r.status_code == 200
    rows = r.json()["backups"]
    assert len(rows) == 1
    assert rows[0]["account_name"] == "db-prod-01"
    assert "password" not in rows[0] and "secret_ciphertext" not in rows[0]


def test_backups_list_rbac_operator_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["operator_op"]
    r = env["client"].get("/tenants/lab/account-backups")
    assert r.status_code == 403


async def test_backup_reveal_tenant_admin_ok(env):
    bid = await _make_backup(env)
    env["app"].dependency_overrides[current_operator] = lambda: env["tenant_admin"]
    r = env["client"].post(
        f"/tenants/lab/account-backups/{bid}/reveal", json={"reason": "verificación tras restore"}
    )
    assert r.status_code == 200
    assert r.json()["password"] == "s3cr3t!"


async def test_backup_reveal_operator_rechazado(env):
    bid = await _make_backup(env)
    env["app"].dependency_overrides[current_operator] = lambda: env["operator_op"]
    r = env["client"].post(
        f"/tenants/lab/account-backups/{bid}/reveal", json={"reason": "verificación tras restore"}
    )
    assert r.status_code == 403


async def test_backup_reveal_sin_reason(env):
    bid = await _make_backup(env)
    env["app"].dependency_overrides[current_operator] = lambda: env["tenant_admin"]
    r = env["client"].post(f"/tenants/lab/account-backups/{bid}/reveal", json={"reason": "corta"})
    assert r.status_code == 422


async def test_backup_reveal_audita_sin_el_secreto(env):
    from sqlalchemy import select

    bid = await _make_backup(env)
    env["app"].dependency_overrides[current_operator] = lambda: env["tenant_admin"]
    env["client"].post(
        f"/tenants/lab/account-backups/{bid}/reveal", json={"reason": "verificación tras restore"}
    )
    rows = (
        (
            await env["session"].execute(
                select(AuditLog).where(AuditLog.action == "account.backup.reveal")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert "s3cr3t!" not in str(rows[0].detail)


async def test_backup_restore_dry_run(env):
    bid = await _make_backup(env)
    r = env["client"].post(f"/tenants/lab/account-backups/{bid}/restore", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert env["fake"].created_accounts == []


async def test_backup_restore_ejecucion(env):
    bid = await _make_backup(env)
    r = env["client"].post(
        f"/tenants/lab/account-backups/{bid}/restore",
        json={
            "dry_run": False,
            "reason": "restaurar cuenta borrada por error",
            "confirm": "db-prod-01",
        },
    )
    assert r.status_code == 200
    assert len(env["fake"].created_accounts) == 1
    assert env["fake"].created_accounts[0]["secret"] == "s3cr3t!"
    assert env["fake"].created_accounts[0]["safeName"] == "SQL-PROD"


async def test_backup_restore_confirm_mismatch(env):
    bid = await _make_backup(env)
    r = env["client"].post(
        f"/tenants/lab/account-backups/{bid}/restore",
        json={"dry_run": False, "reason": "restaurar cuenta borrada por error", "confirm": "otra"},
    )
    assert r.status_code == 422
    assert env["fake"].created_accounts == []


def test_backup_restore_rbac_operator_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["operator_op"]
    r = env["client"].post("/tenants/lab/account-backups/1/restore", json={})
    assert r.status_code == 403


def test_backup_reveal_404_inexistente(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["tenant_admin"]
    r = env["client"].post(
        "/tenants/lab/account-backups/999/reveal", json={"reason": "verificación tras restore"}
    )
    assert r.status_code == 404
