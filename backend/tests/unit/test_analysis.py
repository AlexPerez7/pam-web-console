"""Tests de las funciones puras de app.analysis (portadas de ITR)."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from app import analysis


def _acc(
    acc_id: str,
    *,
    status: str,
    auto: bool = True,
    reason: str = "",
    age_days: int | None = None,
    secret_type: str | None = None,
) -> dict:
    sm = {
        "status": status,
        "automaticManagementEnabled": auto,
        "manualManagementReason": reason,
    }
    if age_days is not None:
        last_changed = datetime.now(UTC) - timedelta(days=age_days)
        sm["lastModifiedTime"] = int(last_changed.timestamp())
    acc = {
        "id": acc_id,
        "platformId": "WinDomain",
        "secretManagement": sm,
    }
    if secret_type is not None:
        acc["secretType"] = secret_type
    return acc


def test_analyze_accounts_failed_ids():
    accounts = [
        _acc("1", status="success"),
        _acc("2", status="failure"),
        _acc("3", status="failure", auto=False),
        {"id": "4", "secretManagement": {}},  # sin plataforma ni status
    ]
    out = analysis.analyze_accounts(accounts)

    assert out["total"] == 4
    assert out["failed"] == 2
    assert out["failed_ids"] == {"2", "3"}
    assert out["no_platform"] == 1
    assert out["auto_managed"] == 2  # 1 y 2 (3 tiene auto=False, 4 no lo trae)
    # 3 y 4: gestión deshabilitada/ausente + nunca rotada (sin lastModifiedTime)
    assert out["disabled_stale"] == 2
    # 1 y 2: gestión activa pero también nunca rotada (sin lastModifiedTime)
    assert out["active_overdue"] == 2


def test_analyze_accounts_disabled_stale_caso_itti():
    """Caso real (Initech, 2026-09-17): gestión deshabilitada + más de un año
    sin rotar -> cuenta como no-compliant aunque CyberArk no la marque
    PolicyFailures (sin gestión activa, no hay política que incumplir)."""
    accounts = [
        _acc("1", status="success", auto=False, age_days=400),  # deshabilitada y vieja
        _acc("2", status="success", auto=False, age_days=30),  # deshabilitada pero reciente
        _acc("3", status="success", auto=False),  # deshabilitada, nunca rotada
        _acc("4", status="success", auto=True, age_days=400),  # vieja pero con gestión activa
    ]
    out = analysis.analyze_accounts(accounts)
    assert out["disabled_stale"] == 2  # sólo la 1 y la 3
    assert out["active_overdue"] == 1  # la 4 — caso real del PVWA (gestión activa, vencida)


def test_analyze_accounts_platform_days_reemplaza_el_default_del_tenant():
    """La plataforma de la cuenta exige rotar cada 10 días — mucho más
    estricto que el default parejo del tenant (30) — así que 15 días sin
    rotar ya cuenta como vencida, aunque no llegue a los 30."""
    accounts = [_acc("1", status="success", auto=False, age_days=15)]
    out = analysis.analyze_accounts(accounts, 30, {"WinDomain": 10})
    assert out["disabled_stale"] == 1


def test_analyze_accounts_ssh_key_nunca_cuenta_como_vencida():
    """Llaves SSH (secretType=key) no rotan por naturaleza -- no deben
    contar en disabled_stale ni active_overdue aunque nunca hayan rotado."""
    accounts = [
        _acc("1", status="success", auto=False, age_days=400, secret_type="key"),
        _acc("2", status="success", auto=True, age_days=400, secret_type="key"),
        _acc("3", status="success", auto=False, age_days=400),  # sin exención: cuenta
    ]
    out = analysis.analyze_accounts(accounts)
    assert out["disabled_stale"] == 1  # sólo la 3
    assert out["active_overdue"] == 0


def test_analyze_accounts_exempt_ids_excluye_disabled_stale():
    """Ver docs/specs/compliance-exemptions.md: cuenta root deshabilitada a
    pedido del cliente, con una excepción manual activa en PAM Web Console -- no debe
    restar del compliance aunque esté vencida."""
    accounts = [
        _acc("root1", status="success", auto=False, age_days=900),
        _acc("root2", status="success", auto=False, age_days=900),
    ]
    out = analysis.analyze_accounts(accounts, exempt_ids={"root1"})
    assert out["disabled_stale"] == 1  # sólo root2, sin exención


def test_compliance_breakdown_pct_exacto_sin_doble_conteo():
    """Antes (aproximación por suma de conteos) esta situación daba mal:
    una cuenta en `PolicyFailures` Y ADEMÁS deshabilitada/vencida se
    restaba dos veces del compliant. Ahora es una unión real, cuenta por
    cuenta — nunca resta de más."""
    accounts = [
        _acc("x", status="success", auto=False, age_days=400),  # PolicyFailures Y DisabledStale
        _acc("y", status="success", auto=True, age_days=5),  # compliant
    ]
    out = analysis.compliance_breakdown(
        accounts, policy_failure_ids={"x"}, filter_counts={}, default_stale_days=30
    )
    assert out["compliant"] == 1
    assert out["compliant_pct"] == 50.0
    assert out["categories"]["PolicyFailures"] == 1
    assert out["categories"]["DisabledStale"] == 1
    assert out["categories"]["ActiveOverdue"] == 0


def test_compliance_breakdown_scenario_96_pct():
    # escenario de docs/specs/done -> dashboard-v2: 1000 cuentas, 40 PolicyFailures,
    # el resto compliant (activas, recién rotadas)
    accounts = [_acc(str(i), status="success", auto=True, age_days=5) for i in range(1000)]
    out = analysis.compliance_breakdown(
        accounts,
        policy_failure_ids={str(i) for i in range(40)},
        filter_counts={"DisabledPasswordByUser": 5, "DisabledPasswordByCPM": 3, "Locked": 1},
        default_stale_days=30,
    )
    assert out["compliant"] == 960
    assert out["compliant_pct"] == 96.0
    assert out["categories"]["PolicyFailures"] == 40
    assert out["categories"]["DisableByUser"] == 5
    assert set(out["categories"]) == {
        "Compliant",
        "PolicyFailures",
        "DisabledStale",
        "ActiveOverdue",
        "DisableByUser",
        "DisableByCPM",
        "Locked",
    }


def test_compliance_breakdown_resta_tambien_disabled_stale():
    """Caso real de Initech: CyberArk sólo marca 5 PolicyFailures sobre 200
    cuentas (97.5% "compliant" nativo), pero 150 tienen la gestión
    deshabilitada hace más de un año -> compliance real bastante más bajo."""
    accounts = [_acc(str(i), status="success", auto=False, age_days=400) for i in range(150)]
    accounts += [_acc(f"c{i}", status="success", auto=True, age_days=5) for i in range(50)]
    out = analysis.compliance_breakdown(
        accounts,
        # 5 de las 50 "limpias" en realidad están en PolicyFailures — deben
        # restar además de las 150 disabled_stale, sin solaparse con ellas
        policy_failure_ids={f"c{i}" for i in range(5)},
        filter_counts={"DisabledPasswordByUser": 150, "Locked": 0},
        default_stale_days=30,
    )
    assert out["compliant"] == 45
    assert out["compliant_pct"] == 22.5
    assert out["categories"]["DisabledStale"] == 150


def test_compliance_breakdown_respeta_exempt_ids_y_ssh_keys():
    """Caso Globex: cuentas root deshabilitadas a pedido del cliente (con
    excepción manual) y una llave SSH no deben restar del % de compliance."""
    accounts = [
        _acc("root1", status="success", auto=False, age_days=900),  # exenta
        _acc("root2", status="success", auto=False, age_days=900),  # NO exenta
        _acc("key1", status="success", auto=False, age_days=900, secret_type="key"),
        _acc("ok", status="success", auto=True, age_days=5),
    ]
    out = analysis.compliance_breakdown(
        accounts,
        policy_failure_ids=set(),
        filter_counts={},
        default_stale_days=30,
        exempt_ids={"root1"},
    )
    assert out["compliant"] == 3  # root1 (exenta), key1 (ssh key) y ok
    assert out["categories"]["DisabledStale"] == 1  # sólo root2


def test_compliance_breakdown_sin_cuentas():
    out = analysis.compliance_breakdown([], policy_failure_ids=set(), filter_counts={})
    assert out == {"compliant": 0, "compliant_pct": 0.0, "categories": {}}


def test_compliance_rows_curva_columnas_sin_ageDays():
    accounts = [_acc("1", status="success"), {"id": "2", "secretManagement": {}}]
    rows = analysis.compliance_rows(accounts)
    assert len(rows) == 2
    assert rows[0]["id"] == "1"
    assert "ageDays" not in rows[0]


def test_disabled_stale_rows_mismo_criterio_que_analyze_accounts():
    accounts = [
        _acc("1", status="success", auto=False, age_days=400),
        _acc("2", status="success", auto=False, age_days=10),
        _acc("3", status="success", auto=True, age_days=400),
        _acc("4", status="success", auto=False),  # nunca rotada
    ]
    rows = analysis.disabled_stale_rows(accounts, 365)
    ids = {r["id"] for r in rows}
    assert ids == {"1", "4"}
    by_id = {r["id"]: r for r in rows}
    assert by_id["1"]["ageDays"] == 400
    assert by_id["4"]["ageDays"] is None


def test_active_overdue_rows_gestion_activa_vencida():
    accounts = [
        _acc("1", status="success", auto=True, age_days=400),
        _acc("2", status="success", auto=True, age_days=5),
        _acc("3", status="success", auto=False, age_days=400),
    ]
    rows = analysis.active_overdue_rows(accounts, 30)
    assert {r["id"] for r in rows} == {"1"}


def test_disabled_stale_rows_respeta_exempt_ids():
    accounts = [
        _acc("1", status="success", auto=False, age_days=400),
        _acc("2", status="success", auto=False, age_days=400),
    ]
    rows = analysis.disabled_stale_rows(accounts, 365, exempt_ids={"1"})
    assert {r["id"] for r in rows} == {"2"}


def test_account_compliance_exempt_ids_sin_reasons():
    acc = _acc("root1", status="success", auto=False, age_days=900)
    out = analysis.account_compliance(
        acc, is_policy_failure=False, default_stale_days=30, exempt_ids={"root1"}
    )
    assert out["compliant"] is True
    assert out["reasons"] == []


def test_account_compliance_reasons_combinadas():
    acc = _acc("1", status="success", auto=True, age_days=400)
    out = analysis.account_compliance(acc, is_policy_failure=True, default_stale_days=30)
    assert out["compliant"] is False
    assert set(out["reasons"]) == {"PolicyFailures", "ActiveOverdue"}
    assert out["age_days"] == 400
    assert out["threshold_days"] == 30


def test_account_compliance_ok():
    acc = _acc("1", status="success", auto=True, age_days=5)
    out = analysis.account_compliance(acc, is_policy_failure=False, default_stale_days=30)
    assert {k: out[k] for k in ("compliant", "age_days", "threshold_days", "reasons")} == {
        "compliant": True,
        "age_days": 5,
        "threshold_days": 30,
        "reasons": [],
    }
    assert out["last_changed_source"] == "account"
    assert out["cpm_failed"] is False


def test_analyze_cpm_errors_reintento_vs_desactivadas():
    accounts = [
        _acc("1", status="success"),  # no cuenta
        _acc("2", status="failure", auto=True),  # en reintento
        _acc("3", status="failure", auto=True),  # en reintento
        _acc("4", status="failure", auto=False, reason="(CPM)MaxRetries"),
        _acc("5", status="failure", auto=False, reason="solicitud del cliente"),  # excluido
    ]
    failed_ids = {"2", "3", "4", "5"}
    out = analysis.analyze_cpm_errors(accounts, failed_ids)

    assert out["failed_total"] == 4
    assert out["active_retry"] == 2
    assert out["given_up"] == 2
    assert out["by_reason"]["Reintentos agotados (CPM)"] == 1
    assert "solicitud del cliente" not in out["by_reason"]


def test_failed_accounts_rows_detalle_por_cuenta():
    accounts = [
        _acc("1", status="success"),  # no aparece
        _acc("2", status="failure", auto=True),  # reintentando, sin motivo
        _acc("3", status="failure", auto=False, reason="(CPM)MaxRetries"),
        _acc("4", status="failure", auto=False, reason="solicitud del cliente"),  # sin label
    ]
    failed_ids = {"2", "3", "4"}
    rows = {r["id"]: r for r in analysis.failed_accounts_rows(accounts, failed_ids)}

    assert set(rows) == {"2", "3", "4"}
    assert rows["2"]["retrying"] is True
    assert rows["2"]["reason"] is None
    assert rows["3"]["retrying"] is False
    assert rows["3"]["reason"] == "Reintentos agotados (CPM)"
    assert rows["4"]["retrying"] is False
    assert rows["4"]["reason"] is None  # motivo excluido, no técnico


def test_analyze_components_resumen():
    out = analysis.analyze_components(
        {
            "CPM": [{"IsLoggedOn": True}, {"IsLoggedOn": False}],
            "PVWA": [{"IsLoggedOn": True}],
            "AIM": None,  # no aplica
        }
    )
    assert out["CPM"] == {
        "available": True,
        "total_instances": 2,
        "connected": 1,
        "disconnected": 1,
    }
    assert out["PVWA"]["disconnected"] == 0
    assert out["AIM"] == {"available": False}


def test_analyze_sessions_cuenta_por_protocolo():
    out = analysis.analyze_sessions(
        [
            {"Protocol": "RDP"},
            {"Protocol": "RDP"},
            {"Protocol": "SSH"},
            {},  # sin Protocol -> "—"
        ]
    )
    assert out["available"] is True
    assert out["total"] == 4
    assert out["by_protocol"]["RDP"] == 2
    assert out["by_protocol"]["SSH"] == 1
    assert out["by_protocol"]["—"] == 1


def test_analyze_sessions_degrada_si_la_llamada_fallo():
    out = analysis.analyze_sessions(None)
    assert out == {"available": False, "total": 0, "by_protocol": Counter()}


def test_last_password_change_toma_la_mas_reciente_y_formatos():
    from app.analysis import last_password_change

    dt = last_password_change(
        [
            {"ModificationTime": 1700000000},
            {"ModificationTime": 1700000300000},  # milisegundos
            {"ModificationTime": "2023-11-14T22:10:00Z"},
            {"ModificationTime": None},
            "basura",
        ]
    )
    assert dt is not None and int(dt.timestamp()) == 1700000300
    assert last_password_change([]) is None
    assert last_password_change(None) is None
