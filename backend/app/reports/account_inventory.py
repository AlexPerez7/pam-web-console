"""Inventario detallado de cuentas: gestión automática, estado del CPM y
últimas fechas de cambio/verificación/reconciliación.

Portado de `Account_Inventory_Export.ps1` — pero sin el `get_account`
por cuenta: las fechas de `secretManagement` ya vienen en el listado.

`account-inventory-full` (más abajo, `heavy=True`) suma último acceso y
falla de cambio/verificación con su motivo real -- comparado el
2026-09-24 contra el reporte nativo "Privileged Accounts Inventory" del
PVWA (`InventoryReports.InventoryReportUI`), que sí trae esos campos
(y "Group name", que este reporte NO reproduce -- sin resolver de dónde
sale, no parece venir de `Activities`). El nativo los arma con datos que
el Vault trackea internamente; acá se reconstruyen con el mismo fan-out
de `Activities` por cuenta que ya usan `target_inactivity.py`/
`account_activity.py` (mismo costo, por eso es un reporte `heavy`
aparte y no el `account-inventory` liviano de siempre).
"""

from __future__ import annotations

import re
from typing import Any

from app.cyberark import CyberArkClient
from app.reports._activities import all_account_activities
from app.reports.base import Column, Report, ReportResult

_COLUMNS = [
    Column("id", "Account ID"),
    Column("name", "Nombre"),
    Column("userName", "Usuario"),
    Column("address", "Address"),
    Column("platformId", "Plataforma"),
    Column("safeName", "Safe"),
    Column("secretType", "Tipo"),
    Column("autoManaged", "Gestión automática", "bool"),
    Column("cpmStatus", "Estado CPM"),
    Column("manualReason", "Motivo (si manual)"),
    Column("lastModified", "Último cambio", "date"),
    Column("lastVerified", "Última verificación", "date"),
    Column("lastReconciled", "Última reconciliación", "date"),
    Column("createdTime", "Creada", "date"),
]


def compute_account_inventory(accounts: list[dict]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for a in accounts:
        sm = a.get("secretManagement") or {}
        rows.append(
            {
                "id": a.get("id"),
                "name": a.get("name"),
                "userName": a.get("userName"),
                "address": a.get("address"),
                "platformId": a.get("platformId"),
                "safeName": a.get("safeName"),
                "secretType": a.get("secretType"),
                "autoManaged": bool(sm.get("automaticManagementEnabled")),
                "cpmStatus": sm.get("status"),
                "manualReason": sm.get("manualManagementReason"),
                "lastModified": sm.get("lastModifiedTime") or sm.get("lastModifiedDateTime"),
                "lastVerified": sm.get("lastVerifiedTime") or sm.get("lastVerifiedDateTime"),
                "lastReconciled": sm.get("lastReconciledTime"),
                "createdTime": a.get("createdTime"),
            }
        )
    return rows


async def _run(client: CyberArkClient, _params: dict[str, Any]) -> ReportResult:
    accounts = await client.list_accounts()
    rows = compute_account_inventory(accounts)
    managed = sum(1 for r in rows if r["autoManaged"])
    failed = sum(1 for r in rows if str(r["cpmStatus"] or "").lower() in ("failure", "failed"))
    return ReportResult(
        columns=_COLUMNS,
        rows=rows,
        meta={"total": len(rows), "gestion_automatica": managed, "en_falla": failed},
    )


report = Report(
    name="account-inventory",
    category="Cuentas",
    label="Inventario detallado de cuentas",
    description=(
        "Todas las cuentas del tenant con estado de gestión del CPM y últimas "
        "fechas de cambio / verificación / reconciliación."
    ),
    run=_run,
)


# --- account-inventory-full: suma último acceso + falla real de cambio/ --- #
# --- verificación, vía el mismo fan-out de Activities que Batch 4 ------- #

_FAIL_RE = re.compile(r"fail", re.IGNORECASE)
_CONNECT_RE = re.compile(r"PSM Connect|^Connect$")
_RETRIEVAL_RE = re.compile(r"Show Password|Copy Password")


def _classify_failure_kind(action: str) -> str | None:
    """Mismo criterio que `frontend/src/lib/activityClassify.ts` `classify()`
    -- no se reusa esa lógica (TS, no Python), pero es intencionalmente
    idéntica para que "falla de cambio" signifique lo mismo en toda la app."""
    a = action.lower()
    if "verif" in a:
        return "verify"
    if "reconcil" in a:
        return "reconcile"
    if "change" in a or "cambio" in a:
        return "change"
    return None


_FULL_COLUMNS = [
    *_COLUMNS,
    Column("lastAccess", "Último acceso", "date"),
    Column("lastAccessBy", "Último acceso por"),
    Column("changeFailure", "Falla de cambio", "bool"),
    Column("verificationFailure", "Falla de verificación", "bool"),
    Column("failureReason", "Motivo de la falla"),
]


def compute_account_inventory_full(
    accounts: list[dict[str, Any]], activities_by_id: dict[str, list | None]
) -> list[dict[str, Any]]:
    rows = compute_account_inventory(accounts)
    for row in rows:
        acc_id = str(row["id"] or "")
        acts = activities_by_id.get(acc_id) or []

        last_connect: int | None = None
        last_connect_by: str | None = None
        last_retrieval: int | None = None
        last_retrieval_by: str | None = None
        for a in acts:
            date = a.get("Date")
            if not date:
                continue
            action = str(a.get("Action") or "")
            if _CONNECT_RE.search(action) and (last_connect is None or date > last_connect):
                last_connect, last_connect_by = int(date), a.get("User")
            elif _RETRIEVAL_RE.search(action) and (last_retrieval is None or date > last_retrieval):
                last_retrieval, last_retrieval_by = int(date), a.get("User")

        last_access: int | None
        last_access_by: str | None
        if last_connect is not None and (last_retrieval is None or last_connect >= last_retrieval):
            last_access, last_access_by = last_connect, last_connect_by
        else:
            last_access, last_access_by = last_retrieval, last_retrieval_by

        failure = last_cpm_failure(acts)
        kind = failure["kind"] if failure else None

        row["lastAccess"] = last_access
        row["lastAccessBy"] = last_access_by
        row["changeFailure"] = kind == "change"
        row["verificationFailure"] = kind == "verify"
        row["failureReason"] = failure["reason"] if failure else None
    return rows


def last_cpm_failure(acts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """La falla del CPM más reciente de una cuenta -- `{"kind", "reason",
    "date"}` (`kind` = change/verify/reconcile) o `None` si no hay ninguna.
    La comparten `account-inventory-full` y `non-rotating-accounts`.

    La falla más reciente con "fail" en el Action Y clasificable como
    change/verify/reconcile determina el tipo. No alcanza con "fail" en el
    Action: Globex tiene MUCHAS actividades de fallas de sesión PSM
    (PSMSR280E/605E/etc, componentes que se cuelgan, timeouts) que también
    dicen "fail" y no son del CPM para nada -- confirmado con datos reales
    (2026-09-24, 429 de 1892 cuentas tenían una falla de sesión PSM más
    reciente que tapaba el motivo real). Se sigue buscando hacia atrás
    hasta encontrar una que sí sea del CPM, en vez de quedarse con la
    primera que diga "fail"."""
    fails = sorted(
        (a for a in acts if a.get("Date") and _FAIL_RE.search(str(a.get("Action") or ""))),
        key=lambda a: a["Date"],
        reverse=True,
    )
    for a in fails:
        kind = _classify_failure_kind(str(a.get("Action") or ""))
        if kind is None:
            continue
        return {"kind": kind, "reason": a.get("Reason") or a.get("Action"), "date": a.get("Date")}
    return None


async def _run_full(client: CyberArkClient, _params: dict[str, Any]) -> ReportResult:
    accounts = await client.list_accounts()
    activities = await all_account_activities(client, accounts)
    rows = compute_account_inventory_full(accounts, activities)
    errors = sum(1 for v in activities.values() if v is None)
    return ReportResult(
        columns=_FULL_COLUMNS,
        rows=rows,
        meta={"total": len(rows), "cuentas_con_error": errors},
    )


report_full = Report(
    name="account-inventory-full",
    category="Cuentas",
    label="Inventario de cuentas (completo)",
    description=(
        "Igual al Inventario detallado, más último acceso y el motivo real de la "
        "última falla de cambio o verificación."
    ),
    run=_run_full,
    heavy=True,
)
