"""Cuentas con problemas de rotación, agrupadas por tipo de sistema, para
entregar al cliente como Excel (Portada con el % por sistema + una pestaña
por sistema). Ver `docs/specs/done/rotation-failures-by-system.md`.

Pedido de un cliente (2026-10-07). "Con problemas" = error del CPM,
deshabilitada por el CPM tras fallar, o gestionada pero vencida contra la
política de su plataforma (decisión de Alex: errores + vencidas). Una
cuenta deshabilitada a mano por el cliente cuenta en el total de su sistema
pero nunca como problema.

El tipo de sistema es `general.systemType` de la plataforma de la cuenta
(la misma clasificación que usa el ITR). Las cuentas internas de CyberArk y
las llaves SSH quedan fuera del numerador Y del denominador: no son
credenciales del cliente que deban rotar.

Reutiliza los criterios de `non_rotating_accounts` (CPM caído, cuentas
internas, último error real desde `Activities`) y el de "vencida" de
`analysis.account_compliance`, así los números coinciden con el Dashboard.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app import analysis
from app.cyberark import CyberArkClient
from app.reports._activities import all_account_activities
from app.reports.base import Column, Param, Report, ReportResult
from app.reports.non_rotating_accounts import (
    attach_last_errors,
    is_cpm_disabled,
    is_cpm_failure,
    is_cyberark_internal,
    needs_last_error,
)

UNCLASSIFIED = "Sin clasificar"

# problema (código estable) -> etiqueta legible
PROBLEM_LABELS: dict[str, str] = {
    "cpm_failure": "Error del CPM",
    "cpm_disabled": "Deshabilitada por el CPM",
    "overdue": "Vencida (sin error)",
}

COLUMNS = [
    Column("systemType", "Sistema"),
    Column("id", "Account ID"),
    Column("name", "Nombre"),
    Column("userName", "Usuario"),
    Column("address", "Address"),
    Column("platformId", "Plataforma"),
    Column("safeName", "Safe"),
    Column("problem", "Problema"),
    Column("cpmStatus", "Estado CPM"),
    Column("autoManaged", "Gestión automática", "bool"),
    Column("manualReason", "Motivo (si manual)"),
    Column("lastModified", "Último cambio", "date"),
    Column("ageDays", "Días sin rotar", "number"),
    Column("thresholdDays", "Umbral (días)", "number"),
    Column("lastErrorKind", "Tipo de la última falla"),
    Column("lastErrorDate", "Fecha de la última falla", "date"),
    Column("lastErrorReason", "Motivo del último error"),
]


def platform_system_types(platforms: list[dict]) -> dict[str, str]:
    """`platform_id -> systemType` (vacío -> "Sin clasificar")."""
    out: dict[str, str] = {}
    for p in platforms:
        general = p.get("general") or {}
        pid = general.get("id")
        if pid:
            out[str(pid)] = str(general.get("systemType") or "").strip() or UNCLASSIFIED
    return out


def compute_by_system(
    accounts: list[dict],
    *,
    system_types: dict[str, str],
    platform_days: dict[str, int],
    cpm_disabled_ids: set,
    default_stale_days: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Filas de las cuentas con problemas (con `systemType`) + meta con el
    resumen por sistema: `by_system` = lista ordenada por cantidad de
    problemas (desc) de `{system, total, cpm_errors, overdue, failing,
    pct}`, más el total general y lo excluido."""
    rows: list[dict[str, Any]] = []
    summary: dict[str, dict[str, int]] = {}
    excluded_internal = excluded_ssh = 0

    for acc in accounts:
        if is_cyberark_internal(acc):
            excluded_internal += 1
            continue
        if str(acc.get("secretType") or "").lower() == "key":
            excluded_ssh += 1
            continue

        acc_id = acc.get("id")
        sm = acc.get("secretManagement") or {}
        system = system_types.get(str(acc.get("platformId") or ""), UNCLASSIFIED)
        bucket = summary.setdefault(system, {"total": 0, "cpm_errors": 0, "overdue": 0})
        bucket["total"] += 1

        status = analysis.account_compliance(
            acc,
            is_policy_failure=False,
            default_stale_days=default_stale_days,
            platform_days=platform_days,
        )
        if is_cpm_failure(sm):
            problem, codes = "cpm_failure", ["CpmFailure"]
        elif is_cpm_disabled(acc_id, sm, cpm_disabled_ids):
            problem, codes = "cpm_disabled", ["DisableByCPM"]
        elif "ActiveOverdue" in status["reasons"]:
            problem, codes = "overdue", ["ActiveOverdue"]
        else:
            continue  # rota bien, o la deshabilitó el cliente a propósito

        bucket["overdue" if problem == "overdue" else "cpm_errors"] += 1
        rows.append(
            {
                "systemType": system,
                "id": acc_id,
                "name": acc.get("name"),
                "userName": acc.get("userName"),
                "address": acc.get("address"),
                "platformId": acc.get("platformId"),
                "safeName": acc.get("safeName"),
                "problem": PROBLEM_LABELS[problem],
                "reasonCodes": codes,
                "cpmStatus": sm.get("status"),
                "autoManaged": bool(sm.get("automaticManagementEnabled")),
                "manualReason": sm.get("manualManagementReason"),
                "lastModified": sm.get("lastModifiedTime") or sm.get("lastModifiedDateTime"),
                "ageDays": status["age_days"],
                "thresholdDays": status["threshold_days"],
                "lastErrorKind": None,
                "lastErrorDate": None,
                "lastErrorReason": None,
            }
        )

    by_system: list[dict[str, Any]] = []
    for system, b in summary.items():
        failing = b["cpm_errors"] + b["overdue"]
        by_system.append(
            {"system": system, **b, "failing": failing, "pct": _pct(failing, b["total"])}
        )
    by_system.sort(key=lambda s: (-s["failing"], -s["total"], s["system"]))

    total = sum(s["total"] for s in by_system)
    failing = len(rows)
    order = {s["system"]: i for i, s in enumerate(by_system)}
    rows.sort(key=lambda r: (order[r["systemType"]], r["problem"], str(r["name"] or "")))
    meta: dict[str, Any] = {
        "cuentas_analizadas": len(accounts),
        "cuentas_consideradas": total,
        "con_problemas": failing,
        "porcentaje": _pct(failing, total),
        "excluidas_internas_cyberark": excluded_internal,
        "excluidas_llaves_ssh": excluded_ssh,
        "by_system": by_system,
    }
    return rows, meta


def _pct(part: int, total: int) -> float:
    return round(part * 100 / total, 1) if total else 0.0


async def _run(client: CyberArkClient, params: dict[str, Any]) -> ReportResult:
    with_last_error = str(params.get("incluir_ultimo_error") or "Sí") != "No"
    default_stale_days = int(
        params.get("_default_stale_days") or analysis.DEFAULT_DISABLED_STALE_DAYS
    )
    accounts, platforms, cpm_disabled = await asyncio.gather(
        client.list_accounts(),
        client.list_platforms(),
        client.saved_filter_accounts("DisabledPasswordByCPM"),
    )
    rows, meta = compute_by_system(
        accounts,
        system_types=platform_system_types(platforms),
        platform_days=analysis.platform_policy_days(platforms),
        cpm_disabled_ids={a.get("id") for a in cpm_disabled},
        default_stale_days=default_stale_days,
    )

    if with_last_error:
        to_check = [r for r in rows if needs_last_error(r)]
        activities = await all_account_activities(client, to_check)
        meta["ultimo_error_consultado"] = len(to_check)
        errors = attach_last_errors(rows, activities)
        if errors:
            meta["ultimo_error_sin_datos"] = errors

    for r in rows:
        r.pop("reasonCodes", None)
    return ReportResult(columns=COLUMNS, rows=rows, meta=meta)


def _xlsx(data: dict[str, Any], tenant_name: str) -> bytes:
    # import diferido: openpyxl sólo hace falta al exportar
    from app.reports.xlsx import build_rotation_failures_xlsx

    return build_rotation_failures_xlsx(data, tenant_name)


report = Report(
    name="rotation-failures-by-system",
    category="Cuentas",
    label="Problemas de rotación por sistema",
    description=(
        "Cuentas con error del CPM o vencidas contra la política de su plataforma, agrupadas "
        "por tipo de sistema (Windows, Unix, bases de datos...). Se exporta a Excel con una "
        "portada con el % de cuentas con problemas de cada sistema y una pestaña por sistema."
    ),
    run=_run,
    heavy=True,
    params=[
        Param(
            "incluir_ultimo_error",
            "Motivo del último error",
            "select",
            options=["Sí", "No"],
            help="Sí (por defecto)",
        ),
    ],
    tenant_params=lambda tenant: {"_default_stale_days": tenant.compliance_stale_days},
    xlsx=_xlsx,
)
