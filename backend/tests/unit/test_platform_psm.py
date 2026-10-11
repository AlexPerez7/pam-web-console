"""platform.psm.update — reasignar el PSM de una plataforma con el mismo
patrón de seguridad que Platform_PSM_Migration.ps1: backup +
verificación + rollback automático ante el bug conocido de CyberArk
(PUT reemplaza el objeto PrivilegedSessionManagement completo). Ver
docs/specs/platform-psm-migration.md."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.writes import router as writes_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.cyberark import CyberArkError
from app.db import get_session
from app.domain.platforms import find_target_platform, normalize_psm, plan_platform_psm
from app.models import AuditLog, Deployment, Operator, OperatorRole, RoleAssignment, Tenant

TARGETS = [
    {"ID": 101, "PlatformID": "WinDomain", "PrivilegedSessionManagement": {"PSMServerId": "PSM1"}},
]

PSM_OBJ = {
    "PSMServerId": "PSM1",
    "PSMServerName": "PSM1",
    "PSMConnectors": [{"PSMConnectorID": "RDP", "Enabled": True}],
}


# --- dominio, puro ---------------------------------------------------------- #


def test_find_target_platform():
    assert find_target_platform(TARGETS, "WinDomain")["ID"] == 101
    assert find_target_platform(TARGETS, "NoExiste") is None


def test_normalize_psm_anula_solo_psm_fields():
    n = normalize_psm(PSM_OBJ)
    assert n["PSMServerId"] is None
    assert n["PSMServerName"] is None
    assert n["PSMConnectors"] == PSM_OBJ["PSMConnectors"]


def test_plan_platform_psm_no_op_si_mismo_psm():
    plan = plan_platform_psm("WinDomain")(PSM_OBJ, {"psm_server_id": "PSM1"})
    assert plan.diff == []
    assert plan.effect is None


def test_plan_platform_psm_diff_y_warning():
    plan = plan_platform_psm("WinDomain")(PSM_OBJ, {"psm_server_id": "PSM2"})
    assert plan.diff == [{"field": "PSM", "from": "PSM1", "to": "PSM2"}]
    assert plan.confirm_phrase == "WinDomain"
    assert any("bug conocido" in w for w in plan.warnings)


# --- endpoint ----------------------------------------------------------------- #


class FakeClient:
    def __init__(self) -> None:
        self.psm = dict(PSM_OBJ)
        self.puts: list[dict] = []
        self.corrupt_on_put = False  # simula el bug: PUT borra PSMConnectors
        self.put_fails_on_rollback = False

    async def list_target_platforms(self) -> list:
        return TARGETS

    async def get_platform_psm(self, numeric_id) -> dict:
        return dict(self.psm)

    async def update_platform_psm(self, numeric_id, body: dict) -> dict:
        is_rollback = body.get("PSMServerId") == PSM_OBJ["PSMServerId"]
        if is_rollback and self.put_fails_on_rollback:
            raise CyberArkError("boom en el rollback")
        self.puts.append(dict(body))
        if self.corrupt_on_put and not is_rollback:
            # el bug conocido: PSMConnectors desaparece (sólo en el PUT que migra,
            # no en el rollback -- eso es lo que este test verifica que restaura)
            self.psm = {"PSMServerId": body["PSMServerId"], "PSMServerName": body["PSMServerName"]}
        else:
            self.psm = dict(body)
        return dict(self.psm)


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


REASON = "migración de PSM, ticket OPS-1"


def test_dry_run_trae_confirm_phrase(env):
    r = env["client"].request(
        "PUT",
        "/tenants/lab/platforms/WinDomain/psm",
        json={"changes": {"psm_server_id": "PSM2"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert body["confirm_phrase"] == "WinDomain"
    assert env["fake"].puts == []


def test_confirm_mismatch_422(env):
    r = env["client"].request(
        "PUT",
        "/tenants/lab/platforms/WinDomain/psm",
        json={
            "changes": {"psm_server_id": "PSM2"},
            "dry_run": False,
            "reason": REASON,
            "confirm": "WinServerLocal",
        },
    )
    assert r.status_code == 422
    assert env["fake"].puts == []


def test_ejecucion_ok_migra_y_verifica(env):
    r = env["client"].request(
        "PUT",
        "/tenants/lab/platforms/WinDomain/psm",
        json={
            "changes": {"psm_server_id": "PSM2"},
            "dry_run": False,
            "reason": REASON,
            "confirm": "WinDomain",
        },
    )
    assert r.status_code == 200
    assert len(env["fake"].puts) == 1
    assert env["fake"].puts[0]["PSMServerId"] == "PSM2"
    assert env["fake"].puts[0]["PSMConnectors"] == PSM_OBJ["PSMConnectors"]  # preservado


def test_bug_conocido_revierte_automaticamente(env):
    env["fake"].corrupt_on_put = True
    r = env["client"].request(
        "PUT",
        "/tenants/lab/platforms/WinDomain/psm",
        json={
            "changes": {"psm_server_id": "PSM2"},
            "dry_run": False,
            "reason": REASON,
            "confirm": "WinDomain",
        },
    )
    assert r.status_code == 502
    assert "revirtió automáticamente" in r.json()["detail"]
    # 2 PUTs: el que corrompe + el rollback; el estado final quedó como el original
    assert len(env["fake"].puts) == 2
    assert env["fake"].psm["PSMConnectors"] == PSM_OBJ["PSMConnectors"]


async def test_bug_conocido_y_rollback_falla_es_critico(env, session):
    env["fake"].corrupt_on_put = True
    env["fake"].put_fails_on_rollback = True
    r = env["client"].request(
        "PUT",
        "/tenants/lab/platforms/WinDomain/psm",
        json={
            "changes": {"psm_server_id": "PSM2"},
            "dry_run": False,
            "reason": REASON,
            "confirm": "WinDomain",
        },
    )
    assert r.status_code == 502
    assert "rollback automático FALLÓ" in r.json()["detail"]

    from sqlalchemy import select

    rows = (
        (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "platform.psm.rollback_failed")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].detail["original_psm_config"]["PSMConnectors"] == PSM_OBJ["PSMConnectors"]


def test_plataforma_inexistente_404(env):
    r = env["client"].request(
        "PUT",
        "/tenants/lab/platforms/NoExiste/psm",
        json={"changes": {"psm_server_id": "PSM2"}},
    )
    assert r.status_code == 404


def test_rbac_operator_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["operator_op"]
    r = env["client"].request(
        "PUT",
        "/tenants/lab/platforms/WinDomain/psm",
        json={"changes": {"psm_server_id": "PSM2"}},
    )
    assert r.status_code == 403
