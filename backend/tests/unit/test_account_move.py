"""Fase 3: account.move — mover una cuenta de Safe vía clonación (retrieve +
create en el destino + delete de la original). Workaround porque CyberArk no
expone un "Move" real vía API con Bearer token (ver
docs/specs/done/account-change-platform.md). RBAC platform_admin +
confirmación reforzada + backup antes de borrar + rollback si el delete de
la original falla después de crear la copia."""

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
from app.core.backups import decrypt_secret
from app.core.deps import current_operator
from app.cyberark import CyberArkError
from app.db import get_session
from app.domain.accounts import build_account_move_plan
from app.models import AccountBackup, AuditLog, Deployment, Operator, OperatorRole, Tenant

ACCOUNT = {
    "id": "42_1",
    "name": "db-prod-01",
    "userName": "svc-db",
    "address": "10.1.2.30",
    "safeName": "SQL-PROD",
    "platformId": "WinServerLocal",
    "platformAccountProperties": {"Port": "1433"},
    "secretManagement": {"automaticManagementEnabled": True},
}

ACCOUNT_2 = {
    "id": "43_1",
    "name": "app-prod-02",
    "userName": "svc-app",
    "address": "10.1.2.31",
    "safeName": "SQL-PROD",
    "platformId": "WinServerLocal",
    "platformAccountProperties": {},
    "secretManagement": {"automaticManagementEnabled": True},
}


def _fake_age_run(cmd, input=None, capture_output=True, check=True, timeout=None):
    """Sustituye al binario `age` real (no está instalado en este entorno de
    test) por un base64 reversible -- mismo fake que test_account_safe_delete.py."""
    if cmd[1] == "-r":
        out = base64.b64encode(input)
    elif cmd[1] == "--decrypt":
        out = base64.b64decode(input)
    else:
        raise AssertionError(f"comando age inesperado: {cmd}")
    return types.SimpleNamespace(stdout=out, stderr=b"")


# --- dominio: build_account_move_plan --------------------------------------- #


def test_build_account_move_plan():
    plan = build_account_move_plan(ACCOUNT, {"safe_name": "SQL-DEST"})
    fields = {d["field"]: (d["from"], d["to"]) for d in plan.diff}
    assert fields["safe"] == ("SQL-PROD", "SQL-DEST")
    assert plan.confirm_phrase == "db-prod-01"
    assert plan.effect is not None
    assert "SQL-DEST" in plan.effect
    assert any("no se mueven" in w for w in plan.warnings)


# --- endpoint ----------------------------------------------------------------- #


class FakeClient:
    def __init__(self) -> None:
        self.accounts: dict[str, dict] = {"42_1": dict(ACCOUNT), "43_1": dict(ACCOUNT_2)}
        self.safes: dict[str, dict] = {"SQL-DEST": {"safeName": "SQL-DEST"}}
        self.deleted_accounts: list[str] = []
        self.retrieved: list[tuple[str, str]] = []
        self.created_accounts: list[dict] = []
        # ids asignados a cada create_account, en orden -- predecible desde
        # un fixture fresco (arranca en "99_1"), asi los tests de rollback
        # pueden anticipar el id antes de que la request corra.
        self.created_ids: list[str] = []
        self.password = "s3cr3t!"
        self._next_id = 99
        # ids de cuenta para los que delete_account debe fallar
        self.delete_fails_for: set[str] = set()

    async def get_account(self, account_id: str) -> dict:
        return self.accounts[account_id]

    async def get_safe(self, safe_id: str) -> dict:
        if safe_id not in self.safes:
            from app.cyberark import NotFound

            raise NotFound(f"safe '{safe_id}' no existe")
        return self.safes[safe_id]

    async def retrieve_password(self, account_id: str, reason: str) -> str:
        self.retrieved.append((account_id, reason))
        return self.password

    async def create_account(self, body: dict) -> dict:
        new_id = f"{self._next_id}_1"
        self._next_id += 1
        self.created_accounts.append(body)
        self.created_ids.append(new_id)
        return {"id": new_id, **body}

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
    # account_bulk_move/account_bulk_move_preview no pasan por run_write
    # (best-effort por cuenta, no un fetch/plan/apply unico) -- usan
    # tenant_client importado directo en app.api.writes, otra referencia.
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
        "platform_admin": platform_admin,
        "approver": approver,
        "session": session,
    }


def _move_body(**overrides):
    body = {
        "dry_run": False,
        "reason": "reorganización de safes, ticket OPS-9",
        "confirm": "db-prod-01",
        "changes": {"safe_name": "SQL-DEST"},
    }
    body.update(overrides)
    return body


def test_account_move_dry_run_no_toca_nada(env):
    r = env["client"].post(
        "/tenants/lab/accounts/42_1/move", json={"changes": {"safe_name": "SQL-DEST"}}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert body["confirm_phrase"] == "db-prod-01"
    assert env["fake"].retrieved == []
    assert env["fake"].created_accounts == []
    assert env["fake"].deleted_accounts == []


def test_account_move_ejecucion_ok(env):
    r = env["client"].post("/tenants/lab/accounts/42_1/move", json=_move_body())
    assert r.status_code == 200
    fake = env["fake"]
    assert fake.retrieved == [("42_1", "reorganización de safes, ticket OPS-9")]
    assert len(fake.created_accounts) == 1
    assert fake.created_accounts[0]["safeName"] == "SQL-DEST"
    assert fake.created_accounts[0]["secret"] == "s3cr3t!"
    assert fake.deleted_accounts == ["42_1"]


async def test_account_move_guarda_backup_de_la_original_antes_de_borrar(env):
    env["client"].post("/tenants/lab/accounts/42_1/move", json=_move_body())

    rows = (await env["session"].execute(select(AccountBackup))).scalars().all()
    assert len(rows) == 1
    assert rows[0].account_name == "db-prod-01"
    assert rows[0].safe_name == "SQL-PROD"  # la safe ORIGINAL, no la destino
    assert await decrypt_secret(rows[0].secret_ciphertext) == "s3cr3t!"


def test_account_move_confirm_mismatch_422(env):
    r = env["client"].post(
        "/tenants/lab/accounts/42_1/move", json=_move_body(confirm="nombre-equivocado")
    )
    assert r.status_code == 422
    assert env["fake"].created_accounts == []
    assert env["fake"].deleted_accounts == []


def test_account_move_ya_esta_en_esa_safe_422(env):
    r = env["client"].post(
        "/tenants/lab/accounts/42_1/move",
        json=_move_body(changes={"safe_name": "SQL-PROD"}),
    )
    assert r.status_code == 422
    assert "ya está en esa Safe" in r.json()["detail"]
    assert env["fake"].retrieved == []


def test_account_move_safe_destino_inexistente_404(env):
    r = env["client"].post(
        "/tenants/lab/accounts/42_1/move",
        json=_move_body(changes={"safe_name": "NO-EXISTE"}),
    )
    assert r.status_code == 404
    assert env["fake"].retrieved == []


def test_account_move_rbac_approver_rechazado(env):
    """A diferencia del resto de Fase 3 (approver+), account.move exige
    platform_admin -- pierde el historial nativo de CyberArk y no tiene
    forma de deshacerse salvo el backup."""
    env["app"].dependency_overrides[current_operator] = lambda: env["approver"]
    r = env["client"].post("/tenants/lab/accounts/42_1/move", json=_move_body())
    assert r.status_code == 403
    assert env["fake"].created_accounts == []


def test_account_move_rollback_si_falla_el_borrado_de_la_original(env):
    """create_account funciona, pero delete_account de la ORIGINAL falla ->
    se revierte borrando la copia recién creada, no queda duplicado."""
    env["fake"].delete_fails_for.add("42_1")
    r = env["client"].post("/tenants/lab/accounts/42_1/move", json=_move_body())
    assert r.status_code == 502
    assert "se revirtió" in r.json()["detail"]
    fake = env["fake"]
    assert len(fake.created_accounts) == 1
    # la cuenta original NO quedó borrada, y la copia sí se revirtió (delete
    # del id nuevo llamado, sin fallar -> no queda en deleted_accounts
    # porque delete_fails_for sólo bloquea el id viejo)
    assert "42_1" not in fake.deleted_accounts
    assert fake.created_ids[0] in fake.deleted_accounts


async def test_account_move_rollback_tambien_falla_audita_y_corta(env):
    """Peor caso: ni el delete de la original ni el rollback (delete de la
    copia nueva) funcionan -- quedan dos copias vivas, se audita el estado
    completo con action=account.move.rollback_failed y se corta con un
    error explícito."""
    fake = env["fake"]
    fake.delete_fails_for.add("42_1")
    # id predecible: fixture fresco, primer create_account -> "99_1"
    fake.delete_fails_for.add("99_1")

    r = env["client"].post("/tenants/lab/accounts/42_1/move", json=_move_body())
    assert r.status_code == 502
    assert "rollback" in r.json()["detail"].lower()
    assert "dos copias vivas" in r.json()["detail"]

    rows = (
        (
            await env["session"].execute(
                select(AuditLog).where(AuditLog.action == "account.move.rollback_failed")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].detail["new_account_id"] == fake.created_ids[0]


# --- account.move en lote (bulk-move) ---------------------------------------- #


def _bulk_preview_body(**overrides):
    body = {"account_ids": ["42_1", "43_1"], "safe_name": "SQL-DEST"}
    body.update(overrides)
    return body


def _bulk_move_body(**overrides):
    body = {
        "account_ids": ["42_1", "43_1"],
        "safe_name": "SQL-DEST",
        "reason": "reorganización de safes, ticket OPS-9",
        "confirm": "SQL-DEST",
    }
    body.update(overrides)
    return body


def test_account_bulk_move_preview_ok(env):
    r = env["client"].post("/tenants/lab/accounts/bulk-move/preview", json=_bulk_preview_body())
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert body["valid"] == 2
    rows = {row["account_id"]: row for row in body["rows"]}
    assert rows["42_1"]["ok"] is True
    assert any(d["field"] == "safe" and d["to"] == "SQL-DEST" for d in rows["42_1"]["diff"])
    assert rows["43_1"]["ok"] is True
    fake = env["fake"]
    assert fake.retrieved == []
    assert fake.created_accounts == []


def test_account_bulk_move_preview_una_ya_en_esa_safe(env):
    env["fake"].accounts["43_1"]["safeName"] = "SQL-DEST"
    r = env["client"].post("/tenants/lab/accounts/bulk-move/preview", json=_bulk_preview_body())
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] == 1
    assert body["invalid"] == 1
    rows = {row["account_id"]: row for row in body["rows"]}
    assert rows["43_1"]["ok"] is False
    assert "ya está en esa Safe" in rows["43_1"]["error"]


def test_account_bulk_move_ejecucion_ok(env):
    r = env["client"].post("/tenants/lab/accounts/bulk-move", json=_bulk_move_body())
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert body["moved"] == 2
    assert body["failed"] == []

    fake = env["fake"]
    assert set(fake.deleted_accounts) == {"42_1", "43_1"}
    assert len(fake.created_accounts) == 2
    assert all(c["safeName"] == "SQL-DEST" for c in fake.created_accounts)
    assert {a for a, _ in fake.retrieved} == {"42_1", "43_1"}


async def test_account_bulk_move_guarda_un_backup_por_cuenta(env):
    env["client"].post("/tenants/lab/accounts/bulk-move", json=_bulk_move_body())
    rows = (await env["session"].execute(select(AccountBackup))).scalars().all()
    assert len(rows) == 2
    assert {r.account_id for r in rows} == {"42_1", "43_1"}


def test_account_bulk_move_confirm_mismatch_422(env):
    r = env["client"].post(
        "/tenants/lab/accounts/bulk-move", json=_bulk_move_body(confirm="otra-safe")
    )
    assert r.status_code == 422
    assert env["fake"].created_accounts == []


def test_account_bulk_move_continua_si_una_cuenta_falla(env):
    """El borrado de la original de 43_1 falla, el rollback (borrar la
    copia nueva) funciona -- esa cuenta queda como fallida, pero 42_1 se
    mueve igual: el lote no aborta por una cuenta."""
    env["fake"].delete_fails_for.add("43_1")
    r = env["client"].post("/tenants/lab/accounts/bulk-move", json=_bulk_move_body())
    assert r.status_code == 200
    body = r.json()
    assert body["moved"] == 1
    assert len(body["failed"]) == 1
    assert body["failed"][0]["account_id"] == "43_1"
    assert "se revirtió" in body["failed"][0]["error"]

    fake = env["fake"]
    assert "42_1" in fake.deleted_accounts  # la que sí se movió
    assert "43_1" not in fake.deleted_accounts  # la que falló, sigue en el origen


def test_account_bulk_move_rbac_approver_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["approver"]
    r = env["client"].post("/tenants/lab/accounts/bulk-move", json=_bulk_move_body())
    assert r.status_code == 403
    assert env["fake"].created_accounts == []


def test_account_bulk_move_cap_de_lote(env):
    ids = [f"acc-{i}" for i in range(51)]  # write_object_cap default = 50
    r = env["client"].post("/tenants/lab/accounts/bulk-move", json=_bulk_move_body(account_ids=ids))
    assert r.status_code == 409
    assert env["fake"].retrieved == []


def test_account_bulk_move_safe_destino_inexistente_404(env):
    r = env["client"].post(
        "/tenants/lab/accounts/bulk-move",
        json=_bulk_move_body(safe_name="NO-EXISTE", confirm="NO-EXISTE"),
    )
    assert r.status_code == 404
    assert env["fake"].retrieved == []
