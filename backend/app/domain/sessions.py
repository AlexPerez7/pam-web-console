"""Lógica de dominio de sesiones PSM. Puro, sin I/O.

Lo consume `app.api.writes` a través de `writeop.run_write`. Ver
`docs/specs/session-terminate.md`.
"""

from __future__ import annotations

from app.core.writeop import Plan


def plan_session_terminate(current: dict, _changes: dict) -> Plan:
    """`current` = objeto de `client.get_live_session(id)`. Acción, no
    edición: sin diff, sin `confirm_phrase` — terminar una sesión no borra
    nada persistente (el usuario se puede reconectar), mismo criterio que
    `account.verify`/`user.activate`."""
    user = current.get("User") or "un usuario"
    target = current.get("RemoteMachine") or "el target"
    effect = f"Se termina la sesión de **{user}** en **{target}**."
    return Plan(patch=[], diff=[], warnings=[], object_count=1, effect=effect)
