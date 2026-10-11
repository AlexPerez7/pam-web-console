"""Reporte `non-rotating-accounts` (cuentas que no rotan)."""

from __future__ import annotations

import asyncio
import time
import types

from app.reports import REGISTRY
from app.reports.base import multi_values
from app.reports.non_rotating_accounts import (
    attach_last_errors,
    compute_non_rotating,
    is_cyberark_internal,
    needs_last_error,
    report,
)

NOW = int(time.time())
DAY = 86400


def _acc(acc_id, *, auto=True, status="success", age_days=1, kind="password", **kw):
    sm = {
        "automaticManagementEnabled": auto,
        "status": status,
        "lastModifiedTime": NOW - age_days * DAY if age_days is not None else None,
    }
    sm.update(kw.pop("sm", {}))
    return {
        "id": acc_id,
        "name": f"acc{acc_id}",
        "userName": kw.pop("user", f"u{acc_id}"),
        "address": "10.0.0.1",
        "platformId": kw.pop("platform", "WinDomain"),
        "safeName": kw.pop("safe", "S1"),
        "secretType": kind,
        "secretManagement": sm,
    }


ACCOUNTS = [
    _acc("ok"),  # rota bien -> fuera
    _acc("fail", status="failure"),  # error del CPM
    _acc("overdue", age_days=100),  # gestionada pero vencida (umbral 30)
    _acc("manual", auto=False, age_days=200, sm={"manualManagementReason": "Solicitud"}),
    _acc(
        "cpmoff",
        auto=False,
        age_days=5,
        status="failure",
        sm={"manualManagementReason": "(CPM)MaxRetries"},
    ),
    _acc("key", kind="key", age_days=400),  # llave SSH vencida
]


def _compute(**kw):
    return compute_non_rotating(
        ACCOUNTS,
        filter_ids=kw.pop("filter_ids", {}),
        platform_days=kw.pop("platform_days", {}),
        default_stale_days=30,
        **kw,
    )


def _ids(rows):
    return {r["id"] for r in rows}


def test_sin_filtros_trae_todas_las_que_no_rotan():
    rows, meta = _compute()
    assert _ids(rows) == {"fail", "overdue", "manual", "cpmoff", "key"}
    assert meta["no_rotan"] == 5
    by_id = {r["id"]: r for r in rows}
    assert "Error de rotación/verificación (CPM)" in by_id["fail"]["reasons"]
    assert by_id["overdue"]["reasons"] == "Gestionada pero vencida"
    assert by_id["manual"]["reasons"] == "Gestión deshabilitada y vencida"
    assert by_id["overdue"]["thresholdDays"] == 30
    assert by_id["overdue"]["ageDays"] == 100


def test_excluir_gestion_deshabilitada_por_usuario_y_llaves_ssh():
    rows, meta = _compute(exclude={"user_disabled", "ssh_keys"})
    # "cpmoff" la deshabilitó el CPM tras fallar: NO es una exclusión por usuario
    assert _ids(rows) == {"fail", "overdue", "cpmoff"}
    assert meta["excluidas_user_disabled"] == 1
    assert meta["excluidas_ssh_keys"] == 1


def test_excluir_deshabilitadas_por_el_cpm():
    rows, _ = _compute(exclude={"cpm_disabled"})
    assert "cpmoff" not in _ids(rows)
    assert "manual" in _ids(rows)


def test_is_cyberark_internal_por_usuario_y_por_safe():
    internal = [
        {"userName": "PluginManagerUser", "safeName": "CPM_Globex"},
        {"userName": "PSMConnect", "safeName": "PSM_Host01"},
        {"userName": "psmadminconnect", "safeName": "X"},
        {"userName": "PSMApp_HOST01", "safeName": "X"},
        {"name": "Operating System", "safeName": "PasswordManager_Pending"},
        {"userName": "x", "safeName": "PVWAConfig"},
        {"userName": "x", "safeName": "Notification Engine"},
    ]
    assert all(is_cyberark_internal(a) for a in internal)
    client = [
        {"userName": "aws-master@globex.example", "safeName": "GLX-AWS"},
        {"userName": "Administrator", "safeName": "Windows-Servers"},
        {"userName": "psm_operador", "safeName": "Linux"},
    ]
    assert not any(is_cyberark_internal(a) for a in client)


def test_excluir_cuentas_internas_de_cyberark():
    accounts = [
        *ACCOUNTS,
        _acc("plugin", auto=False, age_days=300, user="PluginManagerUser"),
        _acc("psm", age_days=300, user="PSMConnect"),
    ]
    rows, meta = compute_non_rotating(
        accounts,
        filter_ids={},
        platform_days={},
        default_stale_days=30,
        exclude={"cyberark_internal"},
    )
    assert "plugin" not in _ids(rows)
    assert "psm" not in _ids(rows)
    assert "overdue" in _ids(rows)
    assert meta["excluidas_cyberark_internal"] == 2


def test_filtrar_por_motivos():
    rows, _ = _compute(reasons={"CpmFailure"})
    assert _ids(rows) == {"fail", "cpmoff"}


def test_savedfilters_de_cyberark_suman_motivos():
    rows, _ = _compute(filter_ids={"PolicyFailures": {"ok"}, "Locked": {"ok"}})
    by_id = {r["id"]: r for r in rows}
    assert by_id["ok"]["reasons"] == "Incumple la política (CyberArk); Bloqueada"


def test_umbral_de_la_plataforma_tiene_prioridad():
    rows, _ = _compute(platform_days={"WinDomain": 365})
    # 100 y 200 días ya no vencen con un umbral de 365
    assert _ids(rows) == {"fail", "cpmoff", "key"}


def test_filtro_por_safe_y_plataforma_sin_distinguir_mayusculas():
    rows, _ = _compute(safe_name="s1", platform_id="windomain")
    assert len(rows) == 5
    rows, _ = _compute(safe_name="otra")
    assert rows == []


def test_ultimo_error_solo_para_cuentas_con_falla_del_cpm():
    rows, _ = _compute()
    assert {r["id"] for r in rows if needs_last_error(r)} == {"fail", "cpmoff"}

    acts = {
        "fail": [
            {"Date": 200, "Action": "PSM Connect failed", "Reason": "PSMSR280E"},
            {"Date": 100, "Action": "CPM Change Password Failed", "Reason": "Clave rechazada"},
        ],
        "cpmoff": None,  # Activities falló
    }
    errors = attach_last_errors(rows, acts)
    by_id = {r["id"]: r for r in rows}
    assert errors == 1
    assert by_id["fail"]["lastErrorKind"] == "Cambio"
    assert by_id["fail"]["lastErrorReason"] == "Clave rechazada"
    assert by_id["fail"]["lastErrorDate"] == 100
    assert by_id["overdue"]["lastErrorReason"] is None


def test_multi_values_descarta_valores_desconocidos():
    assert multi_values({"m": "A, B,X,"}, "m", ["A", "B"]) == {"A", "B"}
    assert multi_values({}, "m", ["A"]) == set()


def test_registrado_heavy_y_catalogo_expone_multiselect():
    assert REGISTRY["non-rotating-accounts"] is report
    cat = report.catalog()
    assert cat["heavy"] is True
    motivos = next(p for p in cat["params"] if p["key"] == "motivos")
    assert motivos["type"] == "multiselect"
    assert motivos["option_labels"]["CpmFailure"].startswith("Error")


def test_run_params_suma_el_umbral_del_tenant_sin_tocar_los_params():
    params = {"motivos": "CpmFailure"}
    merged = report.run_params(types.SimpleNamespace(compliance_stale_days=90), params)
    assert merged["_default_stale_days"] == 90
    assert "_default_stale_days" not in params


class _FakeClient:
    def __init__(self):
        self.activity_calls: list[str] = []

    async def list_accounts(self, search=None):
        return ACCOUNTS

    async def list_platforms(self):
        return []

    async def saved_filter_accounts(self, name):
        return [{"id": "ok"}] if name == "Locked" else []

    async def account_activities(self, acc_id):
        self.activity_calls.append(acc_id)
        return [{"Date": 1, "Action": "CPM Verify Password Failed", "Reason": "timeout"}]


def test_run_completo_con_y_sin_ultimo_error():
    client = _FakeClient()
    params = report.run_params(
        types.SimpleNamespace(compliance_stale_days=30),
        {"excluir": "user_disabled,ssh_keys"},
    )
    result = asyncio.run(report.run(client, params))
    ids = {r["id"] for r in result.rows}
    assert ids == {"ok", "fail", "overdue", "cpmoff"}
    assert sorted(client.activity_calls) == ["cpmoff", "fail"]
    assert all("reasonCodes" not in r for r in result.rows)
    fail = next(r for r in result.rows if r["id"] == "fail")
    assert fail["lastErrorKind"] == "Verificación"

    client = _FakeClient()
    result = asyncio.run(report.run(client, {"incluir_ultimo_error": "No"}))
    assert client.activity_calls == []
    assert "ultimo_error_consultado" not in result.meta
