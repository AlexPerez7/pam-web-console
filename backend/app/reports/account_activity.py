"""Cantidad de conexiones PSM por cuenta en los últimos 30 días -- Batch 4
de `docs/specs/done/reportes.md`. Portado de
`Account_Activity_Report.ps1`.

`heavy=True`: necesita un `GET /Accounts/{id}/Activities` por cuenta (ver
`app.reports._activities`), corre como job ARQ (`app.jobs.reports`), no
síncrono en un request HTTP.
"""

from __future__ import annotations

import time
from typing import Any

from app.cyberark import CyberArkClient
from app.reports._activities import all_account_activities
from app.reports.base import Column, Report, ReportResult

_WINDOW_DAYS = 30

_COLUMNS = [
    Column("accountId", "ID"),
    Column("accountName", "Nombre de cuenta"),
    Column("safeName", "Safe"),
    Column("address", "Address"),
    Column("userName", "Usuario"),
    Column("platformId", "Plataforma"),
    Column("sessions30d", "Sesiones PSM (30 días)", "number"),
]


def compute_account_activity(
    accounts: list[dict[str, Any]],
    activities_by_id: dict[str, list | None],
    *,
    now_epoch: int | None = None,
) -> list[dict[str, Any]]:
    now = now_epoch if now_epoch is not None else int(time.time())
    cutoff = now - _WINDOW_DAYS * 86400
    rows: list[dict[str, Any]] = []

    for acc in accounts:
        acc_id = str(acc.get("id") or "")
        acts = activities_by_id.get(acc_id) or []
        count = sum(
            1
            for a in acts
            if a.get("Date")
            and int(a["Date"]) >= cutoff
            and "PSM Connect" in str(a.get("Action") or "")
        )
        rows.append(
            {
                "accountId": acc_id,
                "accountName": acc.get("name"),
                "safeName": acc.get("safeName"),
                "address": acc.get("address"),
                "userName": acc.get("userName"),
                "platformId": acc.get("platformId"),
                "sessions30d": count,
            }
        )

    rows.sort(key=lambda r: -r["sessions30d"])
    return rows


async def _run(client: CyberArkClient, _params: dict[str, Any]) -> ReportResult:
    accounts = await client.list_accounts()
    activities = await all_account_activities(client, accounts)
    rows = compute_account_activity(accounts, activities)
    errors = sum(1 for v in activities.values() if v is None)
    meta = {
        "cuentas_total": len(accounts),
        "ventana_dias": _WINDOW_DAYS,
        "cuentas_con_error": errors,
    }
    return ReportResult(columns=_COLUMNS, rows=rows, meta=meta)


report = Report(
    name="account-activity",
    category="Cuentas",
    label="Actividad de cuentas (30 días)",
    description="Cantidad de conexiones PSM por cuenta en los últimos 30 días.",
    run=_run,
    heavy=True,
)
