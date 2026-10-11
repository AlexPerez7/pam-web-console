"""platform.psm.update.bulk — reasignar el PSM de VARIAS plataformas al
mismo PSM destino de una (ej. migrar todo lo que apunta a un PSM que va a
recibir un parche). Mismo motor de seguridad que `platform.psm.update`
individual (`_apply_platform_psm_update`, compartido) — ver
docs/specs/done/platform-psm-bulk-migration.md."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.api.writes as writes_mod
from app.api.writes import router as writes_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.cyberark import CyberArkError, NotFound
from app.db import get_session
from app.models import AuditLog, Deployment, Operator, OperatorRole, RoleAssignment, Tenant

TARGETS = [
    {"ID": 101, "PlatformID": "WinDomain"},
    {"ID": 102, "PlatformID": "WinLocal"},
    {"ID": 103, "PlatformID": "Unix"},
]

CONNECTORS = [{"PSMConnectorID": "RDP", "Enabled": True}]


class FakeClient:
    def __init__(self) -> None:
        self.psms: dict[int, dict] = {
            101: {"PSMServerId": "PSM1", "PSMServerName": "PSM1", "PSMConnectors": CONNECTORS},
            102: {"PSMServerId": "PSM1", "PSMServerName": "PSM1", "PSMConnectors": CONNECTORS},
            # ya está en el destino -- debe quedar afuera del lote (ni éxito ni fallo)
            103: {"PSMServerId": "PSM2", "PSMServerName": "PSM2", "PSMConnectors": CONNECTORS},
        }
        self.puts: list[tuple[int, dict]] = []
        self.fail_ids: set[int] = set()
        self.corrupt_ids: set[int] = set()

    async def list_target_platforms(self) -> list:
        return TARGETS

    async def get_platform_psm(self, numeric_id) -> dict:
        if numeric_id not in self.psms:
            raise NotFound("no existe")
        return dict(self.psms[numeric_id])

    async def update_platform_psm(self, numeric_id, body: dict) -> dict:
        is_rollback = body.get("PSMServerId") in {"PSM1"} and numeric_id in self.corrupt_ids
        self.puts.append((numeric_id, dict(body)))
        if numeric_id in self.fail_ids and not is_rollback:
            raise CyberArkError("boom")
        if numeric_id in self.corrupt_ids and not is_rollback:
            self.psms[numeric_id] = {
                "PSMServerId": body["PSMServerId"],
                "PSMServerName": body["PSMServerName"],
            }
        else:
            self.psms[numeric_id] = dict(body)
        return dict(self.psms[numeric_id])


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    fake = FakeClient()

    @asynccontextmanager
    async def fake_tc(tenant_id: str, sess):
        yield (None, fake)

    monkeypatch.setattr(writeop, "tenant_client", fake_tc)
    monkeypatch.setattr(writes_mod, "tenant_client", fake_tc)

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
    session.add_all([approver, operator_op])
    await session.flush()
    session.add_all(
        [
            RoleAssignment(operator_id=approver.id, tenant_id="lab", role=OperatorRole.approver),
            RoleAssignment(operator_id=operator_op.id, tenant_id="lab", role=OperatorRole.operator),
        ]
    )
    await session.flush()

    app = FastAPI()
    app.include_router(writes_router)

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
        "session": session,
    }


REASON = "migración de PSM por parche, ticket OPS-2"


# --- preview ---------------------------------------------------------------- #


def test_preview_arma_diff_por_plataforma_y_excluye_las_que_ya_estan(env):
    r = env["client"].post(
        "/tenants/lab/platforms/bulk-psm-update/preview",
        json={"platform_ids": ["WinDomain", "WinLocal", "Unix"], "psm_server_id": "PSM2"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert body["valid"] == 2
    assert body["invalid"] == 1
    rows = {row["platform_id"]: row for row in body["rows"]}
    assert rows["WinDomain"]["ok"] is True
    assert rows["WinDomain"]["diff"] == [{"field": "PSM", "from": "PSM1", "to": "PSM2"}]
    assert rows["Unix"]["ok"] is False
    assert "ya está en ese PSM" in rows["Unix"]["error"]
    assert env["fake"].puts == []  # preview nunca escribe


def test_preview_plataforma_inexistente(env):
    r = env["client"].post(
        "/tenants/lab/platforms/bulk-psm-update/preview",
        json={"platform_ids": ["NoExiste"], "psm_server_id": "PSM2"},
    )
    assert r.status_code == 200
    row = r.json()["rows"][0]
    assert row["ok"] is False
    assert "no existe" in row["error"]


def test_preview_cap_de_lote(env):
    ids = [f"plat-{i}" for i in range(51)]  # write_object_cap default = 50
    r = env["client"].post(
        "/tenants/lab/platforms/bulk-psm-update/preview",
        json={"platform_ids": ids, "psm_server_id": "PSM2"},
    )
    assert r.status_code == 409


def test_preview_rbac_operator_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["operator_op"]
    r = env["client"].post(
        "/tenants/lab/platforms/bulk-psm-update/preview",
        json={"platform_ids": ["WinDomain"], "psm_server_id": "PSM2"},
    )
    assert r.status_code == 403


# --- ejecución ---------------------------------------------------------------- #


def test_ejecucion_ok_migra_las_que_corresponden(env):
    r = env["client"].post(
        "/tenants/lab/platforms/bulk-psm-update",
        json={
            "platform_ids": ["WinDomain", "WinLocal", "Unix"],
            "psm_server_id": "PSM2",
            "reason": REASON,
            "confirm": "PSM2",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert len(body["updated"]) == 2
    assert {u["platform_id"] for u in body["updated"]} == {"WinDomain", "WinLocal"}
    assert len(body["failed"]) == 1
    assert body["failed"][0]["platform_id"] == "Unix"

    assert env["fake"].psms[101]["PSMServerId"] == "PSM2"
    assert env["fake"].psms[101]["PSMConnectors"] == CONNECTORS  # preservado
    assert env["fake"].psms[102]["PSMServerId"] == "PSM2"


def test_confirm_mismatch_422(env):
    r = env["client"].post(
        "/tenants/lab/platforms/bulk-psm-update",
        json={
            "platform_ids": ["WinDomain"],
            "psm_server_id": "PSM2",
            "reason": REASON,
            "confirm": "PSM-equivocado",
        },
    )
    assert r.status_code == 422
    assert env["fake"].puts == []


def test_reason_obligatoria(env):
    r = env["client"].post(
        "/tenants/lab/platforms/bulk-psm-update",
        json={
            "platform_ids": ["WinDomain"],
            "psm_server_id": "PSM2",
            "reason": "corta",
            "confirm": "PSM2",
        },
    )
    assert r.status_code == 422
    assert env["fake"].puts == []


def test_una_plataforma_fallando_no_aborta_el_lote(env):
    env["fake"].fail_ids = {101}  # WinDomain falla, WinLocal debe seguir
    r = env["client"].post(
        "/tenants/lab/platforms/bulk-psm-update",
        json={
            "platform_ids": ["WinDomain", "WinLocal"],
            "psm_server_id": "PSM2",
            "reason": REASON,
            "confirm": "PSM2",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["failed"][0]["platform_id"] == "WinDomain"
    assert {u["platform_id"] for u in body["updated"]} == {"WinLocal"}
    assert env["fake"].psms[102]["PSMServerId"] == "PSM2"


def test_bug_conocido_revierte_automaticamente_y_queda_como_fallo(env):
    env["fake"].corrupt_ids = {101}
    r = env["client"].post(
        "/tenants/lab/platforms/bulk-psm-update",
        json={
            "platform_ids": ["WinDomain", "WinLocal"],
            "psm_server_id": "PSM2",
            "reason": REASON,
            "confirm": "PSM2",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["failed"][0]["platform_id"] == "WinDomain"
    assert "revirtió automáticamente" in body["failed"][0]["error"]
    assert env["fake"].psms[101]["PSMConnectors"] == CONNECTORS  # quedó como el original
    assert {u["platform_id"] for u in body["updated"]} == {"WinLocal"}  # el resto sigue


async def test_audita_una_vez_por_lote_con_el_detalle(env, session):
    env["fake"].fail_ids = {101}
    env["client"].post(
        "/tenants/lab/platforms/bulk-psm-update",
        json={
            "platform_ids": ["WinDomain", "WinLocal"],
            "psm_server_id": "PSM2",
            "reason": REASON,
            "confirm": "PSM2",
        },
    )
    rows = (
        (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "platform.psm.update.bulk")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].detail["updated"] == 1
    assert rows[0].detail["failed"] == 1
    assert rows[0].detail["psm_server_id"] == "PSM2"


def test_cap_de_lote(env):
    ids = [f"plat-{i}" for i in range(51)]
    r = env["client"].post(
        "/tenants/lab/platforms/bulk-psm-update",
        json={
            "platform_ids": ids,
            "psm_server_id": "PSM2",
            "reason": REASON,
            "confirm": "PSM2",
        },
    )
    assert r.status_code == 409


def test_rbac_operator_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["operator_op"]
    r = env["client"].post(
        "/tenants/lab/platforms/bulk-psm-update",
        json={
            "platform_ids": ["WinDomain"],
            "psm_server_id": "PSM2",
            "reason": REASON,
            "confirm": "PSM2",
        },
    )
    assert r.status_code == 403
