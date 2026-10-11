"""Lógica de dominio de plataformas — reasignar el PSM (Fase 3).

Puro, sin I/O. Ver docs/specs/platform-psm-migration.md — el `PUT` real de
CyberArk reemplaza el objeto de gestión de sesiones COMPLETO al guardar
(bug conocido: mandar sólo `{PSMServerId, PSMServerName}` borra
`PSMConnectors`/Connection Components, ej. "Override User Parameters" de
cuentas de dominio). El patrón GET-completo → mutar sólo 2 campos → PUT →
releer y verificar → rollback automático si algo más cambió viene de
`Platform_PSM_Migration.ps1` (ya probado en producción).
"""

from __future__ import annotations

from collections.abc import Callable

from app.core.writeop import Plan


def find_target_platform(targets: list, platform_id: str) -> dict | None:
    """Busca una plataforma por `PlatformID` en la lista de
    `client.list_target_platforms()` — esa lista trae el ID NUMÉRICO
    interno que exige la API de PSM ("Get target platforms", distinto del
    `GET /API/Platforms` genérico que ya usa el resto de PAM Web Console)."""
    for p in targets:
        if isinstance(p, dict) and str(p.get("PlatformID") or "") == platform_id:
            return p
    return None


def normalize_psm(obj: dict) -> dict:
    """Copia de un objeto `PrivilegedSessionManagement` con
    `PSMServerId`/`PSMServerName` anulados — para comparar si CyberArk
    modificó algo MÁS que esos dos campos al guardar."""
    clone = dict(obj)
    clone["PSMServerId"] = None
    clone["PSMServerName"] = None
    return clone


def plan_platform_psm(platform_id: str) -> Callable[[dict, dict], Plan]:
    """`current` = el objeto `PrivilegedSessionManagement` completo de la
    plataforma (ya resuelto por el `fetch` del endpoint). `changes` =
    `{psm_server_id, psm_server_name}`. `confirm_phrase` = `platform_id`:
    esto afecta a TODAS las cuentas de esa plataforma."""

    def _build(current: dict, changes: dict) -> Plan:
        cur_id = current.get("PSMServerId")
        new_id = str(changes.get("psm_server_id") or "")
        if not new_id or new_id == cur_id:
            return Plan(patch=[], diff=[], warnings=[], object_count=1, confirm_phrase=platform_id)

        diff = [{"field": "PSM", "from": cur_id, "to": new_id}]
        effect = f"Se reasigna la plataforma **{platform_id}** al PSM **{new_id}**."
        warnings = [
            "CyberArk reemplaza el objeto de gestión de sesiones COMPLETO al guardar — hay "
            "un bug conocido donde eso borra PSMConnectors / Connection Components (ej. "
            "Override User Parameters de cuentas de dominio). PAM Web Console parte siempre del objeto "
            "actual completo y verifica después del cambio que nada más se haya modificado; "
            "si detecta algo distinto, revierte automáticamente al original."
        ]
        return Plan(
            patch=[],
            diff=diff,
            warnings=warnings,
            object_count=1,
            effect=effect,
            confirm_phrase=platform_id,
        )

    return _build


# --- activar / desactivar / duplicar / borrar (Fase 3) ------------------- #
# Ver docs/specs/platform-lifecycle.md. Endpoints verificados contra el
# módulo open-source `psPAS` (docs.cyberark.com bloqueó el fetch
# automatizado para estas páginas puntuales).


def plan_platform_activation(platform_id: str, target_active: bool) -> Callable[[dict, dict], Plan]:
    """`current` = el objeto target-platform completo (trae `Active`).
    Factory igual a `plan_cpm(action)`: activar/desactivar afecta a TODAS
    las cuentas de la plataforma, así que va con `confirm_phrase` aunque
    sea reversible (mismo techo que `plan_platform_psm`)."""

    def _build(current: dict, _changes: dict) -> Plan:
        cur_active = bool(current.get("Active"))

        if cur_active == target_active:
            # sin `effect`: así `run_write` lo trata como no-op real (mismo
            # criterio que `plan_platform_psm`) y el frontend muestra "sin
            # cambios" en vez de pedir confirmar un no-op.
            return Plan(patch=[], diff=[], warnings=[], object_count=1, confirm_phrase=platform_id)

        diff = [{"field": "active", "from": cur_active, "to": target_active}]
        if target_active:
            effect = f"Se activa la plataforma **{platform_id}**."
            warnings = [
                "el CPM retoma la gestión (rotación/verificación) de todas las cuentas "
                "de esta plataforma."
            ]
        else:
            effect = f"Se desactiva la plataforma **{platform_id}**."
            warnings = [
                "el CPM deja de rotar/verificar todas las cuentas de esta plataforma "
                "hasta que se reactive."
            ]
        return Plan(
            patch=[],
            diff=diff,
            warnings=warnings,
            object_count=1,
            effect=effect,
            confirm_phrase=platform_id,
        )

    return _build


def build_platform_duplicate_plan(current: dict, changes: dict) -> Plan:
    """Alta de una copia de la plataforma — riesgo bajo (no toca la
    plataforma origen ni ninguna cuenta), sin `confirm_phrase`, mismo
    criterio que `plan_account_create`/`plan_safe_create`. `current` = el
    objeto target-platform origen (sólo se usa para el nombre en el
    `effect`); `changes = {name, description?}`. `name` no vacío y
    distinto del origen ya lo valida el endpoint antes de llegar acá (ver
    `app.api.writes.platform_duplicate`)."""
    origin = str(current.get("PlatformID") or "")
    name = str(changes.get("name") or "").strip()
    effect = f"Se crea la plataforma **{name}**, copia de **{origin}**."
    return Plan(patch=[], diff=[], warnings=[], object_count=1, effect=effect)


def plan_platform_import(_current: dict, changes: dict) -> Plan:
    """`_current` no se usa (es una alta, como `plan_platform_duplicate`).
    `changes = {"filename": str, "size_bytes": int}` — NUNCA el contenido
    real del archivo (evita mandarlo dos veces y que quede en el audit).
    Sin `confirm_phrase` (riesgo bajo, mismo criterio que
    `plan_platform_duplicate`). No hay diff real posible: CyberArk no
    expone el contenido de un `.zip` sin importarlo, así que el "preview"
    es sólo el nombre/tamaño del archivo, no un diff de campos."""
    filename = str(changes.get("filename") or "el archivo")
    size_kb = round(int(changes.get("size_bytes") or 0) / 1024, 1)
    effect = (
        f"Se importa una plataforma nueva desde **{filename}** ({size_kb} KB) — "
        "CyberArk decide el resultado según el contenido del paquete, no hay "
        "preview real de qué plataforma se crea."
    )
    return Plan(patch=[], diff=[], warnings=[], object_count=1, effect=effect)


_PLATFORM_DELETE_FIELDS = (
    ("PlatformID", "PlatformID"),
    ("Name", "Nombre"),
    ("SystemType", "Tipo de sistema"),
    ("Active", "Activa"),
)


def build_platform_delete_plan(current: dict, _changes: dict) -> Plan:
    """`current` = `{"platform": {...}}` — el target-platform completo, ya
    validado sin cuentas en uso por el `fetch` del endpoint (ver
    `app.api.writes.platform_delete`). Diff curado (no el objeto crudo
    completo, que trae toda la política del CPM) — mismo criterio que
    `build_safe_delete_plan`. `confirm_phrase` = el `PlatformID` string."""
    platform = current.get("platform") or {}
    diff: list[dict] = [
        {"field": label, "from": platform.get(key), "to": None}
        for key, label in _PLATFORM_DELETE_FIELDS
        if platform.get(key) is not None
    ]
    platform_id = str(platform.get("PlatformID") or "")
    effect = (
        f"Se borra la plataforma **{platform_id}** — PAM Web Console guarda un backup del paquete de "
        "exportación de CyberArk antes de borrarla."
    )
    return Plan(
        patch=[],
        diff=diff,
        warnings=["esto no se puede deshacer desde CyberArk — sólo queda el backup del export."],
        object_count=1,
        effect=effect,
        confirm_phrase=platform_id,
    )
