"""Fase 3: account.delete en lote (bulk-delete) — ver
docs/specs/account-bulk-delete.md. RBAC platform_admin, confirmación
"ELIMINAR <N>", backup antes de borrar por cuenta, una cuenta que falla no
aborta el lote y si el backup falla esa cuenta NO se borra."""

from __future__ import annotations

import base64
import types
from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.api.writes as writes_mod
from app.api.writes import router as writes_router
from app.core import backups as backups_mod
from app.core import security, writeop
from app.core.backups import BackupCryptoError
from app.core.deps import current_operator
from app.cyberark import CyberArkError, NotFound
from app.db import get_session
from app.models import AccountBackup, AuditLog, Deployment, Operator, OperatorRole, Tenant

ACCOUNTS = {
    "42_1": {
        "id": "42_1",
        "name": "db-prod-01",
        "userName": "svc-db",
        "address": "10.1.2.30",
        "safeName": "SQL-PROD",
        "platformId": "WinServerLocal",
    },
    "43_1": {
        "id": "43_1",
        "name": "app-prod-02",
        "userName": "svc-app",
        "address": "10.1.2.31",
        "safeName": "SQL-PROD",
        "platformId": "WinServerLocal",
    },
    "44_1": {
        "id": "44_1",
        "name": "web-prod-03",
        "userName": "svc-web",
        "address": "10.1.2.32",
        "safeName": "WEB-PROD",
        "platformId": "WinServerLocal",
    },
}


def _fake_age_run(cmd, input=None, capture_output=True, check=True, timeout=None):
    """Sustituye al binario `age` por un base64 reversible (mismo fake que
    test_account_move.py)."""
    if cmd[1] == "-r":
        out = base64.b64encode(input)
    elif cmd[1] == "--decrypt":
        out = base64.b64decode(input)
    else:
        raise AssertionError(f"comando age inesperado: {cmd}")
    return types.SimpleNamespace(stdout=out, stderr=b"")


class FakeClient:
    def __init__(self) -> None:
        self.accounts = {k: dict(v) for k, v in ACCOUNTS.items()}
        self.deleted_accounts: list[str] = []
        self.retrieved: list[str] = []
        self.delete_fails_for: set[str] = set()

    async def get_account(self, account_id: str) -> dict:
        if account_id not in self.accounts:
            raise NotFound(f"cuenta '{account_id}' no existe")
        return self.accounts[account_id]

    async def retrieve_password(self, account_id: str, reason: str) -> str:
        self.retrieved.append(account_id)
        return "s3cr3t!"

    async def delete_account(self, account_id: str) -> None:
        if account_id in self.delete_fails_for:
            raise CyberArkError(f"no se pudo borrar {account_id}")
        self.deleted_accounts.append(account_id)


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(backups_mod.subprocess, "run", _fake_age_run)

    @asynccontextmanager
    async def fake_tc(tenant_id: str, sess):
        yield (None, fake)

    monkeypatch.setattr(writeop, "tenant_client", fake_tc)
    monkeypatch.setattr(writes_mod, "tenant_client", fake_tc)

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
    platform_admin = Operator(
        username="pa", password_hash=security.hash_password("x"), role=OperatorRole.platform_admin
    )
    approver = Operator(
        username="ap", password_hash=security.hash_password("x"), role=OperatorRole.approver
    )
    session.add_all([platform_admin, approver])
    await session.flush()

    app = FastAPI()
    app.include_router(writes_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: platform_admin
    return {
        "app": app,
        "client": TestClient(app),
        "fake": fake,
        "approver": approver,
        "monkeypatch": monkeypatch,
        "session": session,
    }


def _preview_body(**overrides):
    body = {"account_ids": ["42_1", "43_1"]}
    body.update(overrides)
    return body


def _delete_body(**overrides):
    body = {
        "account_ids": ["42_1", "43_1"],
        "reason": "baja pedida por el cliente, ticket OPS-31",
        "confirm": "ELIMINAR 2",
    }
    body.update(overrides)
    return body


# --- preview ------------------------------------------------------------------ #


def test_bulk_delete_preview_ok(env):
    r = env["client"].post("/tenants/lab/accounts/bulk-delete/preview", json=_preview_body())
    assert r.status_code == 200
    body = r.json()
    assert (body["total"], body["valid"], body["invalid"]) == (2, 2, 0)
    rows = {row["account_id"]: row for row in body["rows"]}
    assert rows["42_1"]["name"] == "db-prod-01"
    assert rows["42_1"]["safe_name"] == "SQL-PROD"
    # sólo lectura: no se recuperó ninguna contraseña ni se borró nada
    assert env["fake"].retrieved == []
    assert env["fake"].deleted_accounts == []


def test_bulk_delete_preview_cuenta_inexistente(env):
    r = env["client"].post(
        "/tenants/lab/accounts/bulk-delete/preview",
        json=_preview_body(account_ids=["42_1", "no-existe", "43_1"]),
    )
    assert r.status_code == 200
    body = r.json()
    assert (body["total"], body["valid"], body["invalid"]) == (3, 2, 1)
    rows = {row["account_id"]: row for row in body["rows"]}
    assert rows["no-existe"]["ok"] is False
    assert "no existe" in rows["no-existe"]["error"]


def test_bulk_delete_preview_rbac_approver_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["approver"]
    r = env["client"].post("/tenants/lab/accounts/bulk-delete/preview", json=_preview_body())
    assert r.status_code == 403


# --- ejecución ---------------------------------------------------------------- #


def test_bulk_delete_ejecucion_ok(env):
    r = env["client"].post("/tenants/lab/accounts/bulk-delete", json=_delete_body())
    assert r.status_code == 200
    body = r.json()
    assert (body["total"], body["deleted"], body["failed"]) == (2, 2, [])
    assert set(env["fake"].deleted_accounts) == {"42_1", "43_1"}


async def test_bulk_delete_guarda_un_backup_por_cuenta(env):
    env["client"].post("/tenants/lab/accounts/bulk-delete", json=_delete_body())
    rows = (await env["session"].execute(select(AccountBackup))).scalars().all()
    assert {r.account_id for r in rows} == {"42_1", "43_1"}
    assert all(r.deleted_by == "pa" for r in rows)


def test_bulk_delete_confirm_mismatch_422(env):
    # N = cantidad de cuentas del lote: con 2 cuentas, "ELIMINAR 3" no sirve
    r = env["client"].post(
        "/tenants/lab/accounts/bulk-delete", json=_delete_body(confirm="ELIMINAR 3")
    )
    assert r.status_code == 422
    assert "ELIMINAR 2" in r.json()["detail"]
    assert env["fake"].retrieved == []
    assert env["fake"].deleted_accounts == []


def test_bulk_delete_sin_confirm_422(env):
    r = env["client"].post("/tenants/lab/accounts/bulk-delete", json=_delete_body(confirm=None))
    assert r.status_code == 422
    assert env["fake"].deleted_accounts == []


def test_bulk_delete_razon_corta_422(env):
    r = env["client"].post("/tenants/lab/accounts/bulk-delete", json=_delete_body(reason="x"))
    assert r.status_code == 422
    assert env["fake"].deleted_accounts == []


def test_bulk_delete_dedup_cuenta_la_frase_sobre_ids_unicos(env):
    r = env["client"].post(
        "/tenants/lab/accounts/bulk-delete",
        json=_delete_body(account_ids=["42_1", "42_1", "43_1"]),
    )
    assert r.status_code == 200
    assert r.json()["total"] == 2
    assert env["fake"].deleted_accounts.count("42_1") == 1


def test_bulk_delete_continua_si_una_cuenta_falla(env):
    env["fake"].delete_fails_for.add("43_1")
    r = env["client"].post("/tenants/lab/accounts/bulk-delete", json=_delete_body())
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] == 1
    assert [f["account_id"] for f in body["failed"]] == ["43_1"]
    assert env["fake"].deleted_accounts == ["42_1"]


async def test_bulk_delete_si_falla_el_backup_esa_cuenta_no_se_borra(env):
    real_save = writes_mod.save_account_backup

    async def flaky_save(session, *, account, **kw):
        if account["id"] == "43_1":
            raise BackupCryptoError("age no disponible")
        return await real_save(session, account=account, **kw)

    env["monkeypatch"].setattr(writes_mod, "save_account_backup", flaky_save)
    r = env["client"].post("/tenants/lab/accounts/bulk-delete", json=_delete_body())
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] == 1
    assert body["failed"][0]["account_id"] == "43_1"
    assert "falló el backup" in body["failed"][0]["error"]
    # nunca se borra sin backup
    assert env["fake"].deleted_accounts == ["42_1"]
    backups = (await env["session"].execute(select(AccountBackup))).scalars().all()
    assert {b.account_id for b in backups} == {"42_1"}


def test_bulk_delete_cuenta_inexistente_no_aborta_el_lote(env):
    r = env["client"].post(
        "/tenants/lab/accounts/bulk-delete",
        json=_delete_body(account_ids=["42_1", "no-existe"], confirm="ELIMINAR 2"),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] == 1
    assert body["failed"][0]["account_id"] == "no-existe"


def test_bulk_delete_rbac_approver_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["approver"]
    r = env["client"].post("/tenants/lab/accounts/bulk-delete", json=_delete_body())
    assert r.status_code == 403
    assert env["fake"].deleted_accounts == []


def test_bulk_delete_cap_de_lote(env):
    ids = [f"acc-{i}" for i in range(51)]  # write_object_cap default = 50
    r = env["client"].post(
        "/tenants/lab/accounts/bulk-delete",
        json=_delete_body(account_ids=ids, confirm="ELIMINAR 51"),
    )
    assert r.status_code == 409
    assert env["fake"].retrieved == []


async def test_bulk_delete_audita_un_registro_por_lote(env):
    env["client"].post("/tenants/lab/accounts/bulk-delete", json=_delete_body())
    rows = (
        (
            await env["session"].execute(
                select(AuditLog).where(AuditLog.action == "account.delete.bulk")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].result == "ok"
    assert rows[0].detail["deleted"] == 2
    assert rows[0].detail["total"] == 2


async def test_bulk_delete_confirm_mismatch_audita_denied(env):
    env["client"].post("/tenants/lab/accounts/bulk-delete", json=_delete_body(confirm="no"))
    rows = (
        (
            await env["session"].execute(
                select(AuditLog).where(AuditLog.action == "account.delete.bulk")
            )
        )
        .scalars()
        .all()
    )
    assert [r.result for r in rows] == ["denied"]
