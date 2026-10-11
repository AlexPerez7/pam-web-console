"""Lógica de dominio de usuarios del Vault. Puro, sin I/O.

Lo consume `app.api.writes` a través de `writeop.run_write`.
Ver `docs/specs/user-activate.md` y (Fase 3) `docs/specs/
vault-users-groups-crud.md`.
"""

from __future__ import annotations

from typing import Any

from app.core.writeop import Plan


def plan_user_activate(current: dict, _changes: dict) -> Plan:
    """`current` = objeto de Get User Details. Reactiva un usuario suspendido
    por intentos fallidos de login."""
    username = current.get("username") or current.get("userName") or "el usuario"
    suspended = bool(current.get("suspended"))
    enabled = current.get("enableUser")

    warnings: list[str] = []
    if not suspended:
        warnings.append("este usuario no está suspendido; la operación no va a tener efecto.")
    if enabled is False:
        warnings.append(
            "este usuario también está deshabilitado; reactivarlo no lo habilita "
            "(eso es otra operación)."
        )

    effect = (
        f"Se reactiva al usuario **{username}**, suspendido por intentos fallidos "
        "de login. Vuelve a poder iniciar sesión."
    )
    return Plan(patch=[], diff=[], warnings=warnings, object_count=1, effect=effect)


# --- CRUD de usuarios (Fase 3, ver docs/specs/vault-users-groups-crud.md) #
#
# ADVERTENCIA de verificación: `docs.cyberark.com` bloqueó el fetch para
# "Add/Update/Delete User" — el body de alta/edición se armó con el
# casing EXACTO de los parámetros Gen2 de `New-PASUser`/`Set-PASUser` de
# `psPAS` (PascalCase: `UserName`, `FirstName`, `LastName`,
# `ChangePassOnNextLogon` — distinto del camelCase de la RESPUESTA de Get
# User Details, `username`/`personalDetails.firstName`/etc. — es una
# asimetría real de esta API legacy, no un error de tipeo). `email` se
# dejó FUERA de v1: en `psPAS` sólo existe en el parameter set Gen1
# (`PIMServices.svc`), no confirmado que el `/api/Users` Gen2 lo acepte.
# Confirmar este casing contra Globex antes de dar la spec por cerrada.

# campos editables de un usuario existente (Update User, Gen2). `username`
# no se edita por acá (es el identificador).
_USER_UPDATE_FIELDS = ("first_name", "last_name", "change_pass_on_next_logon")


def build_user_create_body(changes: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "UserName": changes["username"],
        "InitialPassword": changes["initial_password"],
        "ChangePassOnNextLogon": bool(changes.get("change_pass_on_next_logon", True)),
    }
    if changes.get("first_name"):
        body["FirstName"] = changes["first_name"]
    if changes.get("last_name"):
        body["LastName"] = changes["last_name"]
    return body


def plan_user_create(_current: dict, changes: dict[str, Any]) -> Plan:
    """`_current` no se usa (es una alta) — firma uniforme con el resto de
    `build_plan`. El `initial_password` NUNCA aparece en el diff."""
    diff: list[dict] = [{"field": "usuario", "from": None, "to": changes["username"]}]
    if changes.get("first_name"):
        diff.append({"field": "nombre", "from": None, "to": changes["first_name"]})
    if changes.get("last_name"):
        diff.append({"field": "apellido", "from": None, "to": changes["last_name"]})
    diff.append(
        {
            "field": "cambiar contraseña en el próximo login",
            "from": None,
            "to": bool(changes.get("change_pass_on_next_logon", True)),
        }
    )
    effect = f"Se crea el usuario **{changes['username']}**."
    return Plan(patch=[], diff=diff, warnings=[], object_count=1, effect=effect)


def build_user_update_body(current: dict, changes: dict[str, Any]) -> dict[str, Any]:
    """Cuerpo de `PUT /api/Users/{id}` — no está confirmado si acepta un
    PUT parcial o exige el objeto completo (a diferencia de Update
    Account/Safe, ya verificados); por las dudas, parte del estado actual
    completo (mapeado a los nombres de campo de la REQUEST, distintos de
    los de la respuesta de Get User) y sólo pisa lo que viene en
    `changes`."""
    personal = current.get("personalDetails") or {}
    body: dict[str, Any] = {
        "id": current.get("id"),
        "username": current.get("username"),
        "FirstName": personal.get("firstName") or "",
        "LastName": personal.get("lastName") or "",
        "ChangePassOnNextLogon": bool(current.get("changePassOnNextLogon")),
    }
    if "first_name" in changes:
        body["FirstName"] = changes["first_name"] or ""
    if "last_name" in changes:
        body["LastName"] = changes["last_name"] or ""
    if "change_pass_on_next_logon" in changes:
        body["ChangePassOnNextLogon"] = bool(changes["change_pass_on_next_logon"])
    return body


def build_user_update_plan(current: dict, changes: dict[str, Any]) -> Plan:
    """`current` = objeto de Get User Details. Diff sólo de lo que cambia
    realmente contra el valor actual."""
    personal = current.get("personalDetails") or {}
    cur_values = {
        "first_name": personal.get("firstName") or "",
        "last_name": personal.get("lastName") or "",
        "change_pass_on_next_logon": bool(current.get("changePassOnNextLogon")),
    }
    labels = {
        "first_name": "nombre",
        "last_name": "apellido",
        "change_pass_on_next_logon": "cambiar contraseña en el próximo login",
    }
    diff: list[dict] = []
    for field in _USER_UPDATE_FIELDS:
        if field not in changes:
            continue
        new = changes[field] if field == "change_pass_on_next_logon" else (changes[field] or "")
        if field == "change_pass_on_next_logon":
            new = bool(new)
        if new != cur_values[field]:
            diff.append({"field": labels[field], "from": cur_values[field], "to": new})

    username = current.get("username") or "el usuario"
    effect = f"Se actualiza el usuario **{username}**." if diff else None
    return Plan(patch=[], diff=diff, warnings=[], object_count=1, effect=effect)


def build_user_delete_plan(current: dict, _changes: dict) -> Plan:
    """`current` = objeto de Get User Details. `confirm_phrase` = el
    `username` real. Sin backup — a diferencia de `account.delete`, un
    usuario del Vault no tiene un secreto que preservar; el `before` del
    audit alcanza para reconstruir a mano si hace falta (mismo criterio
    documentado para el resto de Fase 3, ver `docs/PLAN.md §7`)."""
    username = str(current.get("username") or "")
    personal = current.get("personalDetails") or {}
    diff: list[dict] = [{"field": "usuario", "from": username, "to": None}]
    if personal.get("firstName") or personal.get("lastName"):
        name = f"{personal.get('firstName') or ''} {personal.get('lastName') or ''}".strip()
        diff.append({"field": "nombre", "from": name, "to": None})

    effect = f"Se borra el usuario **{username}**."
    return Plan(
        patch=[], diff=diff, warnings=[], object_count=1, effect=effect, confirm_phrase=username
    )
