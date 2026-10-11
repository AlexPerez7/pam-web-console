"""Reportes — funciones puras + endpoint (docs/specs/reportes.md)."""

from __future__ import annotations

import types
from contextlib import asynccontextmanager

import pytest_asyncio
from arq.jobs import JobStatus
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.reports import catalog_router, router
from app.core import security
from app.core.deps import current_operator
from app.db import get_session
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant
from app.reports._identity import days_since, parse_redrock_epoch
from app.reports.account_activity import compute_account_activity
from app.reports.account_inventory import compute_account_inventory, compute_account_inventory_full
from app.reports.license_candidates import compute_license_candidates
from app.reports.psm_balance import compute_psm_balance
from app.reports.safe_duplicates import compute_safe_duplicates
from app.reports.safe_permissions import compute_safe_permissions
from app.reports.target_inactivity import compute_target_inactivity


def test_compute_safe_duplicates():
    accounts = [
        {"id": "1", "safeName": "S", "address": "host.a", "userName": "u1", "createdTime": 100},
        {"id": "2", "safeName": "S", "address": "HOST.A ", "userName": "u2", "createdTime": 50},
        {"id": "3", "safeName": "S", "address": "host.b", "userName": "u3", "createdTime": 10},
        {"id": "4", "safeName": "T", "address": "host.a", "userName": "u4", "createdTime": 10},
    ]
    rows = compute_safe_duplicates(accounts)
    # sólo el grupo (S, host.a) con 2 cuentas
    assert {r["accountId"] for r in rows} == {"1", "2"}
    assert all(r["cuentasEnGrupo"] == 2 for r in rows)
    oldest = next(r for r in rows if r["masAntiguaDelGrupo"])
    assert oldest["accountId"] == "2"  # createdTime 50 < 100


def test_compute_safe_permissions():
    by_safe = {
        "S": [
            {
                "memberName": "PAM-Admins",
                "memberType": "Group",
                "permissions": {"retrieveAccounts": True, "manageSafe": True},
            }
        ]
    }
    rows = compute_safe_permissions(by_safe)
    assert rows[0]["safeName"] == "S"
    assert rows[0]["retrieveAccounts"] is True
    assert rows[0]["manageSafeMembers"] is False


def test_compute_account_inventory():
    rows = compute_account_inventory(
        [
            {
                "id": "1",
                "name": "web01",
                "secretManagement": {
                    "automaticManagementEnabled": True,
                    "status": "success",
                    "lastVerifiedTime": 1700000000,
                },
            }
        ]
    )
    assert rows[0]["autoManaged"] is True
    assert rows[0]["lastVerified"] == 1700000000


def test_compute_account_inventory_full():
    accounts = [
        {"id": "1", "name": "acc1"},  # PSM Connect más reciente que un retiro
        {"id": "2", "name": "acc2"},  # última falla es de Change
        {"id": "3", "name": "acc3"},  # última falla es de Verify
        {"id": "4", "name": "acc4"},  # sin actividad
    ]
    activities = {
        "1": [
            {"Action": "Show Password", "Date": 100, "User": "opA"},
            {"Action": "PSM Connect", "Date": 200, "User": "opB"},
        ],
        "2": [
            {"Action": "Change Password", "Date": 100, "Reason": "timeout"},
            {"Action": "CPM Change Password Failed", "Date": 300, "Reason": "target unreachable"},
            {"Action": "CPM Verify Password Failed", "Date": 200, "Reason": "old reason"},
        ],
        "3": [{"Action": "CPM Verify Password Failed", "Date": 100, "Reason": "wrong creds"}],
        "4": [],
    }
    rows = compute_account_inventory_full(accounts, activities)
    by_id = {r["id"]: r for r in rows}

    assert by_id["1"]["lastAccess"] == 200
    assert by_id["1"]["lastAccessBy"] == "opB"
    assert by_id["1"]["changeFailure"] is False
    assert by_id["1"]["verificationFailure"] is False

    # la falla MÁS RECIENTE (Date 300, Change) es la que cuenta, no la de
    # Verify que pasó antes
    assert by_id["2"]["changeFailure"] is True
    assert by_id["2"]["verificationFailure"] is False
    assert by_id["2"]["failureReason"] == "target unreachable"

    assert by_id["3"]["changeFailure"] is False
    assert by_id["3"]["verificationFailure"] is True
    assert by_id["3"]["failureReason"] == "wrong creds"

    assert by_id["4"]["lastAccess"] is None
    assert by_id["4"]["changeFailure"] is False
    assert by_id["4"]["failureReason"] is None


def test_compute_account_inventory_full_sin_activities_no_rompe():
    accounts = [{"id": "1", "name": "a"}]
    rows = compute_account_inventory_full(accounts, {"1": None})
    assert rows[0]["lastAccess"] is None
    assert rows[0]["changeFailure"] is False


def test_compute_account_inventory_full_falla_de_sesion_psm_no_tapa_la_del_cpm():
    """Bug real encontrado contra Globex (2026-09-24, 429 de 1892
    cuentas): una falla de sesión PSM (Action tipo PSMSR280E/605E, nada
    que ver con el CPM) más reciente que la última falla REAL de cambio/
    verificación tapaba el motivo correcto, porque el filtro original
    sólo miraba si el Action decía "fail", sin exigir que fuera
    clasificable como change/verify/reconcile."""
    accounts = [{"id": "1", "name": "a"}]
    activities = {
        "1": [
            {"Action": "CPM Change Password Failed", "Date": 100, "Reason": "target unreachable"},
            {
                "Action": "PSMSR280E Session component failed",
                "Date": 200,
                "Reason": "session crashed",
            },
        ]
    }
    rows = compute_account_inventory_full(accounts, activities)
    assert rows[0]["changeFailure"] is True
    assert rows[0]["verificationFailure"] is False
    assert rows[0]["failureReason"] == "target unreachable"


def test_compute_psm_balance():
    platforms = [
        {
            "general": {"id": "WinSrv", "name": "Windows Server", "active": True},
            "sessionManagement": {"psmServerId": "PSM-A"},
        },
        {
            "general": {"id": "Unix", "name": "Unix SSH", "active": True},
            "privilegedSessionManagement": {"psmServerId": "PSM-B"},
        },
        {"general": {"id": "NoPsm", "name": "Sin PSM", "active": False}},
    ]
    accounts = (
        [{"platformId": "WinSrv"}] * 3
        + [{"platformId": "Unix"}] * 1
        + [{"platformId": "NoPsm"}] * 2
    )
    psm_servers = [{"ID": "PSM-A", "Name": "psm-lab-01"}, {"ID": "PSM-B", "Name": "psm-lab-02"}]
    rows, meta = compute_psm_balance(platforms, accounts, psm_servers)

    by_platform = {r["platformId"]: r for r in rows}
    assert by_platform["WinSrv"]["psmServerName"] == "psm-lab-01"
    assert by_platform["WinSrv"]["accounts"] == 3
    assert by_platform["Unix"]["psmServerName"] == "psm-lab-02"
    assert by_platform["NoPsm"]["psmServerName"] == "— sin PSM —"
    assert meta["cuentas_total"] == 6
    assert meta["psm_configurados"] == 2
    assert "50%" in meta["psm-lab-01"]  # 3 de 6


def test_parse_redrock_epoch():
    assert parse_redrock_epoch("/Date(1700000000000+0000)/") == 1700000000
    assert parse_redrock_epoch("2023-11-14T22:13:20Z") == 1700000000
    assert parse_redrock_epoch(None) is None
    assert parse_redrock_epoch("") is None
    assert parse_redrock_epoch("no-es-fecha") is None
    assert days_since(None) is None
    assert days_since(0) is not None


def test_compute_license_candidates():
    """Fusión con identity-user-activity (2026-09-22): ya no filtra por
    userType -- svc-app (AppProvider, sin licencia) queda incluido, con
    licenseType "Sin licencia", igual que antes lo mostraba el reporte
    dado de baja."""
    recent = "/Date(2000000000000)/"  # 2033 -> siempre futuro relativo a "now"
    vault_users = [
        {"username": "svc-epv", "userType": "EPVUser"},
        {"username": "svc-app", "userType": "AppProvider"},  # no consume licencia
    ]
    identity_users = [
        {
            "Username": "svc-epv",
            "Status": "Active",
            "LastLogin": "/Date(1000000000000)/",  # 2001, vencido
        },
        {"Username": "ext-vendor", "UserType": "ExtUser", "Status": "Active", "LastLogin": None},
        {"Username": "ana", "Status": "Active", "LastLogin": recent},
        {"Username": "caro", "Status": "Disabled", "LastLogin": recent},
    ]
    rows, meta = compute_license_candidates(vault_users, identity_users, inactive_days=30)
    by_user = {r["username"]: r for r in rows}
    assert set(by_user) == {"svc-epv", "svc-app", "ext-vendor", "ana", "caro"}
    assert by_user["svc-app"]["licenseType"] == "Sin licencia"
    assert by_user["svc-app"]["candidato"] is True  # sin registro en Identity -> sin login
    assert by_user["svc-epv"]["inVault"] is True
    assert by_user["svc-epv"]["candidato"] is True  # login de 2001
    assert by_user["ext-vendor"]["inVault"] is False
    assert by_user["ext-vendor"]["candidato"] is True  # nunca inició sesión
    assert by_user["ana"]["candidato"] is False  # login reciente, activo
    assert by_user["caro"]["candidato"] is True  # deshabilitado, aunque el login sea reciente
    assert meta["usuarios_totales"] == 5
    assert meta["con_licencia"] == 2  # svc-epv + ext-vendor
    assert meta["candidatos"] == 4  # todos menos ana
    # los candidatos van primero
    assert rows[0]["candidato"] is True


def test_compute_target_inactivity():
    now = 1_700_000_000
    accounts = [
        {
            "id": "1",
            "name": "acc1",
            "safeName": "S",
            "address": "a",
            "userName": "u1",
            "platformId": "P",
        },
        {
            "id": "2",
            "name": "acc2",
            "safeName": "S",
            "address": "b",
            "userName": "u2",
            "platformId": "P",
        },
        {
            "id": "3",
            "name": "acc3",
            "safeName": "S",
            "address": "c",
            "userName": "u3",
            "platformId": "P",
        },
        {
            "id": "4",
            "name": "acc4",
            "safeName": "S",
            "address": "d",
            "userName": "u4",
            "platformId": "P",
        },
    ]
    ten_days_ago = now - 10 * 86400
    two_hundred_days_ago = now - 200 * 86400
    activities = {
        "1": [{"Action": "PSM Connect", "Date": ten_days_ago}],
        "2": [{"Action": "Connect", "Date": two_hundred_days_ago}],
        "3": [],
        "4": [{"Action": "Show Password", "Date": two_hundred_days_ago}],
    }
    rows = compute_target_inactivity(accounts, activities, inactive_days=180, now_epoch=now)
    by_id = {r["accountId"]: r for r in rows}
    assert set(by_id) == {"2", "3", "4"}  # "1" tuvo acceso hace 10 días, no entra
    assert by_id["2"]["accessType"] == "Solo PSM Connect"
    assert by_id["3"]["accessType"] == "Sin actividad registrada"
    assert by_id["3"]["daysSinceAccess"] is None
    assert (
        by_id["4"]["accessType"]
        == "Solo retiro de credencial (Show/Copy Password), sin PSM Connect"
    )
    assert rows[0]["accountId"] == "3"  # nunca accedida -> ordena primero (la más crítica)


def test_compute_target_inactivity_sin_actividad_no_rompe():
    """Una cuenta sin permiso Retrieve/Activities (queda en `None`, ver
    `app.reports._activities.all_account_activities`) se trata igual que
    "sin actividad registrada", no rompe el cómputo."""
    now = 1_700_000_000
    accounts = [
        {
            "id": "1",
            "name": "a",
            "safeName": "S",
            "address": "x",
            "userName": "u",
            "platformId": "P",
        }
    ]
    rows = compute_target_inactivity(accounts, {"1": None}, inactive_days=180, now_epoch=now)
    assert rows[0]["accessType"] == "Sin actividad registrada"


def test_compute_account_activity():
    now = 1_700_000_000
    accounts = [
        {
            "id": "1",
            "name": "a1",
            "safeName": "S",
            "address": "x",
            "userName": "u1",
            "platformId": "P",
        },
        {
            "id": "2",
            "name": "a2",
            "safeName": "S",
            "address": "y",
            "userName": "u2",
            "platformId": "P",
        },
    ]
    within_window = now - 5 * 86400
    outside_window = now - 40 * 86400
    activities = {
        "1": [
            {"Action": "PSM Connect", "Date": within_window},
            {"Action": "PSM Connect", "Date": within_window},
            {"Action": "PSM Connect", "Date": outside_window},  # fuera de la ventana, no cuenta
        ],
        "2": [],
    }
    rows = compute_account_activity(accounts, activities, now_epoch=now)
    by_id = {r["accountId"]: r for r in rows}
    assert by_id["1"]["sessions30d"] == 2
    assert by_id["2"]["sessions30d"] == 0
    assert rows[0]["accountId"] == "1"  # ordenado por sesiones descendente


# --- endpoint --------------------------------------------------------- #
class FakeClient:
    async def list_accounts(self, search=None):
        return [
            {"id": "1", "safeName": "S", "address": "a", "userName": "u1", "createdTime": 1},
            {"id": "2", "safeName": "S", "address": "a", "userName": "u2", "createdTime": 2},
        ]

    async def list_safes(self):
        return [{"safeName": "S", "safeUrlId": "S"}]

    async def safe_members(self, sid):
        return [{"memberName": "g", "memberType": "Group", "permissions": {"listAccounts": True}}]


@pytest_asyncio.fixture
async def env(session, monkeypatch):
    import app.api.reports as reports_mod

    @asynccontextmanager
    async def fake_tc(tenant_id, sess):
        yield (None, FakeClient())

    monkeypatch.setattr(reports_mod, "tenant_client", fake_tc)

    async def noget(k):
        return None

    async def noset(k, v, ttl):
        return None

    monkeypatch.setattr(reports_mod.cache, "get_json", noget)
    monkeypatch.setattr(reports_mod.cache, "set_json", noset)

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
    op = Operator(username="v", password_hash=security.hash_password("x"), role=OperatorRole.viewer)
    session.add(op)
    await session.flush()
    session.add(RoleAssignment(operator_id=op.id, tenant_id="lab", role=OperatorRole.viewer))
    await session.flush()

    app = FastAPI()
    app.include_router(catalog_router)
    app.include_router(router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: op
    return TestClient(app)


def test_catalog(env):
    r = env.get("/reports")
    assert r.status_code == 200
    data = r.json()
    names = {x["name"] for x in data}
    assert {"safe-duplicates", "safe-permissions", "account-inventory"} <= names
    by_name = {x["name"]: x for x in data}
    assert by_name["safe-duplicates"]["category"] == "Safes"
    assert by_name["account-inventory"]["category"] == "Cuentas"


def test_run_report(env):
    r = env.get("/tenants/lab/reports/safe-duplicates")
    assert r.status_code == 200
    body = r.json()
    assert body["columns"][0]["key"] == "safeName"
    assert len(body["rows"]) == 2  # el grupo duplicado


def test_report_desconocido(env):
    assert env.get("/tenants/lab/reports/no-existe").status_code == 404


def test_export_csv(env):
    r = env.get("/tenants/lab/reports/safe-permissions/export?format=csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    assert r.text.splitlines()[0].startswith("Safe,")


def test_export_xlsx_solo_en_reportes_que_lo_declaran(env):
    r = env.get("/tenants/lab/reports/safe-permissions/export?format=xlsx")
    assert r.status_code == 422
    assert "csv, json" in r.json()["detail"]


def test_export_xlsx_desde_el_resultado_del_job(env, monkeypatch):
    import app.api.reports as reports_mod

    cached = {
        "columns": [{"key": "systemType", "label": "Sistema", "kind": "text"}],
        "rows": [{"systemType": "Windows"}],
        "meta": {
            "by_system": [
                {
                    "system": "Windows",
                    "total": 2,
                    "cpm_errors": 1,
                    "overdue": 0,
                    "failing": 1,
                    "pct": 50.0,
                }
            ],
            "cuentas_consideradas": 2,
            "con_problemas": 1,
            "porcentaje": 50.0,
        },
        "cached": False,
        "generated_at": "2026-10-07T15:00:00+00:00",
    }

    async def fake_get(_key):
        return dict(cached)

    monkeypatch.setattr(reports_mod.cache, "get_json", fake_get)
    r = env.get("/tenants/lab/reports/rotation-failures-by-system/export?format=xlsx")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert ".xlsx" in r.headers["content-disposition"]
    assert r.content[:2] == b"PK"  # un .xlsx es un zip


# --- Batch 4: reportes "heavy" (jobs ARQ) ------------------------------ #
class FakeEnqueuedJob:
    job_id = "job-123"


class FakePool:
    async def enqueue_job(self, name, *args):
        return FakeEnqueuedJob()


class FakeArqJob:
    def __init__(self, status, *, result=None, success=True):
        self._status = status
        self._result = result
        self._success = success

    async def status(self):
        return self._status

    async def result_info(self):
        if self._result is None:
            return None
        return types.SimpleNamespace(success=self._success, result=self._result)


def test_run_job_reporte_no_heavy_422(env):
    r = env.post("/tenants/lab/reports/safe-duplicates/run-job")
    assert r.status_code == 422


def test_run_job_reporte_desconocido_404(env):
    assert env.post("/tenants/lab/reports/no-existe/run-job").status_code == 404


def test_run_job_heavy_ok(env, monkeypatch):
    import app.api.reports as reports_mod

    async def fake_pool():
        return FakePool()

    monkeypatch.setattr(reports_mod, "get_arq_pool", fake_pool)
    r = env.post("/tenants/lab/reports/target-inactivity/run-job")
    assert r.status_code == 200
    assert r.json() == {"job_id": "job-123"}


def test_get_heavy_report_sin_cache_501(env):
    r = env.get("/tenants/lab/reports/target-inactivity")
    assert r.status_code == 501


def test_get_heavy_report_con_cache_200(env, monkeypatch):
    import app.api.reports as reports_mod

    async def fake_get(_key):
        return {"columns": [], "rows": [], "meta": {}, "cached": False, "generated_at": "x"}

    monkeypatch.setattr(reports_mod.cache, "get_json", fake_get)
    r = env.get("/tenants/lab/reports/target-inactivity")
    assert r.status_code == 200
    assert r.json()["cached"] is True


def test_job_status_en_progreso(env, monkeypatch):
    import app.api.reports as reports_mod

    async def fake_pool():
        return object()

    monkeypatch.setattr(reports_mod, "get_arq_pool", fake_pool)
    monkeypatch.setattr(
        reports_mod, "ArqJob", lambda job_id, pool: FakeArqJob(JobStatus.in_progress)
    )
    r = env.get("/tenants/lab/reports/target-inactivity/jobs/job-123")
    assert r.status_code == 200
    assert r.json() == {"status": "in_progress"}


def test_job_status_no_encontrado_404(env, monkeypatch):
    import app.api.reports as reports_mod

    async def fake_pool():
        return object()

    monkeypatch.setattr(reports_mod, "get_arq_pool", fake_pool)
    monkeypatch.setattr(reports_mod, "ArqJob", lambda job_id, pool: FakeArqJob(JobStatus.not_found))
    r = env.get("/tenants/lab/reports/target-inactivity/jobs/job-123")
    assert r.status_code == 404


def test_job_status_completo_devuelve_resultado(env, monkeypatch):
    import app.api.reports as reports_mod

    async def fake_pool():
        return object()

    monkeypatch.setattr(reports_mod, "get_arq_pool", fake_pool)
    monkeypatch.setattr(
        reports_mod,
        "ArqJob",
        lambda job_id, pool: FakeArqJob(
            JobStatus.complete,
            result={
                "columns": [],
                "rows": [{"a": 1}],
                "meta": {},
                "cached": False,
                "generated_at": "x",
            },
        ),
    )
    r = env.get("/tenants/lab/reports/target-inactivity/jobs/job-123")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "complete"
    assert body["rows"] == [{"a": 1}]


def test_job_status_fallido_502(env, monkeypatch):
    import app.api.reports as reports_mod

    async def fake_pool():
        return object()

    monkeypatch.setattr(reports_mod, "get_arq_pool", fake_pool)
    monkeypatch.setattr(
        reports_mod,
        "ArqJob",
        lambda job_id, pool: FakeArqJob(JobStatus.complete, result="boom", success=False),
    )
    r = env.get("/tenants/lab/reports/target-inactivity/jobs/job-123")
    assert r.status_code == 502
