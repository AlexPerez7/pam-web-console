"""Lógica de dominio de grupos del Vault (Fase 3). Puro, sin I/O.

Lo consume `app.api.writes` a través de `writeop.run_write`. Ver
`docs/specs/vault-users-groups-crud.md`. Endpoints/casing verificados
contra `New-PASGroup`/`Set-PASGroup`/`Remove-PASGroup`/
`Add-PASGroupMember`/`Remove-PASGroupMember` de `psPAS` — a diferencia de
Usuarios, los campos de Grupo sí usan camelCase (`groupName`), consistente
con la respuesta de `GET /API/UserGroups/{id}`.
"""

from __future__ import annotations

from typing import Any

from app.core.writeop import Plan


def build_group_create_body(changes: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {"groupName": changes["group_name"]}
    if changes.get("description"):
        body["description"] = changes["description"]
    if changes.get("location"):
        body["location"] = changes["location"]
    return body


def plan_group_create(_current: dict, changes: dict[str, Any]) -> Plan:
    diff: list[dict] = [{"field": "nombre", "from": None, "to": changes["group_name"]}]
    if changes.get("description"):
        diff.append({"field": "descripción", "from": None, "to": changes["description"]})
    if changes.get("location"):
        diff.append({"field": "ubicación", "from": None, "to": changes["location"]})
    effect = f"Se crea el grupo **{changes['group_name']}**."
    return Plan(patch=[], diff=diff, warnings=[], object_count=1, effect=effect)


def build_group_update_body(group_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    """Cuerpo de `PUT /API/UserGroups/{id}` — sólo `groupName`, el único
    campo que "Update Group" acepta según `Set-PASGroup`."""
    return {"id": group_id, "groupName": changes["group_name"]}


def build_group_update_plan(current: dict, changes: dict[str, Any]) -> Plan:
    """`current` = objeto de `client.get_group(id)`. Sólo renombra —
    cualquier otro campo pedido se ignora, no lo soporta la API."""
    cur_name = str(current.get("groupName") or "")
    new_name = str(changes.get("group_name") or "").strip()
    if not new_name or new_name == cur_name:
        return Plan(patch=[], diff=[], warnings=[], object_count=1)

    diff = [{"field": "nombre", "from": cur_name, "to": new_name}]
    effect = f"Se renombra el grupo **{cur_name}** a **{new_name}**."
    return Plan(patch=[], diff=diff, warnings=[], object_count=1, effect=effect)


_GROUP_DELETE_FIELDS = (("groupName", "nombre"), ("description", "descripción"))


def build_group_delete_plan(current: dict, _changes: dict) -> Plan:
    """`current` = {"group": {...}} — ya validado sin miembros por el
    `fetch` del endpoint (levanta 422 antes de llegar acá si tiene
    miembros). `confirm_phrase` = `groupName`."""
    group = current.get("group") or {}
    diff: list[dict] = [
        {"field": label, "from": group.get(key), "to": None}
        for key, label in _GROUP_DELETE_FIELDS
        if group.get(key)
    ]
    name = str(group.get("groupName") or "")
    effect = f"Se borra el grupo **{name}** (sin miembros)."
    return Plan(
        patch=[], diff=diff, warnings=[], object_count=1, effect=effect, confirm_phrase=name
    )


def group_member_body(changes: dict[str, Any]) -> dict[str, Any]:
    """Cuerpo de `POST /API/UserGroups/{id}/Members`."""
    body: dict[str, Any] = {
        "memberId": str(changes.get("member_id") or "").strip(),
        "memberType": str(changes.get("member_type") or "vault"),
    }
    if changes.get("member_type") == "domain" and changes.get("domain_name"):
        body["domainName"] = changes["domain_name"]
    return body


def build_group_member_add_plan(current: dict, changes: dict[str, Any]) -> Plan:
    """`current` = objeto completo del grupo (para chequear duplicados)."""
    member_id = str(changes.get("member_id") or "").strip()
    member_type = str(changes.get("member_type") or "vault")

    warnings: list[str] = []
    existing = {
        str(m.get("username") or m.get("name") or "").lower()
        for m in (current.get("members") or [])
        if isinstance(m, dict)
    }
    if member_id.lower() in existing:
        warnings.append(
            f"'{member_id}' ya es miembro de este grupo; agregarlo de nuevo va a fallar."
        )

    diff = [{"field": "miembro", "from": None, "to": f"{member_id} ({member_type})"}]
    effect = f"Se agrega a **{member_id}** como miembro de este grupo."
    return Plan(patch=[], diff=diff, warnings=warnings, object_count=1, effect=effect)


def find_group_member(members: list, name: str) -> dict | None:
    """Busca un miembro por nombre (case-insensitive) en
    `client.user_group_members(...)` — mismo patrón que `find_member` de
    `safes.py`."""
    for m in members:
        if isinstance(m, dict) and str(m.get("memberName") or "").lower() == name.lower():
            return m
    return None


def build_group_member_remove_plan(current: dict, _changes: dict) -> Plan:
    """`current` = {"member": {...}} (ya resuelto por el `fetch`, ver
    `find_group_member`). `confirm_phrase` = el `memberName` real."""
    member = current["member"]
    name = str(member.get("memberName") or "")
    diff = [
        {
            "field": "miembro",
            "from": f"{name} ({member.get('memberType') or '—'})",
            "to": None,
        }
    ]
    effect = f"Se quita a **{name}** de este grupo."
    return Plan(
        patch=[], diff=diff, warnings=[], object_count=1, effect=effect, confirm_phrase=name
    )
