"""Lógica de dominio de cuentas — cálculo del plan de cambios (dry-run).

Puro, sin I/O. Lo consume `app.api.writes` a través de `writeop.run_write`.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable
from typing import Any

from app.core.writeop import Plan

# campos de metadata editables en `account.update` (Fase 2, reversible).
# `platformId` está en la lista oficial de "Properties that can be updated"
# de la API de Update Account (docs.cyberark.com/.../sdk/updateaccount v10) —
# verificado 2026-09-14. `secretManagement.automaticManagementEnabled`
# también está en esa lista (`secretManagement.*`) — se maneja aparte más
# abajo porque vive anidado, no a nivel raíz de la cuenta. `safeName` NO
# está en esa lista (cambiar de safe NO se puede por acá; el PVWA clásico
# lo hace con AccountsMgt.asmx/Move, un endpoint interno sin Bearer token —
# depende de la sesión de navegador del operador, no del service account).
# Mover de safe SÍ existe en PAM Web Console, pero por un endpoint aparte
# (`POST .../accounts/{id}/move`, `build_account_move_plan` más abajo) que
# no es un PATCH real: clona la cuenta en el safe destino y borra la
# original (`platform_admin`, pierde historial nativo de CyberArk).
EDITABLE_TOP_LEVEL = ("name", "address", "userName", "platformId")


def _jp_escape(token: str) -> str:
    """Escape de un segmento de JSON Pointer (RFC 6901)."""
    return token.replace("~", "~0").replace("/", "~1")


def build_account_plan(current: dict, changes: dict) -> Plan:
    patch: list[dict] = []
    diff: list[dict] = []
    warnings: list[str] = []

    for f in EDITABLE_TOP_LEVEL:
        if f not in changes:
            continue
        new = changes[f]
        old = current.get(f)
        if new == old:
            continue
        if not new:
            warnings.append(f"'{f}' queda vacío — CyberArk puede rechazarlo")
        patch.append({"op": "replace", "path": f"/{f}", "value": new})
        diff.append({"field": f, "from": old, "to": new})

    props: dict[str, Any] = changes.get("platformAccountProperties") or {}
    cur_props: dict[str, Any] = current.get("platformAccountProperties") or {}
    for k, v in props.items():
        if cur_props.get(k) == v:
            continue
        pointer = f"/platformAccountProperties/{_jp_escape(k)}"
        patch.append({"op": "replace" if k in cur_props else "add", "path": pointer, "value": v})
        diff.append({"field": f"props.{k}", "from": cur_props.get(k), "to": v})

    # gestión automática (habilitar/deshabilitar rotación del CPM) — igual
    # que "Enable/Disable automatic management" del PVWA. Ausente en `sm` =
    # habilitada (default de CyberArk), de ahí el `True` en el `.get`.
    sm: dict[str, Any] = current.get("secretManagement") or {}
    if "automaticManagementEnabled" in changes:
        new_enabled = bool(changes["automaticManagementEnabled"])
        old_enabled = bool(sm.get("automaticManagementEnabled", True))
        if new_enabled != old_enabled:
            op = "replace" if "automaticManagementEnabled" in sm else "add"
            patch.append(
                {
                    "op": op,
                    "path": "/secretManagement/automaticManagementEnabled",
                    "value": new_enabled,
                }
            )
            diff.append(
                {
                    "field": "secretManagement.automaticManagementEnabled",
                    "from": old_enabled,
                    "to": new_enabled,
                }
            )
            if new_enabled:
                warnings.append(
                    "la gestión automática queda activada: el CPM puede rotar/"
                    "verificar esta cuenta en su próximo ciclo"
                )
            else:
                reason = str(changes.get("manualManagementReason") or "").strip()
                if reason:
                    r_op = "replace" if "manualManagementReason" in sm else "add"
                    patch.append(
                        {
                            "op": r_op,
                            "path": "/secretManagement/manualManagementReason",
                            "value": reason,
                        }
                    )
                    diff.append(
                        {
                            "field": "secretManagement.manualManagementReason",
                            "from": sm.get("manualManagementReason"),
                            "to": reason,
                        }
                    )
                warnings.append(
                    "la gestión automática queda desactivada: el CPM deja de rotar/"
                    "verificar esta cuenta"
                )

    rma: dict[str, Any] = current.get("remoteMachinesAccess") or {}
    if "remote_machines" in changes or "access_restricted_to_remote_machines" in changes:
        new_rm = str(changes.get("remote_machines", rma.get("remoteMachines") or "")).strip()
        new_restr = bool(
            changes.get(
                "access_restricted_to_remote_machines", rma.get("accessRestrictedToRemoteMachines")
            )
        )
        old_rm = str(rma.get("remoteMachines") or "").strip()
        old_restr = bool(rma.get("accessRestrictedToRemoteMachines"))

        if new_rm != old_rm or new_restr != old_restr:
            new_rma = {"remoteMachines": new_rm, "accessRestrictedToRemoteMachines": new_restr}
            op = "replace" if current.get("remoteMachinesAccess") else "add"
            patch.append({"op": op, "path": "/remoteMachinesAccess", "value": new_rma})

            if new_rm != old_rm:
                diff.append(
                    {"field": "remote machine access", "from": old_rm or None, "to": new_rm or None}
                )
            if new_restr != old_restr:
                diff.append(
                    {
                        "field": "acceso restringido a estas máquinas",
                        "from": old_restr,
                        "to": new_restr,
                    }
                )

    if any(d["field"] == "address" for d in diff):
        warnings.append("cambiar 'address' no re-descubre la cuenta ni rota el secreto")
    if any(d["field"] == "platformId" for d in diff):
        warnings.append(
            "cambiar la plataforma no valida ni completa las propiedades que la "
            "plataforma nueva requiere — revisar 'platformAccountProperties' después "
            "del cambio, o el CPM puede empezar a fallar la gestión de la cuenta."
        )

    return Plan(patch=patch, diff=diff, warnings=warnings, object_count=1)


# --- edición en lote (account.update, misma feature, N cuentas) --------- #
#
# Pedido de Alex: la vista clásica del PVWA permite tildar varias cuentas y
# editarlas juntas (ej. 5 cuentas de firewall a las que hay que cambiarles
# el address a un valor NUEVO, igual para las 5). Reusa build_account_plan
# por cuenta — el `patch` real (uno por cuenta, JSON Patch es por recurso)
# se recalcula en `apply` con la misma `changes`, no viaja en el Plan.


def build_account_bulk_plan(current: dict, changes: dict) -> Plan:
    """`current` = {account_id: account_dict, ...} (ya resueltos por el
    `fetch` del endpoint). Aplica los mismos `changes` de `account.update` a
    cada cuenta y arma un diff con una fila por campo que realmente cambia,
    prefijada con el nombre de la cuenta — las que ya tienen el valor
    pedido no aparecen."""
    diff: list[dict] = []
    warnings: list[str] = []
    affected = 0

    for account_id, account in current.items():
        sub = build_account_plan(account, changes)
        if not sub.patch:
            continue
        affected += 1
        label = str(account.get("name") or account_id)
        for d in sub.diff:
            diff.append({"field": f"{label} — {d['field']}", "from": d["from"], "to": d["to"]})
        for w in sub.warnings:
            if w not in warnings:
                warnings.append(w)

    effect = f"Se actualizan {affected} cuenta(s)." if affected else None
    return Plan(
        patch=[], diff=diff, warnings=warnings, object_count=max(affected, 1), effect=effect
    )


# --- acciones on-demand del CPM (change / verify / reconcile) ------------ #

_CPM_EFFECT = {
    "change": (
        "El CPM va a **cambiar** la contraseña de esta cuenta ahora. La "
        "credencial actual deja de ser válida en cuanto el CPM completa el "
        "cambio en el target."
    ),
    "verify": (
        "El CPM va a **verificar** que la contraseña guardada coincide con la "
        "del target. No cambia nada."
    ),
    "reconcile": (
        "El CPM va a **reconciliar**: resetea la contraseña del target usando "
        "la reconcile account. La credencial actual deja de ser válida."
    ),
}


def plan_cpm(action: str) -> Callable[[dict, dict], Plan]:
    """Devuelve un `build_plan` para una acción del CPM. No hay diff — la
    operación se describe con `Plan.effect` + warnings según el estado."""

    def _build(current: dict, _changes: dict) -> Plan:
        sm = current.get("secretManagement") or {}
        warnings: list[str] = []
        if action in ("change", "reconcile") and sm.get("automaticManagementEnabled") is False:
            warnings.append(
                "la gestión automática está desactivada para esta cuenta; el CPM "
                "puede ignorar el pedido"
            )
        status = str(sm.get("status") or "").lower()
        if status in ("inprocess", "in process"):
            warnings.append("ya hay una operación de CPM en curso para esta cuenta")

        return Plan(
            patch=[],
            diff=[],
            warnings=warnings,
            object_count=1,
            effect=_CPM_EFFECT[action],
            is_cpm_action=True,
        )

    return _build


_CPM_BULK_VERB = {
    "change": "**cambiar** la contraseña de",
    "verify": "**verificar** la contraseña de",
    "reconcile": "**reconciliar**",
}


def plan_cpm_bulk(action: str) -> Callable[[dict, dict], Plan]:
    """`build_plan` de una acción del CPM sobre VARIAS cuentas (tilde-múltiple
    de la grilla de Cuentas). `current` = {account_id: account_dict, ...}.
    Reusa `plan_cpm` cuenta por cuenta para los warnings (prefijados con el
    nombre de la cuenta); `object_count` = cantidad de cuentas, así el
    circuit breaker de `run_write` limita el tamaño del lote."""
    single = plan_cpm(action)

    def _build(current: dict, changes: dict) -> Plan:
        labels: list[str] = []
        warnings: list[str] = []
        for account_id, account in current.items():
            label = str(account.get("name") or account_id)
            labels.append(label)
            warnings += [f"{label}: {w}" for w in single(account, changes).warnings]

        shown = ", ".join(labels[:10]) + (f" y {len(labels) - 10} más" if len(labels) > 10 else "")
        effect = (
            f"El CPM va a {_CPM_BULK_VERB[action]} {len(labels)} cuenta(s): {shown}. "
            "Cada pedido se procesa por separado en el próximo ciclo del CPM."
        )
        return Plan(
            patch=[],
            diff=[],
            warnings=warnings,
            object_count=max(len(labels), 1),
            effect=effect,
            is_cpm_action=True,
        )

    return _build


# --- fijar un valor de contraseña específico (sin generar uno aleatorio) - #
#
# Distinto de `change`/`reconcile` (el CPM elige el valor según la política
# de la plataforma): acá el OPERADOR escribe la contraseña. El valor nunca
# aparece en el diff/audit — sólo pasa por el body real hacia CyberArk.


def plan_password_vault_only(_current: dict, _changes: dict) -> Plan:
    """`POST /API/Accounts/{id}/Password/Update` — actualiza SOLO lo que
    CyberArk/PAM Web Console tienen guardado; nunca toca el target. Ver
    `docs/specs/account-password-manual.md`."""
    diff = [{"field": "contraseña (sólo Vault)", "from": None, "to": "(definida)"}]
    effect = "Se actualiza el valor guardado en la Vault, **sin tocar el sistema destino**."
    warnings = [
        "usar SOLO si la contraseña real ya se cambió en el target por fuera de "
        "CyberArk — si el valor no coincide con el que el target tiene "
        "realmente, la próxima autenticación con esta cuenta va a fallar."
    ]
    return Plan(patch=[], diff=diff, warnings=warnings, object_count=1, effect=effect)


def plan_password_set_next(_current: dict, _changes: dict) -> Plan:
    """`POST /API/Accounts/{id}/SetNextPassword` (`ChangeImmediately: false`)
    — guarda el valor para que el CPM lo use en su PRÓXIMO cambio
    automático; no toca nada ahora. Ver
    `docs/specs/account-password-manual.md`."""
    diff = [{"field": "próxima contraseña (CPM)", "from": None, "to": "(definida)"}]
    effect = (
        "El CPM va a usar este valor la **próxima** vez que cambie la "
        "contraseña — no cambia nada ahora."
    )
    return Plan(patch=[], diff=diff, warnings=[], object_count=1, effect=effect)


# --- vincular cuenta de logon / reconciliación (account.link) ----------- #

# extraPasswordIndex de la plataforma: 1 = logon, 3 = reconcile (estándar).
LINK_INDEX = {"logon": 1, "reconcile": 3}
_LINK_LABEL = {"logon": "logon", "reconcile": "reconciliación"}


def account_link_body(changes: dict) -> dict[str, Any]:
    """Traduce el form al cuerpo de `POST /API/Accounts/{id}/LinkAccount`."""
    link_type = str(changes.get("link_type") or "logon")
    return {
        "safe": str(changes.get("linked_safe") or "").strip(),
        "folder": str(changes.get("linked_folder") or "Root").strip() or "Root",
        "name": str(changes.get("linked_name") or "").strip(),
        "extraPasswordIndex": LINK_INDEX.get(link_type, 1),
    }


# --- alta de cuenta (account.create), wizard estilo PVWA --------------- #
#
# NOTA: se intentó leer en vivo las propiedades Required/Optional de la
# plataforma vía "Get Platform Details" (`GET /API/Platforms/{id}`), pero esa
# API sólo devuelve la política de gestión de contraseñas del CPM (retries,
# complejidad, notificaciones…), no el esquema de campos del "Add Account" de
# PVWA — eso vive en `Policies.xml` (safe PVWAConfig), sin API pública.
# Verificado 2026-09-11 contra Globex. Por eso "Additional Properties" es
# una lista libre clave/valor que arma el operador (con sugerencias de
# nombres comunes por tipo de sistema, sólo de referencia).


def build_account_create_body(changes: dict) -> dict[str, Any]:
    """Traduce el form del wizard al cuerpo de `POST /API/Accounts`. El
    secreto (si vino) viaja acá y en ningún otro lado — `plan_account_create`
    nunca lo lee, para que no termine en el diff/audit."""
    body: dict[str, Any] = {
        "safeName": changes["safe_name"],
        "platformId": changes["platform_id"],
        "userName": changes["user_name"],
        "address": changes["address"],
    }
    if changes.get("name"):
        body["name"] = changes["name"]
    if changes.get("secret"):
        body["secret"] = changes["secret"]
        body["secretType"] = "password"
    props = {k: v for k, v in (changes.get("properties") or {}).items() if v}
    if props:
        body["platformAccountProperties"] = props

    # "Allow automatic password management" — sólo se manda si se desactiva
    # (el default de la plataforma ya cubre el caso habilitado).
    if changes.get("automatic_management_enabled") is False:
        sm: dict[str, Any] = {"automaticManagementEnabled": False}
        if changes.get("manual_management_reason"):
            sm["manualManagementReason"] = changes["manual_management_reason"]
        body["secretManagement"] = sm

    # "Remote Machine Access" — lista de targets (separados por ";") a los
    # que puede acceder esta cuenta, típico de cuentas de dominio.
    remote = str(changes.get("remote_machines") or "").strip()
    if remote:
        body["remoteMachinesAccess"] = {
            "remoteMachines": remote,
            "accessRestrictedToRemoteMachines": bool(
                changes.get("access_restricted_to_remote_machines")
            ),
        }
    return body


def plan_account_create(_current: dict, changes: dict) -> Plan:
    """`_current` = {"safe": ..., "platform_details": ...} — ya validados
    (existencia + propiedades requeridas) por el `fetch` del endpoint. No se
    usan acá: el diff es siempre "de alta" (`from: None`)."""
    diff: list[dict] = [
        {"field": "safe", "from": None, "to": changes["safe_name"]},
        {"field": "plataforma", "from": None, "to": changes["platform_id"]},
        {"field": "usuario", "from": None, "to": changes["user_name"]},
        {"field": "address", "from": None, "to": changes["address"]},
    ]
    if changes.get("name"):
        diff.append({"field": "nombre", "from": None, "to": changes["name"]})
    for k, v in (changes.get("properties") or {}).items():
        if v:
            diff.append({"field": f"props.{k}", "from": None, "to": v})

    if changes.get("automatic_management_enabled") is False:
        reason = changes.get("manual_management_reason") or ""
        diff.append(
            {
                "field": "gestión automática",
                "from": None,
                "to": f"deshabilitada — {reason}" if reason else "deshabilitada",
            }
        )
    remote = str(changes.get("remote_machines") or "").strip()
    if remote:
        diff.append({"field": "remote machine access", "from": None, "to": remote})
        if changes.get("access_restricted_to_remote_machines"):
            diff.append({"field": "acceso restringido a estas máquinas", "from": None, "to": True})

    warnings: list[str] = []
    if changes.get("secret"):
        # nunca el valor real, ni acá ni en ningún log/audit.
        diff.append({"field": "contraseña", "from": None, "to": "(definida)"})
    else:
        warnings.append(
            "la cuenta se crea sin secreto (pendiente); vincule una reconcile "
            "account y ejecute Reconcile para fijarlo"
        )

    effect = (
        f"Se crea la cuenta **{changes['user_name']}@{changes['address']}** en la "
        f"safe **{changes['safe_name']}** con la plataforma **{changes['platform_id']}**."
    )
    return Plan(patch=[], diff=diff, warnings=warnings, object_count=1, effect=effect)


# --- Fase 4: onboarding masivo por CSV (account.create.bulk) ------------ #
# Ver docs/specs/done/account-bulk-onboard.md. Mismas columnas/semántica que el
# wizard de account.create de a una — cada fila del CSV es un `changes`
# compatible con `build_account_create_body`/`plan_account_create`.

_BULK_REQUIRED_COLUMNS = ("safe_name", "platform_id", "user_name", "address")
_BULK_KNOWN_COLUMNS = {
    *_BULK_REQUIRED_COLUMNS,
    "name",
    "secret",
    "automatic_management_enabled",
    "manual_management_reason",
    "remote_machines",
    "access_restricted_to_remote_machines",
}
_BULK_TRUE_VALUES = {"true", "1", "si", "sí", "yes"}


def _bulk_parse_bool(raw: str) -> bool:
    return raw.strip().lower() in _BULK_TRUE_VALUES


def _bulk_parse_row(raw: dict[str, str | None]) -> dict[str, Any]:
    """Una fila ya leída por `csv.DictReader` -> `changes` compatible con
    `AccountCreateChanges`. Levanta `ValueError` (mensaje para el operador)
    si la fila es inválida — quien llama la captura por fila, no aborta el
    CSV entero."""
    for col in _BULK_REQUIRED_COLUMNS:
        if not (raw.get(col) or "").strip():
            raise ValueError(f"falta la columna obligatoria '{col}'")

    changes: dict[str, Any] = {
        "safe_name": (raw["safe_name"] or "").strip(),
        "platform_id": (raw["platform_id"] or "").strip(),
        "user_name": (raw["user_name"] or "").strip(),
        "address": (raw["address"] or "").strip(),
        "automatic_management_enabled": True,
        "access_restricted_to_remote_machines": False,
        "properties": {},
    }
    if (raw.get("name") or "").strip():
        changes["name"] = raw["name"].strip()  # type: ignore[union-attr]
    if raw.get("secret"):
        changes["secret"] = raw["secret"]
    if (raw.get("automatic_management_enabled") or "").strip():
        changes["automatic_management_enabled"] = _bulk_parse_bool(
            raw["automatic_management_enabled"]  # type: ignore[arg-type]
        )
    if (raw.get("manual_management_reason") or "").strip():
        changes["manual_management_reason"] = raw["manual_management_reason"].strip()  # type: ignore[union-attr]
    if not changes["automatic_management_enabled"] and not changes.get("manual_management_reason"):
        raise ValueError(
            "'manual_management_reason' es obligatoria si automatic_management_enabled es false"
        )
    if (raw.get("remote_machines") or "").strip():
        changes["remote_machines"] = raw["remote_machines"].strip()  # type: ignore[union-attr]
    if (raw.get("access_restricted_to_remote_machines") or "").strip():
        changes["access_restricted_to_remote_machines"] = _bulk_parse_bool(
            raw["access_restricted_to_remote_machines"]  # type: ignore[arg-type]
        )

    for key, value in raw.items():
        if key is None or key in _BULK_KNOWN_COLUMNS or not value:
            continue
        changes["properties"][key] = value
    return changes


def parse_bulk_csv(content: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parsea un CSV de onboarding masivo. Devuelve `(rows_ok, rows_error)`:
    cada `rows_ok` es un `changes`-dict (con `_row`, la línea del CSV —
    2-based, la 1 es el header) listo para `plan_account_create`/
    `build_account_create_body`; cada `rows_error` es
    `{"_row": N, "error": "..."}`. Puro — no valida contra CyberArk (eso lo
    hace el `fetch` del endpoint, que también necesita I/O)."""
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        return [], [{"_row": 1, "error": "el CSV está vacío o no tiene encabezado"}]

    missing = [c for c in _BULK_REQUIRED_COLUMNS if c not in reader.fieldnames]
    if missing:
        return [], [{"_row": 1, "error": f"faltan columnas obligatorias: {', '.join(missing)}"}]

    rows_ok: list[dict[str, Any]] = []
    rows_error: list[dict[str, Any]] = []
    for i, raw_row in enumerate(reader, start=2):
        try:
            row = _bulk_parse_row(raw_row)
            row["_row"] = i
            rows_ok.append(row)
        except ValueError as e:
            rows_error.append({"_row": i, "error": str(e)})
    return rows_ok, rows_error


# --- Fase 3: borrar una cuenta (account.delete) ------------------------- #

_DELETE_FIELDS = (
    ("name", "nombre"),
    ("userName", "usuario"),
    ("address", "address"),
    ("safeName", "safe"),
    ("platformId", "plataforma"),
)


def build_account_delete_plan(current: dict, _changes: dict) -> Plan:
    """`current` = el objeto de `client.get_account(account_id)`. Diff = la
    cuenta completa (campos visibles) `from -> None`. `confirm_phrase` =
    `current['name']` (el nombre real de la cuenta en CyberArk)."""
    diff: list[dict] = [
        {"field": label, "from": current.get(key), "to": None}
        for key, label in _DELETE_FIELDS
        if current.get(key)
    ]
    name = str(current.get("name") or "")
    effect = (
        f"Se borra la cuenta **{name}**; se guarda una copia de seguridad "
        "completa (incluida la contraseña actual) antes de borrar."
    )
    return Plan(
        patch=[], diff=diff, warnings=[], object_count=1, effect=effect, confirm_phrase=name
    )


def plan_account_link(current: dict, changes: dict) -> Plan:
    link_type = str(changes.get("link_type") or "logon")
    idx = LINK_INDEX.get(link_type, 1)
    safe = str(changes.get("linked_safe") or "").strip()
    folder = str(changes.get("linked_folder") or "Root").strip() or "Root"
    name = str(changes.get("linked_name") or "").strip()

    warnings: list[str] = []
    if name and name == (current.get("name") or "") and safe == (current.get("safeName") or ""):
        warnings.append("está vinculando la cuenta a sí misma.")
    sm = current.get("secretManagement") or {}
    if link_type == "reconcile" and sm.get("automaticManagementEnabled") is False:
        warnings.append(
            "la gestión automática está desactivada para esta cuenta; el CPM no va a "
            "usar la reconcile account hasta que se active."
        )

    diff = [
        {"field": "tipo", "from": None, "to": f"{_LINK_LABEL[link_type]} (índice {idx})"},
        {"field": "cuenta vinculada", "from": None, "to": name},
        {"field": "safe", "from": None, "to": safe},
        {"field": "folder", "from": None, "to": folder},
    ]
    effect = (
        f"Se vincula **{name}** (safe {safe}) como cuenta de "
        f"**{_LINK_LABEL[link_type]}** de esta cuenta."
    )
    return Plan(patch=[], diff=diff, warnings=warnings, object_count=1, effect=effect)


# --- Fase 3: restaurar una cuenta desde un backup (account.restore) ----- #


def account_restore_changes(backup_id: int, metadata: dict, secret: str) -> dict[str, Any]:
    """Arma un `changes` compatible con `plan_account_create` /
    `build_account_create_body` (Fase 2) a partir del `metadata_json` de un
    `AccountBackup` + su contraseña ya descifrada — el restore reusa tal cual
    la maquinaria de `account.create`, sin duplicar lógica."""
    changes: dict[str, Any] = {
        "safe_name": metadata.get("safeName") or "",
        "platform_id": metadata.get("platformId") or "",
        "user_name": metadata.get("userName") or "",
        "address": metadata.get("address") or "",
        "name": metadata.get("name") or None,
        "secret": secret,
        "properties": {
            k: str(v) for k, v in (metadata.get("platformAccountProperties") or {}).items()
        },
        "automatic_management_enabled": True,
        "manual_management_reason": None,
        "remote_machines": None,
        "access_restricted_to_remote_machines": False,
    }
    sm = metadata.get("secretManagement") or {}
    if sm.get("automaticManagementEnabled") is False:
        changes["automatic_management_enabled"] = False
        changes["manual_management_reason"] = (
            f"restaurado desde el backup #{backup_id} — la gestión automática "
            "ya estaba deshabilitada antes del borrado"
        )
    remote = (metadata.get("remoteMachinesAccess") or {}).get("remoteMachines")
    if remote:
        changes["remote_machines"] = remote
        changes["access_restricted_to_remote_machines"] = bool(
            (metadata.get("remoteMachinesAccess") or {}).get("accessRestrictedToRemoteMachines")
        )
    return changes


# --- Fase 3: mover una cuenta de Safe (account.move, workaround) -------- #


def build_account_move_plan(current: dict, changes: dict) -> Plan:
    """`current` = el objeto de `client.get_account(account_id)`. El
    endpoint (`app.api.writes.account_move`) ya validó en su `fetch` que el
    safe destino existe y es distinto del actual — acá sólo se arma el
    diff/warnings. `confirm_phrase` = `current['name']`, mismo criterio que
    `build_account_delete_plan` (esto SÍ borra la cuenta original; el
    resultado neto es "moverla", pero el riesgo real es el de un delete).

    Los `warnings` son la traducción literal del diálogo "Move Account" que
    muestra Privilege Cloud en su Accounts View (Classic UI) — confirmado
    contra una captura real (2026-09-22), no inferido. Confirma algo
    importante: el Move NATIVO de CyberArk también pierde versiones de
    contraseña e historial de actividad (puntos 5/6) — no es una limitación
    exclusiva de este workaround por clonación, es inherente a cómo
    CyberArk modela un "Move" internamente."""
    dest_safe = str(changes.get("safe_name") or "")
    origin_safe = str(current.get("safeName") or "")
    name = str(current.get("name") or "")

    diff = [{"field": "safe", "from": origin_safe, "to": dest_safe}]
    warnings = [
        "Si un CPM distinto gestiona la safe destino, debe tener las políticas correspondientes.",
        'La safe destino debe estar incluida en el parámetro "AllowedSafes" de las '
        "políticas de cuenta correspondientes.",
        "Los vínculos (Logon/Reconcile) a la cuenta deben recrearse.",
        "Los permisos a nivel de cuenta (OLAC) deben recrearse según los miembros de "
        "la safe nueva, si aplica.",
        "Las versiones antiguas de la contraseña no se mueven — quedan en la cuenta "
        "original, que se borra.",
        "El historial de actividad de la cuenta no se mueve.",
        "Las solicitudes de acceso existentes sobre la cuenta van a expirar.",
        "Las contraseñas de un solo uso ya utilizadas y aún no cambiadas no se van a "
        "cambiar antes de volver a usarse.",
        "Los permisos de comandos a nivel de cuenta (usados por OPM) deben recrearse "
        "según los miembros de la safe nueva, si aplica.",
    ]
    effect = (
        f"Se crea una copia de **{name}** en la safe **{dest_safe}** y se borra la "
        f"original en **{origin_safe}** (se guarda un backup cifrado antes de borrar)."
    )
    return Plan(
        patch=[],
        diff=diff,
        warnings=warnings,
        object_count=1,
        effect=effect,
        confirm_phrase=name,
    )


# --- estado de una cuenta en la grilla de Cuentas ------------------------ #

ACCOUNT_STATUS_FLAGS = ("failure", "manual", "cpm_disabled")


def account_status_flags(acc: dict) -> set[str]:
    """Marcas de estado de una cuenta, tal como las pinta la grilla de
    Cuentas: `failure` (el CPM falló en la última operación), `manual`
    (gestión automática deshabilitada por un usuario), `cpm_disabled` (la
    deshabilitó el propio CPM al agotar los reintentos fallidos -- motivo con
    prefijo `(CPM)`, ej. `(CPM)MaxRetries`, mismo criterio que
    `app.reports.non_rotating_accounts`). `manual` y `cpm_disabled` son
    excluyentes. Una cuenta puede
    tener varias; ninguna = en orden. Puro."""
    sm = acc.get("secretManagement") or {}
    flags: set[str] = set()
    if str(sm.get("status") or "").lower() in ("failure", "failed"):
        flags.add("failure")
    if sm.get("automaticManagementEnabled") is False:
        reason = str(sm.get("manualManagementReason") or "")
        flags.add("cpm_disabled" if reason.startswith("(CPM)") else "manual")
    return flags


# --- Búsqueda por remote machines (cuentas de dominio) ------------------- #

_SEARCH_SPLIT = re.compile(r"[,;\n]+")
_RM_SPLIT = re.compile(r"[;,\s]+")


def parse_search_terms(search: str | None) -> list[str]:
    """Separa la barra de búsqueda en términos por coma, punto y coma o salto
    de línea (nunca por espacio: un término puede llevar espacios). Sirve
    para pegar una lista de address de un ticket; deduplica sin
    distinguir mayúsculas y preserva el orden."""
    seen: dict[str, str] = {}
    for part in _SEARCH_SPLIT.split(search or ""):
        term = part.strip()
        if term:
            seen.setdefault(term.lower(), term)
    return list(seen.values())


def account_remote_machines(raw: dict) -> list[str]:
    """Las remote machines de una cuenta (`remoteMachinesAccess.remoteMachines`,
    que CyberArk guarda como un string separado por `;`)."""
    rma = raw.get("remoteMachinesAccess") or {}
    value = rma.get("remoteMachines") if isinstance(rma, dict) else None
    return [m for m in _RM_SPLIT.split(str(value or "")) if m]


def account_matches_remote_machines(raw: dict, terms: list[str], *, exact: bool) -> bool:
    """¿Alguna remote machine de la cuenta coincide con algún término?
    `exact` compara la máquina completa (sin distinguir mayúsculas);
    si no, alcanza con que contenga el término."""
    machines = [m.lower() for m in account_remote_machines(raw)]
    for term in (t.lower() for t in terms):
        if exact:
            if term in machines:
                return True
        elif any(term in m for m in machines):
            return True
    return False


def account_matches_terms(
    raw: dict, terms: list[str], *, exact: bool, include_remote: bool = True
) -> bool:
    """Búsqueda sobre la copia local: nombre, usuario, address, id, safe,
    plataforma y remote machines. `exact` (modo lista, varios términos)
    exige igualdad completa para que `203.0.113.11` no traiga
    `203.0.113.110` — pensado para armar un lote a borrar. Con
    `include_remote=False` mira sólo los campos propios de la cuenta."""
    fields = [
        raw.get("name"),
        raw.get("userName"),
        raw.get("address"),
        raw.get("id"),
        raw.get("safeName"),
        raw.get("platformId"),
    ]
    values = [str(f).lower() for f in fields if f]
    for term in (t.lower() for t in terms):
        if exact:
            if term in values:
                return True
        elif any(term in v for v in values):
            return True
    return include_remote and account_matches_remote_machines(raw, terms, exact=exact)
