"""Reparto de cuentas entre los PSM configurados en el tenant.

Portado de `PSM_Balance_Report.ps1` (la parte de solo lectura: el
diagnóstico "cómo están repartidas las cuentas reales por PSM"). El rebalanceo
sugerido y el CSV para la migración quedan fuera de v1.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from app.cyberark import CyberArkClient, CyberArkError
from app.reports.base import Column, Report, ReportResult

_COLUMNS = [
    Column("platformId", "Plataforma"),
    Column("platformName", "Nombre"),
    Column("active", "Activa", "bool"),
    Column("psmServerId", "PSM ID"),
    Column("psmServerName", "PSM"),
    Column("accounts", "Cuentas", "number"),
]


def _platform_psm(p: dict) -> tuple[str | None, str | None]:
    for section in ("sessionManagement", "privilegedSessionManagement"):
        sm = p.get(section) or {}
        pid = sm.get("psmServerId") or sm.get("PSMServerID") or sm.get("psmServerID")
        pname = sm.get("psmServerName") or sm.get("PSMServerName")
        if pid or pname:
            return pid, pname
    return None, None


def compute_psm_balance(
    platforms: list[dict], accounts: list[dict], psm_servers: list[dict]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    psm_name = {
        (s.get("ID") or s.get("id")): (s.get("Name") or s.get("name"))
        for s in psm_servers
        if s.get("ID") or s.get("id")
    }
    by_platform = Counter(a.get("platformId") or "" for a in accounts)

    rows: list[dict[str, Any]] = []
    per_psm: Counter = Counter()
    per_psm_platforms: Counter = Counter()
    for p in platforms:
        g = p.get("general") or {}
        pid = g.get("id") or ""
        psm_id, psm_name_raw = _platform_psm(p)
        name = psm_name.get(psm_id) or psm_name_raw or (psm_id or "— sin PSM —")
        n = by_platform.get(pid, 0)
        rows.append(
            {
                "platformId": pid,
                "platformName": g.get("name") or pid,
                "active": bool(g.get("active")),
                "psmServerId": psm_id,
                "psmServerName": name,
                "accounts": n,
            }
        )
        per_psm[name] += n
        per_psm_platforms[name] += 1

    rows.sort(key=lambda r: (str(r["psmServerName"]), -r["accounts"]))
    total = sum(per_psm.values()) or 1
    meta = {
        "cuentas_total": sum(per_psm.values()),
        "psm_configurados": len(psm_servers),
        **{
            f"{name}": f"{cnt} cuentas ({round(cnt / total * 100)}%) · {per_psm_platforms[name]} plataformas"
            for name, cnt in per_psm.most_common()
        },
    }
    return rows, meta


async def _run(client: CyberArkClient, _params: dict[str, Any]) -> ReportResult:
    platforms = await client.list_platforms()
    accounts = await client.list_accounts()
    try:
        psm_servers = await client.list_psm_servers()
    except CyberArkError:  # el endpoint puede no estar disponible en el tenant
        psm_servers = []
    rows, meta = compute_psm_balance(platforms, accounts, psm_servers)
    return ReportResult(columns=_COLUMNS, rows=rows, meta=meta)


report = Report(
    name="psm-balance",
    category="Infraestructura",
    label="Reparto de cuentas por PSM",
    description=(
        "Cuántas cuentas reales corren por cada PSM del tenant (según el PSM de la "
        "plataforma de cada cuenta). Base para balancear carga entre PSM."
    ),
    run=_run,
)
