"""Cuentas target sin ningún acceso hace N días -- Batch 4 de
`docs/specs/done/reportes.md`. Portado de
`Target_Inactivity_Report.ps1`: "acceso" es tanto conectar vía PSM
(Action "PSM Connect"/"Connect") como retirar la credencial sin pasar por
PSM (Action "Show Password"/"Copy Password") -- los retiros manuales sólo
viven en el Activities de cada cuenta, no hay un endpoint central que dé
el último acceso de todas las cuentas de una.

`heavy=True`: necesita un `GET /Accounts/{id}/Activities` por cuenta (ver
`app.reports._activities`), corre como job ARQ (`app.jobs.reports`), no
síncrono en un request HTTP.
"""

from __future__ import annotations

import re
import time
from typing import Any

from app.cyberark import CyberArkClient
from app.reports._activities import all_account_activities
from app.reports.base import Column, Param, Report, ReportResult

_CONNECT_RE = re.compile(r"PSM Connect|^Connect$")
_RETRIEVAL_RE = re.compile(r"Show Password|Copy Password")

_DEFAULT_INACTIVE_DAYS = 180

_COLUMNS = [
    Column("accountId", "ID"),
    Column("accountName", "Nombre de cuenta"),
    Column("safeName", "Safe"),
    Column("address", "Address"),
    Column("userName", "Usuario"),
    Column("platformId", "Plataforma"),
    Column("lastAccess", "Último acceso", "date"),
    Column("daysSinceAccess", "Días sin acceso", "number"),
    Column("lastConnect", "Último PSM Connect", "date"),
    Column("lastRetrieval", "Último retiro de credencial", "date"),
    Column("accessType", "Tipo de último acceso"),
]


def compute_target_inactivity(
    accounts: list[dict[str, Any]],
    activities_by_id: dict[str, list | None],
    *,
    inactive_days: int = _DEFAULT_INACTIVE_DAYS,
    now_epoch: int | None = None,
) -> list[dict[str, Any]]:
    now = now_epoch if now_epoch is not None else int(time.time())
    rows: list[dict[str, Any]] = []

    for acc in accounts:
        acc_id = str(acc.get("id") or "")
        acts = activities_by_id.get(acc_id) or []

        connect_dates = [
            int(a["Date"])
            for a in acts
            if a.get("Date") and _CONNECT_RE.search(str(a.get("Action") or ""))
        ]
        retrieval_dates = [
            int(a["Date"])
            for a in acts
            if a.get("Date") and _RETRIEVAL_RE.search(str(a.get("Action") or ""))
        ]
        last_connect = max(connect_dates, default=None)
        last_retrieval = max(retrieval_dates, default=None)
        last_access = max(
            (d for d in (last_connect, last_retrieval) if d is not None), default=None
        )
        days_since = (now - last_access) // 86400 if last_access is not None else None

        is_inactive = days_since is None or days_since >= inactive_days
        if not is_inactive:
            continue

        if last_connect and last_retrieval:
            access_type = "PSM Connect y retiro de credencial (Show/Copy Password)"
        elif last_connect:
            access_type = "Solo PSM Connect"
        elif last_retrieval:
            access_type = "Solo retiro de credencial (Show/Copy Password), sin PSM Connect"
        else:
            access_type = "Sin actividad registrada"

        rows.append(
            {
                "accountId": acc_id,
                "accountName": acc.get("name"),
                "safeName": acc.get("safeName"),
                "address": acc.get("address"),
                "userName": acc.get("userName"),
                "platformId": acc.get("platformId"),
                "lastAccess": last_access,
                "daysSinceAccess": days_since,
                "lastConnect": last_connect,
                "lastRetrieval": last_retrieval,
                "accessType": access_type,
            }
        )

    # cuentas nunca accedidas primero (las más críticas), después de mayor a
    # menor antigüedad -- mismo criterio que el script original.
    rows.sort(
        key=lambda r: -(r["daysSinceAccess"] if r["daysSinceAccess"] is not None else float("inf"))
    )
    return rows


async def _run(client: CyberArkClient, params: dict[str, Any]) -> ReportResult:
    inactive_days = int(params.get("inactive_days") or _DEFAULT_INACTIVE_DAYS)
    accounts = await client.list_accounts()
    activities = await all_account_activities(client, accounts)
    rows = compute_target_inactivity(accounts, activities, inactive_days=inactive_days)
    errors = sum(1 for v in activities.values() if v is None)
    meta = {
        "cuentas_total": len(accounts),
        "cuentas_inactivas": len(rows),
        "umbral_dias": inactive_days,
        "cuentas_con_error": errors,
    }
    return ReportResult(columns=_COLUMNS, rows=rows, meta=meta)


report = Report(
    name="target-inactivity",
    category="Cuentas",
    label="Targets inactivos",
    description=(
        "Cuentas sin ningún acceso (PSM Connect o retiro manual de credencial) "
        "hace más de N días, o nunca accedidas."
    ),
    params=[
        Param(
            key="inactive_days",
            label="Días de inactividad",
            type="number",
            help=f"default {_DEFAULT_INACTIVE_DAYS}",
        )
    ],
    run=_run,
    heavy=True,
)
