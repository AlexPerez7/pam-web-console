"""Lógica de dominio de roles de **Identity/SSO** (distintos de los
usuarios del Vault). Puro, sin I/O.

Alta de un rol ESTÁTICO y VACÍO (`identity_role.create`, nombre + descripción,
ver `docs/specs/done/identity-role-create.md`) —
mismo criterio que `safe.create`/`platform.duplicate`: aditivo, reversible
(borrar el rol desde el portal de Identity si se creó por error), sin
confirmación reforzada.

Agregar miembros a un rol (`identity_role.member.add`): usuarios y roles de
Identity, por ID. Ver `docs/specs/identity-role-members.md`.

Permisos administrativos (`AssignSuperRights`) quedan fuera de alcance, ver
`docs/PLAN.md` "Identity (administración de SSO/MFA)".
"""

from __future__ import annotations

from typing import Any

from app.core.writeop import Plan


def plan_identity_role_create(current: dict, changes: dict[str, Any]) -> Plan:
    """`current` = `{"exists": bool}` (¿ya hay un rol con ese nombre, sin
    distinguir mayúsculas?). Si existe el plan queda sin efecto (no-op) con un
    aviso, igual que "ya es miembro" en `identity_role.member.add`."""
    if current.get("exists"):
        return Plan(
            patch=[],
            diff=[],
            warnings=[
                f"ya existe un rol de Identity llamado '{changes['name']}'; no se crea otro."
            ],
        )
    diff: list[dict] = [{"field": "nombre", "from": None, "to": changes["name"]}]
    if changes.get("description"):
        diff.append({"field": "descripción", "from": None, "to": changes["description"]})
    diff.append({"field": "tipo", "from": None, "to": "Static"})
    warnings: list[str] = []
    if any(h in changes["name"].lower() for h in _ADMIN_HINTS):
        warnings.append(
            f"'{changes['name']}' parece un rol de administración: el rol se crea sin "
            "derechos ni miembros, pero el nombre puede confundirse con uno que sí los tiene."
        )
    effect = (
        f"Se crea el rol estático de Identity **{changes['name']}**, sin miembros ni "
        "derechos administrativos."
    )
    return Plan(patch=[], diff=diff, warnings=warnings, object_count=1, effect=effect)


MEMBER_TYPES = ("User", "Role")
_TYPE_LABELS = {"User": "Usuario", "Role": "Rol"}

# Roles de Identity que dan administración del tenant o de Privilege Cloud;
# agregar a alguien ahí es una escalada de privilegios y se avisa.
_ADMIN_HINTS = ("admin", "auditor", "sysadmin")


def normalize_new_members(raw: Any) -> list[dict[str, str]]:
    """`changes["members"]` -> `[{id, name, type}]`, sin vacíos ni
    duplicados (por tipo + ID), sólo tipos soportados."""
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for m in raw if isinstance(raw, list) else []:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "").strip()
        mtype = str(m.get("type") or "User").strip()
        if not mid or mtype not in MEMBER_TYPES or (mtype, mid) in seen:
            continue
        seen.add((mtype, mid))
        out.append({"id": mid, "name": str(m.get("name") or mid).strip(), "type": mtype})
    return out


def select_new_members(current: dict, changes: dict) -> tuple[list[dict[str, str]], list[str]]:
    """Los miembros pedidos que todavía no están en el rol + avisos por los
    que se omiten. `current` = `{"role_name": str, "members": [filas de
    GetRoleMembers]}` (ya resuelto por el `fetch` del endpoint). La usan el
    plan y la ejecución, así lo que se previsualiza es lo que se agrega."""
    rows = [m for m in current.get("members") or [] if isinstance(m, dict)]
    existing_ids = {str(m.get("Guid") or m.get("ID") or "").lower() for m in rows}
    existing_names = {str(m.get("Name") or "").lower() for m in rows}
    new: list[dict[str, str]] = []
    warnings: list[str] = []
    for m in normalize_new_members(changes.get("members")):
        if m["id"].lower() in existing_ids or m["name"].lower() in existing_names:
            warnings.append(f"'{m['name']}' ya es miembro del rol; se omite.")
        else:
            new.append(m)
    return new, warnings


def build_identity_role_member_add_plan(current: dict, changes: dict) -> Plan:
    role_name = str(current.get("role_name") or "el rol")
    new, warnings = select_new_members(current, changes)
    if not new:
        return Plan(
            patch=[], diff=[], warnings=warnings or ["no hay miembros nuevos para agregar."]
        )

    if any(h in role_name.lower() for h in _ADMIN_HINTS):
        warnings.append(
            f"'{role_name}' parece un rol de administración: sus miembros pueden recibir "
            "derechos administrativos sobre Identity o Privilege Cloud."
        )
    if any(m["type"] == "Role" for m in new):
        warnings.append(
            "agregar un rol como miembro suma a TODOS los miembros de ese rol, "
            "también a los que se agreguen en el futuro."
        )

    diff = [
        {"field": f"miembro ({_TYPE_LABELS[m['type']].lower()})", "from": None, "to": m["name"]}
        for m in new
    ]
    names = ", ".join(f"**{m['name']}**" for m in new)
    effect = f"Se agrega {names} como miembro del rol de Identity **{role_name}**."
    return Plan(patch=[], diff=diff, warnings=warnings, object_count=len(new), effect=effect)
