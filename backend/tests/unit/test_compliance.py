"""Sección Compliance: drill-down navegable por cuenta y motivo (distinto
del Dashboard, que sólo agrega). Ver app/api/compliance.py."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import compliance as compliance_mod
from app.api.compliance import router as compliance_router
from app.core import security
from app.core.deps import current_operator
from app.cyberark import Forbidden
from app.db import get_session
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant


def _acc(
    acc_id: str, *, auto: bool = True, age_days: int | None = None, platform_id: str = "WinDomain"
) -> dict:
    sm: dict = {"automaticManagementEnabled": auto}
    if age_days is not None:
        sm["lastModifiedTime"] = int((datetime.now(UTC) - timedelta(days=age_days)).timestamp())
    return {
        "id": acc_id,
        "name": f"acc-{acc_id}",
        "userName": "svc",
        "address": "10.0.0.1",
        "safeName": "SAFE",
        "platformId": platform_id,
        "secretManagement": sm,
    }


def _platform(platform_id: str, *, periodic: bool = False, days: int | None = None) -> dict:
    return {
        "general": {"id": platform_id, "name": platform_id},
        "credentialsManagement": {
            "performPeriodicChange": periodic,
            "requirePasswordChangeEveryXDays": days,
        },
    }


# CustomPlat exige rotar cada 10 días -- mucho más estricto que el default
# del tenant (30) usado en el resto de las pruebas.
_PLATFORMS = [_platform("WinDomain"), _platform("CustomPlat", periodic=True, days=10)]


class FakeClient:
    def __init__(self, *, accounts=None, filter_results=None, platforms=None) -> None:
        self.accounts = accounts or []
        self.versions: dict[str, list] = {}
        self.versions_error = False
        self.filter_results = filter_results or {}
        self.platforms = platforms if platforms is not None else _PLATFORMS

    async def list_accounts(self, **kwargs) -> list:
        return self.accounts

    async def list_platforms(self) -> list:
        return self.platforms

    async def get_account(self, account_id: str) -> dict:
        return next(a for a in self.accounts if a["id"] == account_id)

    async def saved_filter_accounts(self, saved_filter: str) -> list:
        return self.filter_results.get(saved_filter, [])

    async def list_password_versions(self, account_id: str) -> list:
        if self.versions_error:
            raise Forbidden("sin permiso")
        return self.versions.get(account_id, [])


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    fake = FakeClient(
        accounts=[
            _acc("1", auto=False, age_days=400),  # deshabilitada y vieja -> DisabledStale
            _acc("2", auto=False, age_days=5),  # deshabilitada pero reciente -> no cuenta
            _acc("3", auto=True, age_days=400),  # vieja pero con gestión activa -> ActiveOverdue
            _acc("6", auto=False, age_days=15, platform_id="CustomPlat"),  # deshabilitada, 15
            # días -- vencida contra el umbral de SU plataforma (10) aunque no
            # llegue a los 30 del tenant
        ],
        filter_results={
            "PolicyFailures": [_acc("4")],
            "DisabledPasswordByUser": [_acc("5")],
        },
    )

    @asynccontextmanager
    async def fake_tc(tenant_id: str, sess):
        tenant = await sess.get(Tenant, tenant_id)
        yield (tenant, fake)

    monkeypatch.setattr(compliance_mod, "tenant_client", fake_tc)

    session.add(
        Tenant(
            id="lab",
            name="Lab",
            client="A",
            deployment=Deployment.privilege_cloud,
            pvwa_url="https://x",
            secret_ref="lab",
            compliance_stale_days=30,
        )
    )
    viewer = Operator(
        username="v", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    outsider = Operator(
        username="o", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    op1 = Operator(
        username="op1", password_hash=security.hash_password("x"), role=OperatorRole.operator
    )
    session.add_all([viewer, outsider, op1])
    await session.flush()
    session.add(RoleAssignment(operator_id=viewer.id, tenant_id="lab", role=OperatorRole.viewer))
    session.add(RoleAssignment(operator_id=op1.id, tenant_id="lab", role=OperatorRole.operator))
    await session.flush()

    app = FastAPI()
    app.include_router(compliance_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: viewer
    return {
        "app": app,
        "client": TestClient(app),
        "fake": fake,
        "viewer": viewer,
        "outsider": outsider,
        "op1": op1,
    }


def test_disabled_stale_usa_el_umbral_del_tenant_por_default(env):
    r = env["client"].get("/tenants/lab/compliance/accounts?reason=DisabledStale")
    assert r.status_code == 200
    body = r.json()
    ids = {a["id"] for a in body["accounts"]}
    assert "1" in ids  # 400 días, plataforma sin política propia -> default 30
    assert "2" not in ids  # sólo 5 días
    by_id = {a["id"]: a for a in body["accounts"]}
    assert by_id["1"]["ageDays"] == 400


def test_disabled_stale_usa_el_umbral_de_su_propia_plataforma(env):
    r = env["client"].get("/tenants/lab/compliance/accounts?reason=DisabledStale")
    body = r.json()
    ids = {a["id"] for a in body["accounts"]}
    # cuenta 6: 15 días, deshabilitada -- no llega a los 30 del tenant, pero
    # SU plataforma (CustomPlat) exige rotar cada 10 -> vencida igual
    assert "6" in ids


def test_active_overdue_gestion_activa_pero_vencida(env):
    r = env["client"].get("/tenants/lab/compliance/accounts?reason=ActiveOverdue")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["accounts"][0]["id"] == "3"
    assert body["accounts"][0]["ageDays"] == 400


def test_policy_failures_usa_el_saved_filter_real(env):
    r = env["client"].get("/tenants/lab/compliance/accounts?reason=PolicyFailures")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["accounts"][0]["id"] == "4"
    assert "ageDays" not in body["accounts"][0]


def test_disable_by_user_usa_el_saved_filter_real(env):
    r = env["client"].get("/tenants/lab/compliance/accounts?reason=DisableByUser")
    assert r.json()["accounts"][0]["id"] == "5"


def test_reason_desconocida_422(env):
    r = env["client"].get("/tenants/lab/compliance/accounts?reason=Compliant")
    assert r.status_code == 422


def test_sin_acceso_al_tenant_403(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["outsider"]
    r = env["client"].get("/tenants/lab/compliance/accounts?reason=PolicyFailures")
    assert r.status_code == 403


def test_status_cuenta_activa_vencida(env):
    r = env["client"].get("/tenants/lab/compliance/accounts/3/status")
    assert r.status_code == 200
    body = r.json()
    assert body["account_id"] == "3"
    assert body["compliant"] is False
    assert body["reasons"] == ["ActiveOverdue"]
    assert body["age_days"] == 400
    assert body["threshold_days"] == 30


def test_status_cuenta_deshabilitada_dentro_del_umbral_de_su_plataforma(env):
    # cuenta 2: deshabilitada, 5 días -- ninguna plataforma la marca vencida
    r = env["client"].get("/tenants/lab/compliance/accounts/2/status")
    body = r.json()
    assert body["compliant"] is True
    assert body["reasons"] == []


def test_status_cuenta_en_policy_failures_nativo(env):
    # cuenta 4 sólo existe en el savedFilter, no en accounts -- la agregamos
    # a la lista de cuentas del fake para que get_account la encuentre
    env["fake"].accounts.append(
        {"id": "4", "secretManagement": {"automaticManagementEnabled": True}}
    )
    r = env["client"].get("/tenants/lab/compliance/accounts/4/status")
    body = r.json()
    assert body["compliant"] is False
    assert "PolicyFailures" in body["reasons"]


def test_status_sin_acceso_al_tenant_403(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["outsider"]
    r = env["client"].get("/tenants/lab/compliance/accounts/1/status")
    assert r.status_code == 403


def _ago(days: int) -> int:
    return int((datetime.now(UTC) - timedelta(days=days)).timestamp())


def test_status_usa_la_ultima_version_no_lastmodifiedtime(env):
    """Caso real de Globex: un intento fallido del CPM mueve
    `lastModifiedTime` a "hoy", pero la contraseña vigente es de hace 21
    días -- la antigüedad sale de la versión más reciente."""
    acc = _acc("9", age_days=0)
    acc["secretManagement"]["status"] = "failure"
    env["fake"].accounts.append(acc)
    env["fake"].versions["9"] = [
        {"VersionID": 1, "ModificationTime": _ago(40)},
        {"VersionID": 2, "ModificationTime": _ago(21)},
    ]
    body = env["client"].get("/tenants/lab/compliance/accounts/9/status").json()
    assert body["age_days"] == 21
    assert body["last_changed_source"] == "versions"
    assert body["cpm_failed"] is True


def test_status_sin_versiones_cae_a_lastmodifiedtime(env):
    env["fake"].versions_error = True
    body = env["client"].get("/tenants/lab/compliance/accounts/3/status").json()
    assert body["age_days"] == 400
    assert body["last_changed_source"] == "account"
    assert body["cpm_failed"] is False


# --- excepciones de compliance (docs/specs/compliance-exemptions.md) ------ #


def test_exemptions_lista_vacia_por_default(env):
    r = env["client"].get("/tenants/lab/compliance/exemptions")
    assert r.status_code == 200
    assert r.json()["exemptions"] == []


def test_exemptions_viewer_no_puede_crear_403(env):
    r = env["client"].post(
        "/tenants/lab/compliance/exemptions", json={"account_id": "1", "reason": "pedido"}
    )
    assert r.status_code == 403


def test_exemptions_create_requiere_motivo_no_vacio(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["op1"]
    r = env["client"].post(
        "/tenants/lab/compliance/exemptions", json={"account_id": "1", "reason": ""}
    )
    assert r.status_code == 422


def test_exemptions_create_list_y_efecto_en_disabled_stale(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["op1"]
    r = env["client"].post(
        "/tenants/lab/compliance/exemptions",
        json={"account_id": "1", "reason": "Pedido del cliente: solo almacenamiento"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["account_id"] == "1"
    assert body["created_by"] == "op1"

    listed = env["client"].get("/tenants/lab/compliance/exemptions").json()["exemptions"]
    assert [e["account_id"] for e in listed] == ["1"]


def test_exemptions_create_duplicada_409(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["op1"]
    env["client"].post(
        "/tenants/lab/compliance/exemptions", json={"account_id": "1", "reason": "motivo"}
    )
    r = env["client"].post(
        "/tenants/lab/compliance/exemptions", json={"account_id": "1", "reason": "otro motivo"}
    )
    assert r.status_code == 409


def test_exemptions_delete_requiere_operator(env):
    r = env["client"].delete("/tenants/lab/compliance/exemptions/1")
    assert r.status_code == 403


def test_exemptions_delete_inexistente_404(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["op1"]
    r = env["client"].delete("/tenants/lab/compliance/exemptions/999")
    assert r.status_code == 404


def test_exemptions_delete_restaura_disabled_stale(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["op1"]
    env["client"].post(
        "/tenants/lab/compliance/exemptions", json={"account_id": "1", "reason": "motivo"}
    )
    r = env["client"].delete("/tenants/lab/compliance/exemptions/1")
    assert r.status_code == 204
    assert env["client"].get("/tenants/lab/compliance/exemptions").json()["exemptions"] == []


def test_exempted_account_no_cuenta_en_disabled_stale(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["op1"]
    env["client"].post(
        "/tenants/lab/compliance/exemptions",
        json={"account_id": "1", "reason": "pedido del cliente"},
    )
    env["app"].dependency_overrides[current_operator] = lambda: env["viewer"]
    r = env["client"].get("/tenants/lab/compliance/accounts?reason=DisabledStale")
    ids = {a["id"] for a in r.json()["accounts"]}
    assert "1" not in ids  # exenta
    assert "6" in ids  # sigue vencida, sin exención


def test_exempted_account_status_compliant(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["op1"]
    env["client"].post(
        "/tenants/lab/compliance/exemptions",
        json={"account_id": "1", "reason": "pedido del cliente"},
    )
    env["app"].dependency_overrides[current_operator] = lambda: env["viewer"]
    body = env["client"].get("/tenants/lab/compliance/accounts/1/status").json()
    assert body["compliant"] is True
    assert body["reasons"] == []
