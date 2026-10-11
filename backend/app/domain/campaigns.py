"""Lógica de dominio de campañas de rotación/remediación (Fase 4). Puro,
sin I/O. El estado de cada cuenta se calcula en vivo contra el estado
actual del tenant — PAM Web Console no persiste un historial. Ver
docs/specs/done/rotation-campaigns.md.
"""

from __future__ import annotations

from typing import Any


def campaign_progress(
    members: list[dict[str, Any]], accounts_by_id: dict[str, dict], failed_ids: set
) -> list[dict[str, Any]]:
    """`members` = snapshot de `CampaignAccount` (account_id/account_name/
    safe_name/platform_id). `accounts_by_id`/`failed_ids` = salida de
    `analysis.analyze_accounts` sobre el `list_accounts()` vivo del
    tenant. Devuelve cada miembro con su `status`: `resolved` (ya no está
    en Failure), `pending` (sigue en Failure) o `not_found` (la cuenta ya
    no existe en CyberArk)."""
    rows: list[dict[str, Any]] = []
    for m in members:
        account_id = m["account_id"]
        if account_id not in accounts_by_id:
            status = "not_found"
        elif account_id in failed_ids:
            status = "pending"
        else:
            status = "resolved"
        rows.append({**m, "status": status})
    return rows


def campaign_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    total = len(rows)
    resolved = sum(1 for r in rows if r["status"] == "resolved")
    not_found = sum(1 for r in rows if r["status"] == "not_found")
    return {
        "total": total,
        "resolved": resolved,
        "pending": total - resolved - not_found,
        "not_found": not_found,
    }
