"""Lógica de dominio de usuarios de **Identity/SSO** (distintos de los
usuarios del Vault, ver `app.domain.users`). Puro, sin I/O.

Primer corte: ver estado + bloquear/desbloquear (`IsUserCloudLocked`/
`SetCloudLock`). Ver `docs/specs/identity-users.md`.
"""

from __future__ import annotations

from collections.abc import Callable

from app.core.writeop import Plan


def plan_identity_user_lock(target_lock: bool) -> Callable[[dict, dict], Plan]:
    """Factory igual a `plan_platform_activation`: dos endpoints (`/lock`,
    `/unlock`) comparten esta misma lógica con `target_lock` fijo.
    `current` = `{"locked": bool, "username": str}` (ya resuelto por el
    `fetch` del endpoint)."""

    def _build(current: dict, _changes: dict) -> Plan:
        username = current.get("username") or "el usuario"
        locked = bool(current.get("locked"))

        if locked == target_lock:
            estado = "bloqueado" if locked else "activo"
            return Plan(
                patch=[],
                diff=[],
                warnings=[f"ya está {estado}; la operación no va a tener efecto."],
                object_count=1,
            )

        if target_lock:
            effect = (
                f"Se bloquea al usuario de Identity **{username}** — no va a poder "
                "iniciar sesión hasta que se lo desbloquee."
            )
        else:
            effect = (
                f"Se desbloquea al usuario de Identity **{username}** — puede volver "
                "a iniciar sesión."
            )
        return Plan(patch=[], diff=[], warnings=[], object_count=1, effect=effect)

    return _build
