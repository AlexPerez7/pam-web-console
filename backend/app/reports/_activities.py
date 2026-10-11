"""Actividad de TODAS las cuentas de un tenant, en paralelo -- compartido
por los reportes "heavy" de Batch 4 (`target_inactivity.py`,
`account_activity.py`), que necesitan un `GET /Accounts/{id}/Activities`
por cuenta. Mismo patrón que `app.api.itr._all_account_activities`
(no se reusa esa función directamente: es privada de un módulo ya
cerrado/probado, y tocarlo para exportarla es más riesgo que duplicar
esta función chica).
"""

from __future__ import annotations

import asyncio

from app.cyberark import CyberArkClient, CyberArkError


async def all_account_activities(
    client: CyberArkClient, accounts: list[dict]
) -> dict[str, list | None]:
    """Una cuenta puntual que falla queda en `None` (no se pudo consultar,
    sin permiso, etc.) -- no aborta el resto. Es la parte más lenta de
    estos reportes (1 request por cuenta)."""

    async def _one(acc_id: str) -> tuple[str, list | None]:
        try:
            return acc_id, await client.account_activities(acc_id)
        except CyberArkError:
            return acc_id, None

    ids = [str(a["id"]) for a in accounts if a.get("id")]
    pairs = await asyncio.gather(*(_one(acc_id) for acc_id in ids))
    return dict(pairs)
