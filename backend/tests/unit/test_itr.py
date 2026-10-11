"""Reporte ITR del tenant, generado desde la página Reportes (antes sólo
salía de Telemetry-Reporter por CLI). Ver docs/specs/done/itr-report.md
y app/api/itr.py."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.api.itr as itr_mod
from app.api.itr import router as itr_router
from app.core import security
from app.core.deps import current_operator
from app.cyberark import CyberArkError, NotFound
from app.db import get_session
from app.models import AuditLog, Deployment, Operator, OperatorRole, RoleAssignment, Tenant

_NOW = datetime.now(UTC)


def _acc(acc_id: str) -> dict:
    return {
        "id": acc_id,
        "name": f"acc-{acc_id}",
        "userName": f"svc-{acc_id}",
        "address": "10.0.0.1",
        "safeName": "SAFE1",
        "platformId": "WinDomain",
        "createdTime": int((_NOW - timedelta(days=200)).timestamp()),
        "secretManagement": {
            "status": "success",
            "automaticManagementEnabled": True,
            "lastModifiedTime": int((_NOW - timedelta(days=5)).timestamp()),
            "lastVerifiedTime": int((_NOW - timedelta(days=5)).timestamp()),
        },
    }


ACCOUNTS = [_acc("1"), _acc("2")]

PLATFORMS = [
    {
        "general": {
            "id": "WinDomain",
            "name": "WinDomain",
            "active": True,
            "platformType": "Regular",
        },
        "credentialsManagement": {
            "performPeriodicChange": True,
            "requirePasswordChangeEveryXDays": 30,
            "allowManualChange": True,
        },
        "sessionManagement": {},
        "privilegedAccessWorkflows": {},
        "linkedAccounts": [],
    }
]

SAFES = [
    {
        "safeName": "SAFE1",
        "managingCPM": "PasswordManager",
        "olacEnabled": False,
        "numberOfDaysRetention": 90,
    }
]

USERS = [
    {
        "id": 1,
        "username": "svc-1",
        "userType": "EPVUser",
        "source": "CyberArk",
        "enableUser": True,
        "suspended": False,
        "groupsMembership": [],
        "componentUser": False,
    }
]


class FakeClient:
    def __init__(self) -> None:
        self.connector_management_url: str | None = "https://cm.example"
        self.sia_connector_url: str | None = "https://sia.example"
        self.accounts = list(ACCOUNTS)
        self.platforms = list(PLATFORMS)
        self.safes = list(SAFES)
        self.users = list(USERS)
        self.fail_accounts: Exception | None = None
        self.fail_platforms: Exception | None = None
        self.fail_connectors: Exception | None = None
        self.fail_safes: Exception | None = None
        self.fail_filters: dict[str, Exception] = {}
        self.activities_calls: list[str] = []
        self.list_accounts_calls = 0
        self.component_details_data: dict[str, list] = {}

    async def list_accounts(self, **kwargs) -> list:
        self.list_accounts_calls += 1
        if self.fail_accounts:
            raise self.fail_accounts
        return self.accounts

    async def list_platforms(self) -> list:
        if self.fail_platforms:
            raise self.fail_platforms
        return self.platforms

    async def list_safes(self) -> list:
        if self.fail_safes:
            raise self.fail_safes
        return self.safes

    async def list_users(self, search: str | None = None) -> list:
        return self.users

    async def get_license_report(self) -> dict:
        return {"licensesData": []}

    async def list_connectors(self) -> list:
        if self.fail_connectors:
            raise self.fail_connectors
        return []

    async def list_sia_connectors(self) -> list:
        return []

    async def get_all_sia_connector_details(self, connector_ids: list[str]) -> dict:
        return {}

    async def get_identity_users_full(self, page_size: int = 200) -> list:
        return []

    async def saved_filter_accounts(self, saved_filter: str) -> list:
        if saved_filter in self.fail_filters:
            raise self.fail_filters[saved_filter]
        return []

    async def component_details(self, component_id: str) -> list:
        return self.component_details_data.get(component_id, [])

    async def safe_members(self, safe_id: str) -> list:
        return []

    async def account_activities(self, account_id: str) -> list:
        self.activities_calls.append(account_id)
        return [{"Action": "PSM Connect", "Date": 0, "User": "svc"}]


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    fake = FakeClient()

    @asynccontextmanager
    async def fake_tc(tenant_id: str, sess):
        tenant = await sess.get(Tenant, tenant_id)
        yield (tenant, fake)

    monkeypatch.setattr(itr_mod, "tenant_client", fake_tc)

    cache_store: dict[str, dict] = {}

    async def get_json(key: str):
        return cache_store.get(key)

    async def set_json(key: str, value: dict, ttl: int) -> None:
        cache_store[key] = value

    monkeypatch.setattr(itr_mod.cache, "get_json", get_json)
    monkeypatch.setattr(itr_mod.cache, "set_json", set_json)

    session.add(
        Tenant(
            id="lab",
            name="Lab",
            client="Cliente A",
            deployment=Deployment.privilege_cloud,
            pvwa_url="https://x",
            secret_ref="lab",
            compliance_stale_days=30,
        )
    )
    # a propósito NO tiene override en app/itr/clientes/ (ver
    # test_export_itti_usa_el_mismo_layout_generico_que_cualquier_tenant):
    # el reporte de Initech tiene que salir igual de completo que el de
    # cualquier otro tenant.
    session.add(
        Tenant(
            id="initech",
            name="Initech",
            client="Initech",
            deployment=Deployment.privilege_cloud,
            pvwa_url="https://x",
            secret_ref="initech",
            compliance_stale_days=30,
        )
    )
    tenant_admin = Operator(
        username="ta", password_hash=security.hash_password("x"), role=OperatorRole.tenant_admin
    )
    operator = Operator(
        username="op", password_hash=security.hash_password("x"), role=OperatorRole.operator
    )
    session.add_all([tenant_admin, operator])
    await session.flush()
    session.add_all(
        [
            RoleAssignment(
                operator_id=tenant_admin.id, tenant_id="lab", role=OperatorRole.tenant_admin
            ),
            RoleAssignment(operator_id=operator.id, tenant_id="lab", role=OperatorRole.operator),
            RoleAssignment(
                operator_id=tenant_admin.id, tenant_id="initech", role=OperatorRole.tenant_admin
            ),
        ]
    )
    await session.flush()

    app = FastAPI()
    app.include_router(itr_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: tenant_admin
    return {
        "app": app,
        "client": TestClient(app),
        "fake": fake,
        "tenant_admin": tenant_admin,
        "operator": operator,
        "session": session,
        "cache_store": cache_store,
    }


def test_export_ok(env):
    r = env["client"].post("/tenants/lab/reports/itr/export", json={"reason": "revisión mensual"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "itr_report_lab_" in r.headers["content-disposition"]
    assert "<html" in r.text.lower()


def test_export_privilege_cloud_no_muestra_pvwa_ni_aim(env):
    """PVWA/AIM son infraestructura que administra CyberArk en Privilege
    Cloud/ISPSS, no el equipo PAM (Alex, 2026-09-29) -- no aportan al
    reporte de un tenant cloud, y sus cuentas de servicio (ej. PVWAAppUser)
    tampoco deben aparecer en la tabla de conectividad detallada."""
    env["fake"].component_details_data = {
        "PVWA": [
            {
                "ComponentUserName": "PVWAAppUser",
                "ComponentIP": "10.0.0.5",
                "IsLoggedOn": True,
            }
        ],
        "AIM": [{"ComponentUserName": "AIMApp", "ComponentIP": "10.0.0.6", "IsLoggedOn": True}],
        "SessionManagement": [
            {"ComponentUserName": "PSMAppUser", "ComponentIP": "10.0.0.7", "IsLoggedOn": True}
        ],
    }
    r = env["client"].post("/tenants/lab/reports/itr/export", json={})
    assert r.status_code == 200
    assert "PVWAAppUser" not in r.text
    assert "AIMApp" not in r.text
    assert "PSMAppUser" in r.text


def test_export_itti_usa_el_mismo_layout_generico_que_cualquier_tenant(env):
    """Initech NO tiene un layout propio (Alex, 2026-09-29): el reporte tiene
    que salir tan completo como el de Globex/lab — las 10 pestañas en el
    layout genérico. Si se quiere sacar una pestaña puntual, es
    `excluded_tabs`, no un template distinto por cliente. `activityUsedChart`
    vive en la pestaña Sesiones del layout base — tiene que estar en las dos
    respuestas, en el mismo lugar."""
    r_base = env["client"].post("/tenants/lab/reports/itr/export", json={})
    r_itti = env["client"].post("/tenants/initech/reports/itr/export", json={})
    assert r_base.status_code == r_itti.status_code == 200
    assert "activityUsedChart" in r_base.text
    assert "activityUsedChart" in r_itti.text


def test_export_reason_opcional(env):
    r = env["client"].post("/tenants/lab/reports/itr/export", json={})
    assert r.status_code == 200


def test_export_sin_include_sessions_no_pega_a_activities(env):
    r = env["client"].post("/tenants/lab/reports/itr/export", json={})
    assert r.status_code == 200
    assert env["fake"].activities_calls == []


def test_export_include_sessions_pega_a_activities_por_cuenta(env):
    r = env["client"].post("/tenants/lab/reports/itr/export", json={"include_sessions": True})
    assert r.status_code == 200
    assert sorted(env["fake"].activities_calls) == sorted(a["id"] for a in ACCOUNTS)


def test_export_excluded_tabs_rechaza_id_desconocido(env):
    r = env["client"].post("/tenants/lab/reports/itr/export", json={"excluded_tabs": ["no-existe"]})
    assert r.status_code == 422


async def test_export_excluded_tabs_valido_ok_y_auditado(env, session):
    r = env["client"].post("/tenants/lab/reports/itr/export", json={"excluded_tabs": ["findings"]})
    assert r.status_code == 200

    rows = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "report.itr.generate")))
        .scalars()
        .all()
    )
    assert rows[0].detail["excluded_tabs"] == ["findings", "sessions"]


async def test_export_use_cache_evita_repegar_a_cyberark(env, session):
    r1 = env["client"].post("/tenants/lab/reports/itr/export", json={"use_cache": True})
    assert r1.status_code == 200
    assert env["fake"].list_accounts_calls == 1
    assert len(env["cache_store"]) == 1

    r2 = env["client"].post("/tenants/lab/reports/itr/export", json={"use_cache": True})
    assert r2.status_code == 200
    assert env["fake"].list_accounts_calls == 1  # no volvió a pedirlo


async def test_export_sin_use_cache_siempre_pega_a_cyberark(env):
    env["client"].post("/tenants/lab/reports/itr/export", json={})
    env["client"].post("/tenants/lab/reports/itr/export", json={})
    assert env["fake"].list_accounts_calls == 2


async def test_export_cache_separada_por_include_sessions(env):
    """Un hit de caché guardado SIN Sesiones no debe usarse cuando se pide
    CON Sesiones (mostraría la pestaña vacía por error) — la clave de
    caché incluye `include_sessions`."""
    env["client"].post("/tenants/lab/reports/itr/export", json={"use_cache": True})
    assert env["fake"].activities_calls == []

    env["client"].post(
        "/tenants/lab/reports/itr/export", json={"use_cache": True, "include_sessions": True}
    )
    assert sorted(env["fake"].activities_calls) == sorted(a["id"] for a in ACCOUNTS)


def test_export_rbac_operator_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["operator"]
    r = env["client"].post("/tenants/lab/reports/itr/export", json={})
    assert r.status_code == 403


def test_export_accounts_falla_aborta(env):
    env["fake"].fail_accounts = NotFound("no se pudo listar accounts")
    r = env["client"].post("/tenants/lab/reports/itr/export", json={})
    assert r.status_code == 404


def test_export_platforms_falla_aborta(env):
    env["fake"].fail_platforms = CyberArkError("500 servicio caído")
    r = env["client"].post("/tenants/lab/reports/itr/export", json={})
    assert r.status_code == 502


def test_export_fuente_no_critica_falla_no_rompe_el_reporte(env):
    """Connector Management caído (403, mismo caso real documentado en
    ITR) no debería abortar el resto del reporte."""
    env["fake"].fail_connectors = CyberArkError("403 forbidden")
    r = env["client"].post("/tenants/lab/reports/itr/export", json={"reason": "prueba"})
    assert r.status_code == 200
    assert "<html" in r.text.lower()


async def test_export_audita_ok(env, session):
    env["client"].post("/tenants/lab/reports/itr/export", json={"reason": "revisión mensual"})
    rows = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "report.itr.generate")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    detail = rows[0].detail
    assert detail["accounts"] == len(ACCOUNTS)
    assert detail["platforms"] == len(PLATFORMS)
    assert detail["safes"] == len(SAFES)
    assert detail["reason"] == "revisión mensual"
    assert rows[0].result == "ok"


async def test_export_incluye_el_logo_del_cliente_si_esta_cargado(env, session):
    """El logo se guarda como bytes en la fila del tenant (ver
    `app.api.tenants.put_tenant_logo`) — `app.itr.report_builder` sólo sabe
    leerlo desde un archivo, así que `app/api/itr.py` lo vuelca a un
    temporal antes de renderizar. Confirma que ese plomo funciona de
    punta a punta: el HTML final trae el logo embebido como data URI."""
    # PNG 1x1 real (no hace falta que sea válido para `_image_to_data_uri`,
    # que sólo lee bytes y los codifica en base64 — pero usar uno real es
    # más representativo del caso de uso).
    png_1x1 = bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000a49444154789c6360000002000155035c78000000"
        "0049454e44ae426082"
    )
    t = await session.get(Tenant, "lab")
    t.client_logo = png_1x1
    t.client_logo_content_type = "image/png"
    await session.flush()

    r = env["client"].post("/tenants/lab/reports/itr/export", json={})
    assert r.status_code == 200
    assert "data:image/png;base64," in r.text


async def test_export_audita_error(env, session):
    env["fake"].fail_accounts = NotFound("no se pudo listar accounts")
    env["client"].post("/tenants/lab/reports/itr/export", json={"reason": "revisión mensual"})
    rows = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "report.itr.generate")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].result == "error"


_HEALTHCHECK_JSON = json.dumps(
    {
        "Servidor": "psm01",
        "Fecha": "2026-09-18 10:00",
        "CPU": {"Porcentaje": 42},
        "RAM": {"Porcentaje": 60, "UsadoGB": 12, "TotalGB": 20},
        "Discos": [{"Unidad": "C:", "Porcentaje": 55, "UsadoGB": 110, "TotalGB": 200}],
        "ServiciosCyberArk": [{"Nombre": "CyberArk PSM Service", "Estado": "Running"}],
        "CertificadoRDP": {"Nombre": "psm01.cliente.local", "FechaVencimiento": "2027-01-01"},
    }
).encode("utf-8")


def test_infra_files_upload_rechaza_nombre_invalido(env):
    r = env["client"].post(
        "/tenants/lab/itr/infra-files",
        files={"files": ("otro.json", b"{}", "application/json")},
    )
    assert r.status_code == 422


def test_infra_files_rbac_operator_rechazado(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["operator"]
    r = env["client"].post(
        "/tenants/lab/itr/infra-files",
        files={"files": ("HealthCheck_psm01_20260918.json", _HEALTHCHECK_JSON, "application/json")},
    )
    assert r.status_code == 403


async def test_infra_files_upload_list_delete(env, session):
    r = env["client"].post(
        "/tenants/lab/itr/infra-files",
        files={"files": ("HealthCheck_psm01_20260918.json", _HEALTHCHECK_JSON, "application/json")},
    )
    assert r.status_code == 200
    assert r.json() == {"tenant": "lab", "uploaded": 1, "replaced": 0}

    listed = env["client"].get("/tenants/lab/itr/infra-files").json()
    assert len(listed["files"]) == 1
    file_id = listed["files"][0]["id"]
    assert listed["files"][0]["filename"] == "HealthCheck_psm01_20260918.json"

    rows = (
        (await session.execute(select(AuditLog).where(AuditLog.action == "itr.infra_file.upload")))
        .scalars()
        .all()
    )
    assert len(rows) == 1

    d = env["client"].delete(f"/tenants/lab/itr/infra-files/{file_id}")
    assert d.status_code == 200
    assert env["client"].get("/tenants/lab/itr/infra-files").json()["files"] == []


async def test_export_incluye_datos_de_infraestructura_si_hay_archivos(env):
    env["client"].post(
        "/tenants/lab/itr/infra-files",
        files={"files": ("HealthCheck_psm01_20260918.json", _HEALTHCHECK_JSON, "application/json")},
    )
    r = env["client"].post("/tenants/lab/reports/itr/export", json={})
    assert r.status_code == 200
    assert "psm01" in r.text


async def _itr_audit_rows(session) -> list[AuditLog]:
    return list(
        (await session.execute(select(AuditLog).where(AuditLog.action == "report.itr.generate")))
        .scalars()
        .all()
    )


def test_export_sin_fallas_no_muestra_banner_de_datos_incompletos(env):
    r = env["client"].post("/tenants/lab/reports/itr/export", json={})
    assert r.status_code == 200
    assert "Datos incompletos" not in r.text


async def test_export_policy_failures_caido_avisa_compliance_parcial(env, session):
    """Sin `PolicyFailures`, las cuentas fuera de política se contarían como
    conformes: el % sale inflado. El reporte tiene que decirlo."""
    env["fake"].fail_filters = {"PolicyFailures": CyberArkError("500 servicio caído")}
    r = env["client"].post("/tenants/lab/reports/itr/export", json={})
    assert r.status_code == 200
    assert "Datos incompletos" in r.text
    assert "Compliance es parcial" in r.text

    rows = await _itr_audit_rows(session)
    assert "filter:PolicyFailures" in rows[0].detail["source_errors"]


def test_export_safes_caido_avisa_en_vez_de_mostrar_ceros(env):
    env["fake"].fail_safes = CyberArkError("500 servicio caído")
    r = env["client"].post("/tenants/lab/reports/itr/export", json={})
    assert r.status_code == 200
    assert "No se pudo consultar: Safes" in r.text


def test_export_connectors_sin_url_no_cuenta_como_falla(env):
    """Un tenant sin Connector Management/SIA configurado no es una fuente
    caída: no debe ensuciar el banner."""
    env["fake"].connector_management_url = None
    env["fake"].sia_connector_url = None
    env["fake"].fail_connectors = CyberArkError("no debería llamarse")
    r = env["client"].post("/tenants/lab/reports/itr/export", json={})
    assert r.status_code == 200
    assert "Datos incompletos" not in r.text


def test_export_cache_hit_lo_avisa_en_el_reporte(env):
    env["client"].post("/tenants/lab/reports/itr/export", json={"use_cache": True})
    r = env["client"].post("/tenants/lab/reports/itr/export", json={"use_cache": True})
    assert r.status_code == 200
    assert "Datos tomados de la caché" in r.text


async def test_export_falla_al_renderizar_queda_auditada(env, session, monkeypatch):
    def boom(**kwargs):
        raise ValueError("plantilla rota")

    monkeypatch.setattr(itr_mod.report_builder, "build_html_report", boom)
    r = env["client"].post("/tenants/lab/reports/itr/export", json={})
    assert r.status_code == 500

    rows = await _itr_audit_rows(session)
    assert len(rows) == 1
    assert rows[0].result == "error"
    assert rows[0].detail["phase"] == "render"


def test_infra_files_upload_rechaza_separadores_de_ruta(env):
    for name in ("HealthCheck_/../../x.json", r"HealthCheck_..\..\x.json"):
        r = env["client"].post(
            "/tenants/lab/itr/infra-files",
            files={"files": (name, _HEALTHCHECK_JSON, "application/json")},
        )
        assert r.status_code == 422, name


def test_infra_files_upload_mismo_nombre_reemplaza(env):
    name = "HealthCheck_psm01_20260918.json"
    for _ in range(2):
        r = env["client"].post(
            "/tenants/lab/itr/infra-files",
            files={"files": (name, _HEALTHCHECK_JSON, "application/json")},
        )
        assert r.status_code == 200
    assert r.json()["replaced"] == 1
    assert len(env["client"].get("/tenants/lab/itr/infra-files").json()["files"]) == 1


def test_infra_files_upload_rechaza_repetido_en_el_lote(env):
    name = "HealthCheck_psm01_20260918.json"
    r = env["client"].post(
        "/tenants/lab/itr/infra-files",
        files=[
            ("files", (name, _HEALTHCHECK_JSON, "application/json")),
            ("files", (name, _HEALTHCHECK_JSON, "application/json")),
        ],
    )
    assert r.status_code == 422


def test_export_infra_json_invalido_no_rompe_el_reporte(env):
    """Un HealthCheck con estructura inesperada (una lista en vez de un
    objeto) se ignora con aviso, en vez de tirar abajo todo el reporte."""
    env["client"].post(
        "/tenants/lab/itr/infra-files",
        files=[
            ("files", ("HealthCheck_psm01_20260918.json", _HEALTHCHECK_JSON, "application/json")),
            ("files", ("HealthCheck_roto_20260918.json", b"[1, 2]", "application/json")),
        ],
    )
    r = env["client"].post("/tenants/lab/reports/itr/export", json={})
    assert r.status_code == 200
    assert "psm01" in r.text
    assert "HealthCheck_roto_20260918.json" in r.text
