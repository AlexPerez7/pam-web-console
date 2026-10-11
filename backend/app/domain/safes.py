"""Lógica de dominio de safes — planes (dry-run) de agregar/editar/quitar un
miembro y de crear una safe.

Puro, sin I/O. Lo consume `app.api.writes` a través de `writeop.run_write`.
Ver `docs/specs/done/safe-member-add.md` y `docs/specs/safe-member-edit.md`.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from app.core.writeop import Plan
from app.domain.safe_permissions import (
    ADMIN_KEYS,
    ALL_KEYS,
    PERMISSION_KEYS,
    PRESET_LABELS,
    granted_keys,
    resolve_permissions,
)

MEMBER_TYPES = ("User", "Group", "Role")


def _iso_date(epoch: int) -> str:
    return datetime.fromtimestamp(int(epoch), tz=UTC).date().isoformat()


def _member_name(changes: dict) -> str:
    return str(changes.get("member_name") or "").strip()


def safe_member_body(changes: dict[str, Any]) -> dict[str, Any]:
    """Traduce el input del form al cuerpo de `POST /API/Safes/{id}/Members`."""
    perms = resolve_permissions(
        str(changes.get("preset") or "read_only"), changes.get("permissions")
    )
    body: dict[str, Any] = {
        "memberName": _member_name(changes),
        "searchIn": str(changes.get("search_in") or "Vault").strip() or "Vault",
        "MemberType": str(changes.get("member_type") or "User"),
        "permissions": perms,
    }
    exp = changes.get("membership_expiration_date")
    if exp:
        body["membershipExpirationDate"] = int(exp)
    return body


def build_safe_member_plan(current: dict, changes: dict) -> Plan:
    name = _member_name(changes)
    member_type = str(changes.get("member_type") or "User")
    search_in = str(changes.get("search_in") or "Vault").strip() or "Vault"
    preset = str(changes.get("preset") or "read_only")
    perms = resolve_permissions(preset, changes.get("permissions"))
    granted = granted_keys(perms)

    warnings: list[str] = []

    existing = {
        str(m.get("memberName") or "").lower()
        for m in (current.get("members") or [])
        if isinstance(m, dict)
    }
    if name.lower() in existing:
        warnings.append(
            f"'{name}' ya es miembro de esta safe; agregarlo de nuevo va a fallar. "
            'Para cambiarle los permisos, use "Editar" en la lista de miembros.'
        )

    if ADMIN_KEYS.intersection(granted):
        warnings.append(
            "estos permisos dejan al miembro cambiar la configuración y los "
            "permisos de la safe (manageSafe / manageSafeMembers)."
        )

    if not granted:
        warnings.append("el miembro no tendría ningún permiso sobre la safe.")

    diff: list[dict] = [
        {"field": "miembro", "from": None, "to": f"{name} ({member_type})"},
        {"field": "buscar en", "from": None, "to": search_in},
        {
            "field": "preset",
            "from": None,
            "to": PRESET_LABELS.get(preset, preset),
        },
        {"field": "permisos", "from": None, "to": ", ".join(granted) or "—"},
    ]

    exp = changes.get("membership_expiration_date")
    if exp:
        iso = datetime.fromtimestamp(int(exp), tz=UTC).date().isoformat()
        diff.append({"field": "vence", "from": None, "to": iso})

    effect = f"Se agrega a **{name}** ({member_type}) como miembro de esta safe."

    return Plan(patch=[], diff=diff, warnings=warnings, object_count=1, effect=effect)


# --- alta de safe (safe.create) ----------------------------------------- #


def suggest_safe_defaults(safes: list[dict]) -> dict[str, Any]:
    """CPM y retención más frecuentes entre las safes existentes del tenant,
    para precargar el form de `safe.create`. Puro. Vacío si no hay safes."""
    if not safes:
        return {}
    cpm = Counter(s.get("managingCPM") for s in safes if s.get("managingCPM")).most_common(1)
    days = Counter(
        s.get("numberOfDaysRetention") for s in safes if s.get("numberOfDaysRetention")
    ).most_common(1)
    versions = Counter(
        s.get("numberOfVersionsRetention") for s in safes if s.get("numberOfVersionsRetention")
    ).most_common(1)

    out: dict[str, Any] = {}
    if cpm:
        out["managing_cpm"] = cpm[0][0]
    if days:
        out["retention_type"], out["retention_value"] = "days", days[0][0]
    elif versions:
        out["retention_type"], out["retention_value"] = "versions", versions[0][0]
    return out


def list_cpm_names(safes: list[dict]) -> list[str]:
    """Nombres de CPM distintos entre las safes existentes del tenant — PAM Web Console
    no tiene (ni CyberArk expone directo) un "listar CPMs disponibles", así
    que se infiere de los que ya gestionan alguna safe. Alimenta el selector
    de `safe.create`/`safe.update`; un CPM nuevo que todavía no gestiona
    ninguna safe no aparece (el form permite tipearlo a mano)."""
    return sorted({s["managingCPM"] for s in safes if isinstance(s, dict) and s.get("managingCPM")})


def build_safe_create_body(changes: dict) -> dict[str, Any]:
    body: dict[str, Any] = {"safeName": changes["safe_name"]}
    if changes.get("description"):
        body["description"] = changes["description"]
    if changes.get("managing_cpm"):
        body["managingCPM"] = changes["managing_cpm"]
    if changes.get("retention_type") == "days" and changes.get("retention_value"):
        body["numberOfDaysRetention"] = changes["retention_value"]
    elif changes.get("retention_type") == "versions" and changes.get("retention_value"):
        body["numberOfVersionsRetention"] = changes["retention_value"]
    body["olacEnabled"] = bool(changes.get("olac_enabled"))
    return body


def plan_safe_create(_current: dict, changes: dict) -> Plan:
    diff: list[dict] = [{"field": "nombre", "from": None, "to": changes["safe_name"]}]
    if changes.get("description"):
        diff.append({"field": "descripción", "from": None, "to": changes["description"]})
    if changes.get("managing_cpm"):
        diff.append({"field": "CPM", "from": None, "to": changes["managing_cpm"]})
    if changes.get("retention_type") and changes.get("retention_value"):
        unit = "días" if changes["retention_type"] == "days" else "versiones"
        diff.append(
            {"field": "retención", "from": None, "to": f"{changes['retention_value']} {unit}"}
        )
    diff.append({"field": "OLAC", "from": None, "to": bool(changes.get("olac_enabled"))})

    cpm_txt = f" (CPM **{changes['managing_cpm']}**)" if changes.get("managing_cpm") else ""
    effect = f"Se crea la safe **{changes['safe_name']}**{cpm_txt}."
    return Plan(patch=[], diff=diff, warnings=[], object_count=1, effect=effect)


# --- Fase 3: editar / quitar un miembro existente ------------------------ #


def safe_member_update_body(member_name: str, changes: dict) -> dict[str, Any]:
    """Cuerpo de `PUT /API/Safes/{id}/Members/{memberName}`. `member_name`
    viene de la URL (fuente de verdad), no de `changes`."""
    perms = resolve_permissions(str(changes.get("preset") or "custom"), changes.get("permissions"))
    body: dict[str, Any] = {"memberName": member_name, "permissions": perms}
    exp = changes.get("membership_expiration_date")
    if exp:
        body["membershipExpirationDate"] = int(exp)
    return body


def find_member(members: list, name: str) -> dict | None:
    """Busca un miembro por nombre (case-insensitive) en una lista de
    `client.safe_members(...)`. Lo usan los `fetch` de `safe.member.update` /
    `safe.member.remove` — si no aparece, el endpoint levanta `NotFound`
    *ahí* (dentro del try/except de `writeop.run_write` que sí lo mapea a
    404); `build_plan` recibe siempre un miembro ya resuelto."""
    for m in members:
        if isinstance(m, dict) and str(m.get("memberName") or "").lower() == name.lower():
            return m
    return None


def build_safe_member_update_plan(current: dict, changes: dict) -> Plan:
    """`current` = {"member": {...}} (ya resuelto por el `fetch`, ver
    `find_member`). Compara los permisos/vencimiento pedidos contra los que
    el miembro ya tiene y arma el diff sólo de lo que cambia.
    `confirm_phrase` = el `memberName` tal como está en CyberArk (no el que
    haya tipeado el operador)."""
    member = current["member"]

    preset = str(changes.get("preset") or "custom")
    new_perms = resolve_permissions(preset, changes.get("permissions"))
    cur_perms_raw = member.get("permissions") or {}
    cur_perms = {k: bool(cur_perms_raw.get(k, False)) for k in ALL_KEYS}

    diff: list[dict] = [
        {"field": label, "from": cur_perms[key], "to": new_perms[key]}
        for key, label in PERMISSION_KEYS
        if cur_perms[key] != new_perms[key]
    ]

    cur_exp = member.get("membershipExpirationDate")
    new_exp = changes.get("membership_expiration_date")
    if new_exp is not None and new_exp != cur_exp:
        diff.append(
            {
                "field": "vence",
                "from": _iso_date(cur_exp) if cur_exp else None,
                "to": _iso_date(new_exp),
            }
        )

    warnings: list[str] = []
    granted = granted_keys(new_perms)
    if ADMIN_KEYS.intersection(granted):
        warnings.append(
            "estos permisos dejan al miembro cambiar la configuración y los "
            "permisos de la safe (manageSafe / manageSafeMembers)."
        )
    if not granted:
        warnings.append("el miembro se quedaría sin ningún permiso sobre la safe.")

    effect = (
        f"Se actualizan los permisos de **{member['memberName']}** en esta safe." if diff else None
    )

    return Plan(
        patch=[],
        diff=diff,
        warnings=warnings,
        object_count=1,
        effect=effect,
        confirm_phrase=str(member["memberName"]),
    )


def build_safe_member_remove_plan(current: dict, _changes: dict) -> Plan:
    """`current` = {"member": {...}}. Acción, no edición: `diff` muestra lo
    que se pierde (from -> None), igual que `safe_member_add` lo muestra al
    revés (None -> to)."""
    member = current["member"]

    perms = {k: bool((member.get("permissions") or {}).get(k, False)) for k in ALL_KEYS}
    granted = granted_keys(perms)

    diff: list[dict] = [
        {
            "field": "miembro",
            "from": f"{member['memberName']} ({member.get('memberType') or '—'})",
            "to": None,
        },
        {"field": "permisos", "from": ", ".join(granted) or "—", "to": None},
    ]
    exp = member.get("membershipExpirationDate")
    if exp:
        diff.append({"field": "vencía", "from": _iso_date(exp), "to": None})

    effect = f"Se quita a **{member['memberName']}** de esta safe — pierde todo acceso."

    return Plan(
        patch=[],
        diff=diff,
        warnings=[],
        object_count=1,
        effect=effect,
        confirm_phrase=str(member["memberName"]),
    )


# --- Fase 3: editar una safe existente ----------------------------------- #

_RETENTION_UNIT = {"days": "días", "versions": "versiones"}


def _current_retention(safe: dict) -> tuple[str, int] | None:
    if safe.get("numberOfDaysRetention"):
        return "days", int(safe["numberOfDaysRetention"])
    if safe.get("numberOfVersionsRetention"):
        return "versions", int(safe["numberOfVersionsRetention"])
    return None


def safe_update_body(current: dict, changes: dict) -> dict[str, Any]:
    """Cuerpo de `PUT /API/Safes/{id}` — parte del objeto actual (la API
    real pide el objeto completo, no un patch parcial) y sólo pisa lo que
    viene en `changes`."""
    body: dict[str, Any] = {
        "safeName": current["safeName"],
        "description": current.get("description") or "",
        "managingCPM": current.get("managingCPM") or "",
        "olacEnabled": bool(current.get("olacEnabled")),
    }
    cur_ret = _current_retention(current)
    if cur_ret and cur_ret[0] == "days":
        body["numberOfDaysRetention"] = cur_ret[1]
    elif cur_ret and cur_ret[0] == "versions":
        body["numberOfVersionsRetention"] = cur_ret[1]

    if "description" in changes:
        body["description"] = changes["description"] or ""
    if "managing_cpm" in changes:
        body["managingCPM"] = changes["managing_cpm"] or ""
    if "olac_enabled" in changes:
        body["olacEnabled"] = bool(changes["olac_enabled"])
    if changes.get("retention_type") and changes.get("retention_value"):
        body.pop("numberOfDaysRetention", None)
        body.pop("numberOfVersionsRetention", None)
        key = (
            "numberOfDaysRetention"
            if changes["retention_type"] == "days"
            else "numberOfVersionsRetention"
        )
        body[key] = changes["retention_value"]
    return body


def build_safe_update_plan(current: dict, changes: dict) -> Plan:
    """`current` = el objeto de `client.get_safe(safe_id)`. Compara
    description/managingCPM/retención/olacEnabled contra `changes` — diff
    sólo de lo que cambia. `confirm_phrase` = el `safeName` real."""
    diff: list[dict] = []
    warnings: list[str] = []

    if "description" in changes and changes["description"] != (current.get("description") or ""):
        diff.append(
            {
                "field": "descripción",
                "from": current.get("description") or None,
                "to": changes["description"] or None,
            }
        )

    if "managing_cpm" in changes and changes["managing_cpm"] != (current.get("managingCPM") or ""):
        diff.append(
            {
                "field": "CPM",
                "from": current.get("managingCPM") or None,
                "to": changes["managing_cpm"] or None,
            }
        )
        warnings.append(
            "cambiar el CPM afecta la rotación de TODAS las cuentas de esta safe — "
            "verificar que el CPM nuevo esté configurado para gestionarlas."
        )

    if changes.get("retention_type") and changes.get("retention_value"):
        cur_ret = _current_retention(current)
        new_ret = (changes["retention_type"], int(changes["retention_value"]))
        if cur_ret != new_ret:
            cur_txt = f"{cur_ret[1]} {_RETENTION_UNIT[cur_ret[0]]}" if cur_ret else None
            new_txt = f"{new_ret[1]} {_RETENTION_UNIT[new_ret[0]]}"
            diff.append({"field": "retención", "from": cur_txt, "to": new_txt})
            if cur_ret and cur_ret[0] == new_ret[0] and new_ret[1] < cur_ret[1]:
                warnings.append(
                    f"se reduce la retención de {cur_txt} a {new_txt}; las versiones/días "
                    "fuera de la nueva ventana no se recuperan."
                )

    if "olac_enabled" in changes:
        new_olac = bool(changes["olac_enabled"])
        cur_olac = bool(current.get("olacEnabled"))
        if new_olac != cur_olac:
            diff.append({"field": "OLAC", "from": cur_olac, "to": new_olac})
            if new_olac:
                warnings.append(
                    "OLAC activa listas de control de acceso por objeto — revisar los "
                    "permisos de los miembros después de activarlo."
                )
            else:
                warnings.append(
                    "desactivar OLAC puede no estar soportado por CyberArk una vez "
                    "activado; si la operación falla, es un límite de la plataforma."
                )

    effect = f"Se actualiza la safe **{current['safeName']}**." if diff else None

    return Plan(
        patch=[],
        diff=diff,
        warnings=warnings,
        object_count=1,
        effect=effect,
        confirm_phrase=str(current["safeName"]),
    )


# --- Fase 3: borrar una safe vacía (safe.delete) ------------------------- #

_DELETE_FIELDS = (
    ("safeName", "nombre"),
    ("description", "descripción"),
    ("managingCPM", "CPM"),
)


def build_safe_delete_plan(current: dict, _changes: dict) -> Plan:
    """`current` = {"safe": {...}} — ya validada vacía (0 cuentas) por el
    `fetch` del endpoint, que levanta 422 antes de llegar acá si tiene
    cuentas. Diff = la safe `from -> None`. `confirm_phrase` = `safeName`."""
    safe = current["safe"]
    diff: list[dict] = [
        {"field": label, "from": safe.get(key), "to": None}
        for key, label in _DELETE_FIELDS
        if safe.get(key)
    ]
    name = str(safe["safeName"])
    effect = f"Se borra la safe **{name}** (vacía)."
    return Plan(
        patch=[], diff=diff, warnings=[], object_count=1, effect=effect, confirm_phrase=name
    )
