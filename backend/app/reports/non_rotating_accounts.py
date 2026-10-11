"""Cuentas que no están rotando -- por qué, y el motivo real del último
error del CPM. Ver `docs/specs/done/non-rotating-accounts.md`.

Pedido de un cliente (2026-09-28): una sola vista de TODAS las cuentas que
no rotan (error del CPM, gestión deshabilitada, vencidas contra la política
de su plataforma, bloqueadas...), personalizable al generarla -- p. ej.
sin las cuentas con gestión automática deshabilitada a propósito ni las
llaves SSH, que tampoco rotan y no deben contarse.

Mismo criterio de "vencida" que el Dashboard y la sección Compliance
(`analysis.account_compliance` -- umbral de LA PLATAFORMA de la cuenta, o
`Tenant.compliance_stale_days` si no define uno), así el número de este
reporte coincide con el del Dashboard. Las categorías de CyberArk
(PolicyFailures/DisabledPasswordByUser/ByCPM/Locked) salen de los mismos
savedFilters que ya usa Compliance.

`heavy=True`: el motivo real del último error sale de `Activities`, un
request por cuenta (mismo fan-out que `account-inventory-full`). Sólo se
consulta para las cuentas del RESULTADO con alguna señal de falla del CPM
(status Failure o deshabilitada por el CPM), no para todo el tenant; con
`incluir_ultimo_error=No` no se consulta nada y el job termina en segundos.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app import analysis
from app.cyberark import CyberArkClient
from app.reports._activities import all_account_activities
from app.reports.account_inventory import last_cpm_failure
from app.reports.base import Column, Param, Report, ReportResult, multi_values

# motivo (código estable, viaja en la query) -> etiqueta legible
REASON_LABELS: dict[str, str] = {
    "CpmFailure": "Error de rotación/verificación (CPM)",
    "PolicyFailures": "Incumple la política (CyberArk)",
    "ActiveOverdue": "Gestionada pero vencida",
    "DisabledStale": "Gestión deshabilitada y vencida",
    "DisableByUser": "Gestión deshabilitada por un usuario",
    "DisableByCPM": "Gestión deshabilitada por el CPM",
    "Locked": "Bloqueada",
}

EXCLUDE_LABELS: dict[str, str] = {
    "cyberark_internal": "Cuentas internas de CyberArk y de componentes (PSM, CPM...)",
    "user_disabled": "Gestión automática deshabilitada (por un usuario)",
    "cpm_disabled": "Gestión automática deshabilitada por el CPM",
    "ssh_keys": "Llaves SSH",
}

# Cuentas que crea y usa el propio CyberArk (no son credenciales del
# cliente): usuarios de componentes -- PSM, CPM, PVWA -- y cuentas guardadas
# en safes internos del producto. Pedido de Alex (2026-09-28), probando el
# reporte contra Globex: `PluginManagerUser`, "Operating System",
# `PSMConnect`/`PSMAdminConnect` aparecían como "no rotan". Comparación sin
# distinguir mayúsculas; los prefijos cubren las variantes por host
# (`PSMConnect_<host>`, `PSMApp_<host>`, ...).
_INTERNAL_USER_PREFIXES = (
    "pluginmanageruser",
    "psmconnect",
    "psmadminconnect",
    "psmapp_",
    "psmgw_",
    "psmpapp_",
    "psmpgw_",
    "pvwaappuser",
    "pvwagwuser",
    "pvwagwaccounts",
    "passwordmanager",
)
_INTERNAL_SAFES = {
    s.lower()
    for s in (
        "System",
        "VaultInternal",
        "Notification Engine",
        "SharedAuth_Internal",
        "TelemetryConfig",
        "AppProviderCacheSafe",
        "AccountsFeed",
        "AccountsFeedADAccounts",
        "AccountsFeedDiscoveryLogs",
        "PSM",
        "PSMSessions",
        "PSMLiveSessions",
        "PSMNotifications",
        "PSMRecordings",
        "PSMUniversalConnectors",
        "PSMUnmanagedSessionAccounts",
        "PSMPConf",
        "PSMPADBridgeConf",
        "PSMPADBUserProfile",
        "PSMPADBridgeCustom",
        "PIMSuRecordings",
        "PIMSuSessions",
    )
}
# safes con variantes (`PasswordManager_Pending`, `PVWAConfig`, ...)
_INTERNAL_SAFE_PREFIXES = ("passwordmanager", "pvwa")

# savedFilter real de CyberArk -> motivo de este reporte
_SAVED_FILTERS: dict[str, str] = {
    "PolicyFailures": "PolicyFailures",
    "DisabledPasswordByUser": "DisableByUser",
    "DisabledPasswordByCPM": "DisableByCPM",
    "Locked": "Locked",
}

_FAILURE_KIND_LABELS = {
    "change": "Cambio",
    "verify": "Verificación",
    "reconcile": "Reconciliación",
}

_COLUMNS = [
    Column("id", "Account ID"),
    Column("name", "Nombre"),
    Column("userName", "Usuario"),
    Column("address", "Address"),
    Column("platformId", "Plataforma"),
    Column("safeName", "Safe"),
    Column("secretType", "Tipo de secreto"),
    Column("autoManaged", "Gestión automática", "bool"),
    Column("cpmStatus", "Estado CPM"),
    Column("reasons", "Motivo(s)"),
    Column("manualReason", "Motivo (si manual)"),
    Column("lastModified", "Último cambio", "date"),
    Column("ageDays", "Días sin rotar", "number"),
    Column("thresholdDays", "Umbral (días)", "number"),
    Column("lastErrorKind", "Tipo de la última falla"),
    Column("lastErrorDate", "Fecha de la última falla", "date"),
    Column("lastErrorReason", "Motivo del último error"),
]


def is_cpm_failure(sm: dict) -> bool:
    return str(sm.get("status") or "").strip().lower() in ("failure", "failed")


def is_cpm_disabled(acc_id: Any, sm: dict, cpm_disabled_ids: set) -> bool:
    """Deshabilitada por el CPM (tras agotar reintentos o un error sin
    reintento): savedFilter `DisabledPasswordByCPM`, o el prefijo `(CPM)`
    en `manualManagementReason` (`(CPM)MaxRetries`/`(CPM)UnrecoverableError`)
    por si el savedFilter no la trajo."""
    if sm.get("automaticManagementEnabled"):
        return False
    reason = str(sm.get("manualManagementReason") or "")
    return acc_id in cpm_disabled_ids or reason.startswith("(CPM)")


def is_cyberark_internal(acc: dict) -> bool:
    """Cuenta de un componente o del propio CyberArk -- por usuario
    (`PluginManagerUser`, `PSMConnect`, `PSMApp_*`...) o por estar en un
    safe interno del producto (`PasswordManager*`, `PVWA*`, `PSM*`...)."""
    user = str(acc.get("userName") or "").strip().lower()
    if user and user.startswith(_INTERNAL_USER_PREFIXES):
        return True
    safe = str(acc.get("safeName") or "").strip().lower()
    return safe in _INTERNAL_SAFES or safe.startswith(_INTERNAL_SAFE_PREFIXES)


def compute_non_rotating(
    accounts: list[dict],
    *,
    filter_ids: dict[str, set],
    platform_days: dict[str, int],
    default_stale_days: int,
    reasons: set[str] | None = None,
    exclude: set[str] | None = None,
    safe_name: str | None = None,
    platform_id: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Filas de las cuentas que no rotan, ya filtradas, + meta con los
    conteos de lo excluido. `filter_ids`: motivo (`PolicyFailures`/
    `DisableByUser`/`DisableByCPM`/`Locked`) -> set de ids del savedFilter
    correspondiente. `reasons` vacío/None = todos los motivos."""
    reasons = reasons or set(REASON_LABELS)
    exclude = exclude or set()
    safe_filter = (safe_name or "").strip().lower()
    platform_filter = (platform_id or "").strip().lower()
    cpm_disabled_ids = filter_ids.get("DisableByCPM", set())

    rows: list[dict[str, Any]] = []
    excluded = dict.fromkeys(EXCLUDE_LABELS, 0)
    for acc in accounts:
        acc_id = acc.get("id")
        sm = acc.get("secretManagement") or {}
        if safe_filter and str(acc.get("safeName") or "").lower() != safe_filter:
            continue
        if platform_filter and str(acc.get("platformId") or "").lower() != platform_filter:
            continue

        status = analysis.account_compliance(
            acc,
            is_policy_failure=acc_id in filter_ids.get("PolicyFailures", set()),
            default_stale_days=default_stale_days,
            platform_days=platform_days,
            # este reporte quiere ver las llaves SSH como "no rotan" (es su
            # propósito) y las excluye a pedido con su propio filtro
            # `ssh_keys` -- a diferencia del % de Compliance, que siempre
            # las exime (ver `analysis._is_rotation_exempt`).
            ssh_keys_exempt=False,
        )
        acc_reasons: list[str] = []
        if is_cpm_failure(sm):
            acc_reasons.append("CpmFailure")
        acc_reasons.extend(status["reasons"])
        for code in ("DisableByUser", "DisableByCPM", "Locked"):
            if acc_id in filter_ids.get(code, set()):
                acc_reasons.append(code)
        if not acc_reasons:
            continue  # rota bien: no es parte de este reporte

        # exclusiones: se cuentan sólo entre las que SÍ entrarían al reporte
        is_ssh_key = str(acc.get("secretType") or "").lower() == "key"
        auto = bool(sm.get("automaticManagementEnabled"))
        cpm_disabled = is_cpm_disabled(acc_id, sm, cpm_disabled_ids)
        if "cyberark_internal" in exclude and is_cyberark_internal(acc):
            excluded["cyberark_internal"] += 1
            continue
        if "ssh_keys" in exclude and is_ssh_key:
            excluded["ssh_keys"] += 1
            continue
        if "cpm_disabled" in exclude and cpm_disabled:
            excluded["cpm_disabled"] += 1
            continue
        if "user_disabled" in exclude and not auto and not cpm_disabled:
            excluded["user_disabled"] += 1
            continue

        if not reasons.intersection(acc_reasons):
            continue

        rows.append(
            {
                "id": acc_id,
                "name": acc.get("name"),
                "userName": acc.get("userName"),
                "address": acc.get("address"),
                "platformId": acc.get("platformId"),
                "safeName": acc.get("safeName"),
                "secretType": acc.get("secretType"),
                "autoManaged": auto,
                "cpmStatus": sm.get("status"),
                "reasons": "; ".join(REASON_LABELS[r] for r in acc_reasons),
                "reasonCodes": acc_reasons,
                "manualReason": sm.get("manualManagementReason"),
                "lastModified": sm.get("lastModifiedTime") or sm.get("lastModifiedDateTime"),
                "ageDays": status["age_days"],
                "thresholdDays": status["threshold_days"],
                "lastErrorKind": None,
                "lastErrorDate": None,
                "lastErrorReason": None,
            }
        )

    meta: dict[str, Any] = {"cuentas_analizadas": len(accounts), "no_rotan": len(rows)}
    for code in exclude:
        if code in excluded:
            meta[f"excluidas_{code}"] = excluded[code]
    return rows, meta


def needs_last_error(row: dict[str, Any]) -> bool:
    """Sólo tiene sentido buscar el último error del CPM en cuentas con una
    señal de falla del CPM -- el resto (p. ej. deshabilitada a mano y
    vencida) no tiene un error que mostrar, y consultarlas sería un
    request a CyberArk por cuenta sin ningún beneficio."""
    codes = row.get("reasonCodes") or []
    return "CpmFailure" in codes or "DisableByCPM" in codes


def attach_last_errors(
    rows: list[dict[str, Any]],
    activities_by_id: dict[str, list | None],
) -> int:
    """Completa las columnas del último error con `last_cpm_failure`.
    Devuelve cuántas cuentas no se pudieron consultar (Activities falló)."""
    errors = 0
    for row in rows:
        acc_id = str(row.get("id") or "")
        if acc_id not in activities_by_id:
            continue
        acts = activities_by_id[acc_id]
        if acts is None:
            errors += 1
            continue
        failure = last_cpm_failure(acts)
        if failure:
            row["lastErrorKind"] = _FAILURE_KIND_LABELS.get(failure["kind"], failure["kind"])
            row["lastErrorDate"] = failure["date"]
            row["lastErrorReason"] = failure["reason"]
    return errors


async def _run(client: CyberArkClient, params: dict[str, Any]) -> ReportResult:
    reasons = multi_values(params, "motivos", list(REASON_LABELS))
    exclude = multi_values(params, "excluir", list(EXCLUDE_LABELS))
    with_last_error = str(params.get("incluir_ultimo_error") or "Sí") != "No"
    default_stale_days = int(
        params.get("_default_stale_days") or analysis.DEFAULT_DISABLED_STALE_DAYS
    )

    saved_filters = list(_SAVED_FILTERS)
    accounts, platforms, *filtered = await asyncio.gather(
        client.list_accounts(),
        client.list_platforms(),
        *(client.saved_filter_accounts(f) for f in saved_filters),
    )
    filter_ids = {
        _SAVED_FILTERS[f]: {a.get("id") for a in accs}
        for f, accs in zip(saved_filters, filtered, strict=True)
    }

    rows, meta = compute_non_rotating(
        accounts,
        filter_ids=filter_ids,
        platform_days=analysis.platform_policy_days(platforms),
        default_stale_days=default_stale_days,
        reasons=reasons,
        exclude=exclude,
        safe_name=params.get("safe_name"),
        platform_id=params.get("platform_id"),
    )

    if with_last_error:
        to_check = [r for r in rows if needs_last_error(r)]
        activities = await all_account_activities(client, to_check)
        meta["ultimo_error_consultado"] = len(to_check)
        errors = attach_last_errors(rows, activities)
        if errors:
            meta["ultimo_error_sin_datos"] = errors

    for r in rows:
        r.pop("reasonCodes", None)
    return ReportResult(columns=_COLUMNS, rows=rows, meta=meta)


report = Report(
    name="non-rotating-accounts",
    category="Cuentas",
    label="Cuentas que no rotan",
    description=(
        "Cuentas con error del CPM, gestión deshabilitada, vencidas contra la política de su "
        "plataforma o bloqueadas, con el motivo real del último error. Filtrable por motivo, "
        "exclusiones (cuentas internas de CyberArk, gestión deshabilitada, llaves SSH), "
        "safe y plataforma."
    ),
    run=_run,
    heavy=True,
    params=[
        Param(
            "motivos",
            "Motivos",
            "multiselect",
            options=list(REASON_LABELS),
            option_labels=REASON_LABELS,
            help="sin selección, todos",
        ),
        Param(
            "excluir",
            "Excluir",
            "multiselect",
            options=list(EXCLUDE_LABELS),
            option_labels=EXCLUDE_LABELS,
            help="sin selección, no se excluye nada",
        ),
        Param("safe_name", "Safe", "text", help="opcional — nombre exacto"),
        Param("platform_id", "Plataforma", "text", help="opcional — Platform ID exacto"),
        Param(
            "incluir_ultimo_error",
            "Motivo del último error",
            "select",
            options=["Sí", "No"],
            help="Sí (por defecto)",
        ),
    ],
    tenant_params=lambda tenant: {"_default_stale_days": tenant.compliance_stale_days},
)
