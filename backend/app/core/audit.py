"""
Log de auditoría append-only con hash encadenado (docs/PLAN.md §5.4).

Cada entrada encadena su hash con el de la anterior: `hash = sha256(payload
canónico + prev_hash)`. Cualquier modificación o borrado de una fila rompe
la cadena a partir de ahí y `verify_chain()` lo detecta. Además la tabla
tiene un trigger de Postgres que bloquea UPDATE/DELETE (migración 0002).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import current_request_id
from app.models import AuditLog

# Advisory lock arbitrario y fijo, dedicado a serializar record() en
# Postgres. Sin él, dos requests concurrentes del mismo operador (ej. una
# pantalla que dispara account.get + account.activities en paralelo) pueden
# leer el mismo último hash antes de que cualquiera inserte, y bifurcar la
# cadena (visto en producción: audit_log ids 446/447, 2026-09-14, mismo
# prev_hash). No aplica en SQLite (tests unit) — ahí no hay concurrencia real.
_CHAIN_LOCK_KEY = 72617564  # "raud" en decimal, sin significado más allá de ser fijo


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _payload(row_like: dict, prev_hash: str | None) -> dict:
    return {
        "ts": row_like["ts"].isoformat(),
        "operator": row_like["operator"],
        "role": row_like["role"],
        "tenant_id": row_like["tenant_id"],
        "action": row_like["action"],
        "target": row_like["target"],
        "detail": row_like["detail"],
        "dry_run": row_like["dry_run"],
        "result": row_like["result"],
        "request_id": row_like["request_id"],
        "source_ip": row_like["source_ip"],
        "prev_hash": prev_hash,
    }


async def record(
    session: AsyncSession,
    *,
    operator: str,
    role: str,
    action: str,
    result: str,
    tenant_id: str | None = None,
    target: str | None = None,
    detail: dict | None = None,
    dry_run: bool = False,
    request_id: str | None = None,
    source_ip: str | None = None,
    ts: datetime | None = None,
) -> AuditLog:
    """
    `ts` es opcional y por defecto `now()` — pensado para el único caso donde
    hace falta forzarlo: re-importar entradas del standby en un fail-back
    (`manage.py import-standby-audit`), donde se preserva el momento real del evento aunque la fila se
    inserte después, al final de la cadena de la primaria.
    """
    if request_id is None:
        request_id = current_request_id()

    if session.bind is not None and session.bind.dialect.name == "postgresql":
        # Se libera solo al terminar la transacción (COMMIT/ROLLBACK) del
        # caller — mientras tanto, cualquier otro record() concurrente
        # espera acá antes de leer el último hash.
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CHAIN_LOCK_KEY})

    prev_hash = (
        await session.execute(select(AuditLog.hash).order_by(desc(AuditLog.id)).limit(1))
    ).scalar_one_or_none()

    fields = {
        "ts": ts if ts is not None else datetime.now(UTC),
        "operator": operator,
        "role": role,
        "tenant_id": tenant_id,
        "action": action,
        "target": target,
        "detail": detail or {},
        "dry_run": dry_run,
        "result": result,
        "request_id": request_id,
        "source_ip": source_ip,
    }
    digest = hashlib.sha256(_canonical(_payload(fields, prev_hash)).encode()).hexdigest()

    row = AuditLog(prev_hash=prev_hash, hash=digest, **fields)
    session.add(row)
    await session.flush()
    return row


@dataclass(frozen=True)
class ChainVerification:
    """R11 (docs/specs/done/audit-page.md, Tanda 3): además de íntegra/rota, cuántos
    registros se verificaron y el rango de `id` recorrido — para que la
    página Auditoría lo muestre sin adivinar qué tan al día está la
    verificación."""

    intact: bool
    first_broken_id: int | None
    checked: int
    first_id: int | None
    last_id: int | None


async def verify_chain(session: AsyncSession) -> ChainVerification:
    """Recorre el log entero y valida la cadena de hashes."""
    rows = (await session.execute(select(AuditLog).order_by(AuditLog.id))).scalars().all()
    if not rows:
        return ChainVerification(True, None, 0, None, None)

    first_id, last_id = rows[0].id, rows[-1].id
    prev_hash: str | None = None
    for i, r in enumerate(rows):
        fields = {
            "ts": r.ts,
            "operator": r.operator,
            "role": r.role,
            "tenant_id": r.tenant_id,
            "action": r.action,
            "target": r.target,
            "detail": r.detail,
            "dry_run": r.dry_run,
            "result": r.result,
            "request_id": r.request_id,
            "source_ip": r.source_ip,
        }
        expected = hashlib.sha256(_canonical(_payload(fields, prev_hash)).encode()).hexdigest()
        if r.prev_hash != prev_hash or r.hash != expected:
            return ChainVerification(False, r.id, i + 1, first_id, last_id)
        prev_hash = r.hash
    return ChainVerification(True, None, len(rows), first_id, last_id)
