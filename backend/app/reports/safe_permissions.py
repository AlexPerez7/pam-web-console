"""Auditoría de permisos: cada miembro de cada safe con su nivel de permiso.

Portado de `Safe_Permissions_Audit.ps1`. Fan-out acotado (un
`safe_members` por safe, en paralelo, limitado por el semáforo del cliente).
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.cyberark import CyberArkClient, CyberArkError
from app.domain.safe_permissions import PERMISSION_KEYS as _PERM_KEYS
from app.reports.base import Column, Param, Report, ReportResult

_COLUMNS = [
    Column("safeName", "Safe"),
    Column("memberName", "Miembro"),
    Column("memberType", "Tipo"),
    Column("membershipExpirationDate", "Vence", "date"),
    Column("isPredefinedUser", "Predefinido", "bool"),
    *[Column(k, label, "bool") for k, label in _PERM_KEYS],
]


def compute_safe_permissions(by_safe: dict[str, list[dict]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for safe_name, members in sorted(by_safe.items()):
        for m in members:
            perms = m.get("permissions") or {}
            row: dict[str, Any] = {
                "safeName": safe_name,
                "memberName": m.get("memberName"),
                "memberType": m.get("memberType"),
                "membershipExpirationDate": m.get("membershipExpirationDate"),
                "isPredefinedUser": bool(m.get("isPredefinedUser")),
            }
            for k, _ in _PERM_KEYS:
                row[k] = bool(perms.get(k))
            rows.append(row)
    return rows


async def _run(client: CyberArkClient, params: dict[str, Any]) -> ReportResult:
    safe_name = (params.get("safe_name") or "").strip() or None
    safes = await client.list_safes()
    if safe_name:
        safes = [s for s in safes if s.get("safeName") == safe_name]

    async def _members(safe: dict) -> tuple[str, list[dict]]:
        sid = safe.get("safeUrlId") or safe.get("safeName") or ""
        try:
            return safe.get("safeName") or sid, await client.safe_members(sid)
        except CyberArkError:
            return safe.get("safeName") or sid, []

    pairs = await asyncio.gather(*(_members(s) for s in safes))
    by_safe: dict[str, list[dict]] = {}
    for name, members in pairs:
        by_safe.setdefault(name, []).extend(members)
    rows = compute_safe_permissions(by_safe)
    return ReportResult(
        columns=_COLUMNS,
        rows=rows,
        meta={"safes": len(safes), "asignaciones": len(rows)},
    )


report = Report(
    name="safe-permissions",
    category="Safes",
    label="Auditoría de permisos de safes",
    description="Cada miembro de cada safe del tenant con su nivel de permiso, plano para revisar.",
    run=_run,
    params=[Param("safe_name", "Safe", "text", required=False, help="opcional — sin valor, todas")],
)
