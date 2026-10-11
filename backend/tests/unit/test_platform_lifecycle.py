"""Fase 3: activar/desactivar/duplicar/borrar una plataforma.

Ver docs/specs/platform-lifecycle.md. Endpoints verificados contra el
módulo open-source `psPAS` (docs.cyberark.com bloqueó el fetch automatizado
para estas páginas puntuales).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.backups import platform_router as platform_backups_router
from app.api.writes import router as writes_router
from app.core import security, writeop
from app.core.deps import current_operator
from app.db import get_session
from app.domain.platforms import (
    build_platform_delete_plan,
    build_platform_duplicate_plan,
    plan_platform_activation,
)
from app.models import Deployment, Operator, OperatorRole, PlatformBackup, RoleAssignment, Tenant

TARGET_ACTIVE = {"ID": 202, "PlatformID": "WinDomain", "Name": "Windows Domain", "Active": True}
TARGET_INACTIVE = {
    "ID": 303,
    "PlatformID": "Retired",
    "Name": "Plataforma vieja",
    "Active": False,
}

ACCOUNTS = [
    {"id": "1", "platformId": "WinDomain"},
    {"id": "2", "platformId": "Retired"},
]


# --- dominio, puro ---------------------------------------------------------- #


def test_plan_activation_no_op_si_ya_activa():
    plan = plan_platform_activation("WinDomain", True)(TARGET_ACTIVE, {})
    assert plan.diff == []
    assert plan.effect is None
    assert plan.confirm_phrase == "WinDomain"


def test_plan_activation_diff_al_desactivar():
    plan = plan_platform_activation("WinDomain", False)(TARGET_ACTIVE, {})
    assert plan.diff == [{"field": "active", "from": True, "to": False}]
    assert plan.confirm_phrase == "WinDomain"
    assert any("deja de rotar" in w for w in plan.warnings)


def test_plan_activation_diff_al_activar():
    plan = plan_platform_activation("Retired", True)(TARGET_INACTIVE, {})
    assert plan.diff == [{"field": "active", "from": False, "to": True}]
    assert any("retoma la gestión" in w for w in plan.warnings)


def test_build_duplicate_plan_effect():
    plan = build_platform_duplicate_plan(TARGET_ACTIVE, {"name": "WinDomain-Copia"})
    assert plan.confirm_phrase is None
    assert "WinDomain-Copia" in plan.effect
    assert "WinDomain" in plan.effect


def test_build_delete_plan_diff_curado():
    plan = build_platform_delete_plan({"platform": TARGET_ACTIVE}, {})
    fields = {d["field"] for d in plan.diff}
    assert fields == {"PlatformID", "Nombre", "Activa"}
    assert plan.confirm_phrase == "WinDomain"


# --- endpoint ----------------------------------------------------------------- #


class FakeClient:
    def __init__(self) -> None:
        self.targets = [dict(TARGET_ACTIVE), dict(TARGET_INACTIVE)]
        self.accounts = list(ACCOUNTS)
        self.activated: list[int] = []
        self.deactivated: list[int] = []
        self.duplicated: list[tuple[int, dict]] = []
        self.deleted: list[int] = []
        self.exported: list[str] = []
        self.export_bytes = b"PK\x03\x04fake-zip"

    async def list_target_platforms(self) -> list:
        return self.targets

    async def activate_target_platform(self, numeric_id) -> None:
        self.activated.append(numeric_id)

    async def deactivate_target_platform(self, numeric_id) -> None:
        self.deactivated.append(numeric_id)

    async def duplicate_target_platform(self, numeric_id, body: dict) -> dict:
        self.duplicated.append((numeric_id, dict(body)))
        return {"PlatformID": body["name"]}

    async def delete_target_platform(self, numeric_id) -> None:
        self.deleted.append(numeric_id)

    async def export_platform(self, platform_id: str) -> bytes:
        self.exported.append(platform_id)
        return self.export_bytes

    async def list_accounts(self, search=None) -> list:
        return self.accounts


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

    async def cache_delete(key: str) -> None:
        return None

    monkeypatch.setattr(writeop.cache, "delete", cache_delete)

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
    app.include_router(platform_backups_router)

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


REASON = "mantenimiento programado, ticket OPS-2"


# --- activar / desactivar ----------------------------------------------------- #


def test_deactivate_dry_run_no_toca_nada(env):
    r = env["client"].post("/tenants/lab/platforms/WinDomain/deactivate", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["dry_run"] is True
    assert body["confirm_phrase"] == "WinDomain"
    assert env["fake"].deactivated == []


def test_deactivate_ejecucion_ok(env):
    r = env["client"].post(
        "/tenants/lab/platforms/WinDomain/deactivate",
        json={"dry_run": False, "reason": REASON, "confirm": "WinDomain"},
    )
    assert r.status_code == 200
    assert env["fake"].deactivated == [202]


def test_activate_ejecucion_ok(env):
    r = env["client"].post(
        "/tenants/lab/platforms/Retired/activate",
        json={"dry_run": False, "reason": REASON, "confirm": "Retired"},
    )
    assert r.status_code == 200
    assert env["fake"].activated == [303]


def test_activation_confirm_mismatch_422(env):
    r = env["client"].post(
        "/tenants/lab/platforms/WinDomain/deactivate",
        json={"dry_run": False, "reason": REASON, "confirm": "otra-cosa"},
    )
    assert r.status_code == 422
    assert env["fake"].deactivated == []


def test_activation_plataforma_inexistente_404(env):
    r = env["client"].post("/tenants/lab/platforms/NoExiste/activate", json={})
    assert r.status_code == 404


def test_activation_rbac_operator_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["operator_op"]
    r = env["client"].post("/tenants/lab/platforms/WinDomain/deactivate", json={})
    assert r.status_code == 403


# --- duplicar ------------------------------------------------------------------ #


def test_duplicate_ejecucion_ok(env):
    r = env["client"].post(
        "/tenants/lab/platforms/WinDomain/duplicate",
        json={
            "changes": {"name": "WinDomain-Copia", "description": "copia de prueba"},
            "dry_run": False,
            "reason": REASON,
        },
    )
    assert r.status_code == 200
    assert env["fake"].duplicated == [
        (202, {"name": "WinDomain-Copia", "description": "copia de prueba"})
    ]


def test_duplicate_operator_permitido(env):
    """Riesgo bajo: RBAC operator+, sin confirmación reforzada."""
    env["app"].dependency_overrides[current_operator] = lambda: env["operator_op"]
    r = env["client"].post(
        "/tenants/lab/platforms/WinDomain/duplicate",
        json={"changes": {"name": "WinDomain-Copia"}, "dry_run": False, "reason": REASON},
    )
    assert r.status_code == 200
    assert env["fake"].duplicated


def test_duplicate_mismo_nombre_que_origen_422(env):
    r = env["client"].post(
        "/tenants/lab/platforms/WinDomain/duplicate",
        json={"changes": {"name": "WinDomain"}, "dry_run": False, "reason": REASON},
    )
    assert r.status_code == 422
    assert env["fake"].duplicated == []


def test_duplicate_dry_run_no_toca_nada(env):
    r = env["client"].post(
        "/tenants/lab/platforms/WinDomain/duplicate",
        json={"changes": {"name": "WinDomain-Copia"}},
    )
    assert r.status_code == 200
    assert r.json()["dry_run"] is True
    assert env["fake"].duplicated == []


# --- borrar --------------------------------------------------------------------- #


def test_delete_bloqueado_si_tiene_cuentas(env):
    r = env["client"].request(
        "DELETE",
        "/tenants/lab/platforms/WinDomain",
        json={"dry_run": False, "reason": REASON, "confirm": "WinDomain"},
    )
    assert r.status_code == 422
    assert "1 cuenta" in r.json()["detail"]
    assert env["fake"].deleted == []
    assert env["fake"].exported == []


def test_delete_sin_cuentas_respalda_antes_de_borrar(env):
    env["fake"].accounts = [{"id": "1", "platformId": "WinDomain"}]  # ninguna en "Retired"
    r = env["client"].request(
        "DELETE",
        "/tenants/lab/platforms/Retired",
        json={"dry_run": False, "reason": REASON, "confirm": "Retired"},
    )
    assert r.status_code == 200
    assert env["fake"].exported == ["Retired"]
    assert env["fake"].deleted == [303]


async def test_delete_deja_backup_recuperable(env):
    from sqlalchemy import select

    env["fake"].accounts = []
    env["client"].request(
        "DELETE",
        "/tenants/lab/platforms/Retired",
        json={"dry_run": False, "reason": REASON, "confirm": "Retired"},
    )
    rows = (await env["session"].execute(select(PlatformBackup))).scalars().all()
    assert len(rows) == 1
    assert rows[0].platform_id == "Retired"
    assert rows[0].numeric_id == "303"
    assert rows[0].package == env["fake"].export_bytes
    assert rows[0].size_bytes == len(env["fake"].export_bytes)


def test_delete_confirm_mismatch_422(env):
    env["fake"].accounts = []
    r = env["client"].request(
        "DELETE",
        "/tenants/lab/platforms/Retired",
        json={"dry_run": False, "reason": REASON, "confirm": "otra-cosa"},
    )
    assert r.status_code == 422
    assert env["fake"].deleted == []
    assert env["fake"].exported == []


def test_delete_rbac_operator_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["operator_op"]
    r = env["client"].request("DELETE", "/tenants/lab/platforms/Retired", json={})
    assert r.status_code == 403


# --- backups de plataformas ----------------------------------------------------- #


async def test_platform_backups_list_y_download(env):
    env["fake"].accounts = []
    env["client"].request(
        "DELETE",
        "/tenants/lab/platforms/Retired",
        json={"dry_run": False, "reason": REASON, "confirm": "Retired"},
    )

    env["app"].dependency_overrides[current_operator] = lambda: env["tenant_admin"]
    r = env["client"].get("/tenants/lab/platform-backups")
    assert r.status_code == 200
    rows = r.json()["backups"]
    assert len(rows) == 1
    assert rows[0]["platform_id"] == "Retired"

    dl = env["client"].get(f"/tenants/lab/platform-backups/{rows[0]['id']}/download")
    assert dl.status_code == 200
    assert dl.content == env["fake"].export_bytes
    assert "Retired.zip" in dl.headers["content-disposition"]


def test_platform_backups_list_rbac_operator_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["operator_op"]
    r = env["client"].get("/tenants/lab/platform-backups")
    assert r.status_code == 403
