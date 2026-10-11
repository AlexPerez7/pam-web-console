"""Planes de escritura para cuentas descubiertas por CyberArk Discovery
(Gen2, Privilege Cloud/ISPSS) -- puro, sin I/O. Ver `app/api/discovery.py`
y los métodos `*_discovered_account*` de `app/cyberark/client.py`."""

from __future__ import annotations

from app.core.writeop import Plan


def _identity(current: dict) -> str:
    identifiers = current.get("identifiers") or {}
    username = identifiers.get("username") or "?"
    address = identifiers.get("address") or "?"
    return f"{username}@{address}"


def plan_discovered_account_onboard(current: dict, changes: dict) -> Plan:
    """`current` = la cuenta descubierta (fetch de `get_discovered_account`);
    `changes = {safe_name, platform_id, reset_secret, tags?,
    additional_properties?}` -- nunca el secreto (va aparte, directo a
    `apply`, nunca al diff/audit). Sin `confirm_phrase`: mismo riesgo que
    `account.create` (alta nueva, no toca nada existente)."""
    who = _identity(current)
    effect = (
        f"Se onboardea **{who}** al Vault -- Safe **{changes['safe_name']}**, "
        f"plataforma **{changes['platform_id']}**."
    )
    warnings = []
    if changes.get("reset_secret") is False:
        warnings.append("la contraseña NO se rota al onboardear; CyberArk conserva la que ya tenía")
    return Plan(patch=[], diff=[], warnings=warnings, object_count=1, effect=effect)


def plan_discovered_account_ignore(current: dict, _changes: dict) -> Plan:
    who = _identity(current)
    effect = (
        f"**{who}** se mueve a la lista de ignoradas -- no vuelve a aparecer en los próximos scans."
    )
    return Plan(patch=[], diff=[], warnings=[], object_count=1, effect=effect)


def plan_discovered_account_restore(current: dict, _changes: dict) -> Plan:
    who = _identity(current)
    effect = f"**{who}** vuelve a la lista de cuentas descubiertas pendientes."
    return Plan(patch=[], diff=[], warnings=[], object_count=1, effect=effect)


def plan_discovered_account_delete(current: dict, _changes: dict) -> Plan:
    who = _identity(current)
    effect = (
        f"Se borra **{who}** de la lista de descubiertas -- no afecta el "
        "próximo scan, puede volver a aparecer."
    )
    return Plan(patch=[], diff=[], warnings=[], object_count=1, effect=effect)
