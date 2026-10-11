"""Cuentas con `address` duplicado dentro de una misma safe.

Portado de `Safe_Duplicate_Report.ps1`. Sólo lee: emite cada cuenta de
cada grupo con ≥ 2 cuentas para que el cliente decida caso a caso; marca la
más antigua del grupo como sugerencia.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.cyberark import CyberArkClient
from app.reports.base import Column, Param, Report, ReportResult

_COLUMNS = [
    Column("safeName", "Safe"),
    Column("grupo", "Grupo", "number"),
    Column("cuentasEnGrupo", "Cuentas en el grupo", "number"),
    Column("address", "Address"),
    Column("accountId", "Account ID"),
    Column("userName", "Usuario"),
    Column("platformId", "Plataforma"),
    Column("createdTime", "Creada", "date"),
    Column("masAntiguaDelGrupo", "Más antigua del grupo", "bool"),
]


def compute_safe_duplicates(accounts: list[dict]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for a in accounts:
        safe = (a.get("safeName") or "").strip()
        addr = (a.get("address") or "").strip().lower()
        if not safe or not addr:
            continue
        groups[(safe, addr)].append(a)

    rows: list[dict[str, Any]] = []
    n = 0
    for (safe, _addr), accs in sorted(groups.items()):
        if len(accs) < 2:
            continue
        n += 1
        oldest = min(accs, key=lambda x: x.get("createdTime") or float("inf"))
        for a in sorted(accs, key=lambda x: x.get("createdTime") or 0):
            rows.append(
                {
                    "safeName": safe,
                    "grupo": n,
                    "cuentasEnGrupo": len(accs),
                    "address": a.get("address"),
                    "accountId": a.get("id"),
                    "userName": a.get("userName"),
                    "platformId": a.get("platformId"),
                    "createdTime": a.get("createdTime"),
                    "masAntiguaDelGrupo": a.get("id") == oldest.get("id"),
                }
            )
    return rows


async def _run(client: CyberArkClient, params: dict[str, Any]) -> ReportResult:
    safe_name = (params.get("safe_name") or "").strip() or None
    if safe_name:
        accounts, _ = await client.accounts_page(safe_name=safe_name, offset=0, limit=1000)
        # la primera página puede no traer todo; para el reporte traemos todo
        got = list(accounts)
        offset = len(got)
        while len(accounts) == 1000:
            accounts, _ = await client.accounts_page(safe_name=safe_name, offset=offset, limit=1000)
            got.extend(accounts)
            offset += 1000
        accounts = got
    else:
        accounts = await client.list_accounts()

    rows = compute_safe_duplicates(accounts)
    safes_afectadas = len({r["safeName"] for r in rows})
    return ReportResult(
        columns=_COLUMNS,
        rows=rows,
        meta={"accounts_scanned": len(accounts), "safes_con_duplicados": safes_afectadas},
    )


report = Report(
    name="safe-duplicates",
    category="Safes",
    label="Safes con cuentas duplicadas",
    description=(
        "Cuentas con el mismo address dentro de una safe (posible carga duplicada). "
        "No borra nada; marca la más antigua de cada grupo como sugerencia."
    ),
    run=_run,
    params=[
        Param(
            "safe_name",
            "Safe",
            "text",
            required=False,
            help="opcional — sin valor, escanea todas las safes del tenant",
        )
    ],
)
