"""
Cálculo de métricas para los dashboards. Funciones puras, sin I/O ni requests
— portadas de `analysis.py` de Telemetry-Reporter y recortadas a lo que
PAM Web Console puede reconstruir con Accounts + Safes + Platforms de la API de
Privilege Cloud. La forma final de la respuesta la arma `app.api.dashboards`.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

# Umbral (días) DEFAULT para considerar "no compliant" a una cuenta con la
# gestión automática deshabilitada — CyberArk NO la cuenta en su propio
# savedFilter PolicyFailures (sin gestión activa no hay política que
# incumplir), pero desde Compliance real de un cliente es exactamente el
# caso más riesgoso: una credencial sin rotar, "invisible" para el %
# nativo. Confirmado con Alex (2026-09-17) contra un caso real de Initech: la
# mayoría de sus cuentas están así, y el 99%+ de "Compliance" que mostraba
# el Dashboard (heredado 1:1 del criterio nativo de CyberArk) no reflejaba
# eso. Ajustable por tenant (`Tenant.compliance_stale_days`, default 30 —
# ver `app/api/tenants.py`); este valor sólo se usa si `analyze_accounts`
# se llama sin pasar uno explícito (tests, scripts sueltos).
DEFAULT_DISABLED_STALE_DAYS = 30

# orden canónico de los tramos de antigüedad (para los gráficos de barras)
AGE_BUCKET_LABELS = [
    "0-30 días",
    "1-2 meses",
    "2-3 meses",
    "3-6 meses",
    "6-12 meses",
    "1-2 años",
    "+2 años",
    "Nunca",
]

# variantes de secretManagement.status observadas en tenants reales
_STATUS_DISPLAY = {
    "succeeded": "Success",
    "failed": "Failure",
    "inprocess": "In Process",
    "success": "Success",
    "failure": "Failure",
}


def _to_datetime(epoch_value) -> datetime | None:
    """Epoch en segundos -> datetime UTC. None si es nulo, 0 o inválido."""
    if not epoch_value:
        return None
    try:
        return datetime.fromtimestamp(int(epoch_value), tz=UTC)
    except (ValueError, OSError, OverflowError):
        return None


def _parse_when(value) -> datetime | None:
    """Epoch (segundos o milisegundos) o ISO 8601 -> datetime UTC."""
    if isinstance(value, str) and not value.strip().lstrip("-").isdigit():
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return _to_datetime(n // 1000 if n > 10**12 else n)


def last_password_change(versions: list[dict] | None) -> datetime | None:
    """Fecha del último cambio EXITOSO de la contraseña: la versión más
    reciente de `GET /API/Accounts/{id}/Secret/Versions` (cada cambio que
    llega a la Vault genera una versión; un intento fallido del CPM, no).

    Existe porque `secretManagement.lastModifiedTime` NO sirve para esto:
    se mueve también cuando falla un intento del CPM. Caso real (Globex,
    2026-09-29): cuenta con la contraseña de hace 21 días según el PVWA,
    un cambio fallido a las 09:39 (EXT01) e PAM Web Console mostrando "0 días" y
    "último cambio hace 11 min" -- la hora exacta de la falla."""
    best: datetime | None = None
    for v in versions or []:
        dt = _parse_when(v.get("ModificationTime")) if isinstance(v, dict) else None
        if dt and (best is None or dt > best):
            best = dt
    return best


def _age_bucket(dt: datetime | None, now: datetime) -> str:
    if dt is None:
        return "Nunca"
    days = (now - dt).days
    if days <= 30:
        return "0-30 días"
    if days <= 60:
        return "1-2 meses"
    if days <= 90:
        return "2-3 meses"
    if days <= 180:
        return "3-6 meses"
    if days <= 365:
        return "6-12 meses"
    if days <= 730:
        return "1-2 años"
    return "+2 años"


def platform_policy_days(platforms: list[dict]) -> dict[str, int]:
    """`platform_id -> requirePasswordChangeEveryXDays`, sólo para
    plataformas con `performPeriodicChange` activo y un valor > 0 — el
    umbral de rotación que la PLATAFORMA misma define, distinto del
    default parejo del tenant (`Tenant.compliance_stale_days`). Campo
    confirmado vía el SDK Go de CyberArk
    (`davidh-cyberark/privilegeaccessmanager-sdk-go`, `docs.cyberark.com`
    bloqueó el fetch, mismo patrón de siempre) — **pendiente confirmar
    contra un tenant real** que el nombre/valor sea exactamente ese antes
    de confiar en él para algo más que este cruce. Las plataformas sin
    rotación periódica activa no definen "su propio" umbral — el caller
    cae al default del tenant."""
    result: dict[str, int] = {}
    for p in platforms:
        general = p.get("general") or {}
        cred = p.get("credentialsManagement") or {}
        platform_id = general.get("id", "")
        days = cred.get("requirePasswordChangeEveryXDays")
        if platform_id and cred.get("performPeriodicChange") and days:
            result[platform_id] = int(days)
    return result


def _is_rotation_exempt(
    account: dict, exempt_ids: set | None, *, ssh_keys_exempt: bool = True
) -> bool:
    """True si la cuenta nunca debe contar como vencida/no-compliant, sin
    importar hace cuánto rotó: llaves SSH (`secretType == "key"`, no rotan
    por naturaleza) o cuentas con una excepción manual activa en PAM Web Console
    (`compliance_exemptions` — gestión deshabilitada a pedido del cliente,
    para siempre; ver docs/specs/compliance-exemptions.md). CyberArk no
    registra ese motivo, así que la excepción vive en PAM Web Console, no se infiere.

    `ssh_keys_exempt=False`: para `account_compliance` cuando lo llama
    `app.reports.non_rotating_accounts` — ESE reporte quiere ver las llaves
    SSH como "no rotan" (es justamente su propósito), con su propio filtro
    opcional `ssh_keys` para excluirlas a pedido; sólo el % de Compliance
    (Dashboard/`/compliance`/ITR) las exime siempre."""
    if ssh_keys_exempt and str(account.get("secretType") or "").lower() == "key":
        return True
    return exempt_ids is not None and account.get("id") in exempt_ids


def _account_overdue(
    sm: dict,
    platform_id: str,
    platform_days: dict[str, int],
    default_days: int,
    now: datetime,
    last_changed: datetime | None = None,
) -> tuple[bool, int | None]:
    """(está_vencida, antigüedad_en_días — None si nunca rotó, siempre
    cuenta como vencida). El umbral es el de LA PLATAFORMA de la cuenta
    si la tiene definido (`platform_days`), si no el default parejo del
    tenant — mismo criterio en todo lados: `analyze_accounts`,
    `compliance_breakdown`, y las filas navegables de `app/api/
    compliance.py`."""
    threshold = platform_days.get(platform_id, default_days)
    if last_changed is None:
        last_changed = _to_datetime(sm.get("lastModifiedTime") or sm.get("lastModifiedDateTime"))
    age_days = None if last_changed is None else (now - last_changed).days
    return (age_days is None or age_days > threshold), age_days


def analyze_accounts(
    accounts: list[dict],
    disabled_stale_days: int = DEFAULT_DISABLED_STALE_DAYS,
    platform_days: dict[str, int] | None = None,
    exempt_ids: set | None = None,
) -> dict:
    """
    Métricas agregadas de la lista cruda de `/API/Accounts`:
      total, failed (status Failure), failed_ids (set), auto_managed,
      never_changed, no_platform, status_counts, by_platform (Counter),
      age_change (Counter por tramo), disabled_stale (gestión deshabilitada
      + vencida contra la política de SU plataforma —`platform_days`— o el
      default del tenant si no define una), active_overdue (gestión
      ACTIVA pero igual vencida — mismo caso que el ejemplo real del PVWA:
      cuenta gestionada, 334 días sin rotar, "Not compliant"; CyberArk lo
      detecta con su propio `PolicyFailures`, esto es la verificación
      independiente de PAM Web Console contra el mismo umbral que ve el operador).
    """
    total = len(accounts)
    status_counts: Counter = Counter()
    by_platform: Counter = Counter()
    age_change: Counter = Counter()
    failed_ids: set = set()
    auto_managed = 0
    never_changed = 0
    no_platform = 0
    disabled_stale = 0
    active_overdue = 0
    now = datetime.now(UTC)
    platform_days = platform_days or {}

    for acc in accounts:
        sm = acc.get("secretManagement") or {}
        raw = (sm.get("status") or "").strip()
        status = _STATUS_DISPLAY.get(raw.lower(), raw or "Desconocido")
        status_counts[status] += 1
        if status == "Failure":
            failed_ids.add(acc.get("id"))
        auto_enabled = bool(sm.get("automaticManagementEnabled"))
        if auto_enabled:
            auto_managed += 1

        platform_id = acc.get("platformId") or ""
        if platform_id:
            by_platform[platform_id] += 1
        else:
            no_platform += 1

        last_changed = _to_datetime(sm.get("lastModifiedTime") or sm.get("lastModifiedDateTime"))
        if last_changed is None:
            never_changed += 1
        age_change[_age_bucket(last_changed, now)] += 1

        overdue, _ = _account_overdue(sm, platform_id, platform_days, disabled_stale_days, now)
        if overdue and not _is_rotation_exempt(acc, exempt_ids):
            if auto_enabled:
                active_overdue += 1
            else:
                disabled_stale += 1

    return {
        "total": total,
        "failed": len(failed_ids),
        "failed_ids": failed_ids,
        "auto_managed": auto_managed,
        "never_changed": never_changed,
        "no_platform": no_platform,
        "status_counts": status_counts,
        "by_platform": by_platform,
        "age_change": age_change,
        "disabled_stale": disabled_stale,
        "active_overdue": active_overdue,
    }


def analyze_safes(safes: list[dict]) -> dict:
    """
    Métricas de la lista cruda de `/API/Safes` (sin includeAccounts, así que
    no hay conteo de cuentas por safe):
      total, olac_count, no_cpm_count, retention_days_counts (Counter).
    """
    total = len(safes)
    olac_count = 0
    no_cpm_count = 0
    retention: Counter = Counter()

    for s in safes:
        if s.get("olacEnabled") or s.get("OLACEnabled"):
            olac_count += 1
        if not s.get("managingCPM"):
            no_cpm_count += 1
        days = s.get("numberOfDaysRetention")
        retention[days if days is not None else "Sin definir"] += 1

    return {
        "total": total,
        "olac_count": olac_count,
        "no_cpm_count": no_cpm_count,
        "retention_days_counts": retention,
    }


def analyze_platforms(platforms: list[dict], accounts_by_platform: Counter) -> dict:
    """
    Métricas de la lista cruda de `/API/Platforms`:
      total, active, custom (id != platformBaseID), no_periodic_rotation
      (permiten cambio manual pero sin rotación periódica), y `rows` con el
      conteo de cuentas por plataforma (desc).
    """
    rows = []
    for p in platforms:
        general = p.get("general") or {}
        cred = p.get("credentialsManagement") or {}
        platform_id = general.get("id", "")
        allow_manual_change = bool(cred.get("allowManualChange", False))
        periodic_change = bool(cred.get("performPeriodicChange", False))
        rows.append(
            {
                "id": platform_id,
                "name": general.get("name", platform_id),
                "active": bool(general.get("active", False)),
                "accounts": accounts_by_platform.get(platform_id, 0),
                "is_custom": bool(platform_id) and platform_id != general.get("platformBaseID"),
                "no_periodic_rotation": allow_manual_change and not periodic_change,
            }
        )
    rows.sort(key=lambda r: r["accounts"], reverse=True)

    return {
        "total": len(rows),
        "active": sum(1 for r in rows if r["active"]),
        "custom": sum(1 for r in rows if r["is_custom"]),
        "no_periodic_rotation": sum(1 for r in rows if r["no_periodic_rotation"]),
        "rows": rows,
    }


def accounts_by_month(accounts: list[dict]) -> list[tuple[str, int]]:
    """Altas de cuentas agrupadas por mes de `createdTime`, sin huecos."""
    by_month: Counter = Counter()
    for acc in accounts:
        created = _to_datetime(acc.get("createdTime"))
        if created:
            by_month[created.strftime("%Y-%m")] += 1
    if not by_month:
        return []

    months = sorted(by_month)
    start = datetime.strptime(months[0], "%Y-%m")
    end = datetime.strptime(months[-1], "%Y-%m")
    out: list[tuple[str, int]] = []
    cur = start
    while cur <= end:
        key = cur.strftime("%Y-%m")
        out.append((key, by_month.get(key, 0)))
        cur = (
            cur.replace(year=cur.year + 1, month=1)
            if cur.month == 12
            else cur.replace(month=cur.month + 1)
        )
    return out


# --- compliance / CPM / componentes (portado de ITR) -------------------- #

# manualManagementReason -> etiqueta legible (portado de ITR)
_CPM_REASON_LABELS = {
    "(CPM)MaxRetries": "Reintentos agotados (CPM)",
    "(CPM)UnrecoverableError": "Error sin reintento (CPM)",
    "Desactivacion masiva de rotacion automatica via API": "Desactivación masiva (API)",
}
# motivos que no describen una falla técnica del CPM
_CPM_REASON_EXCLUDED = {"no reason", "solicitud del cliente", ""}


def compliance_breakdown(
    accounts: list[dict],
    *,
    policy_failure_ids: set,
    filter_counts: dict,
    platform_days: dict[str, int] | None = None,
    default_stale_days: int = DEFAULT_DISABLED_STALE_DAYS,
    exempt_ids: set | None = None,
) -> dict:
    """
    Compliance EXACTO por cuenta — no una aproximación por suma de
    conteos (versión anterior a 2026-09-17): no-compliant si CyberArk la
    marca `PolicyFailures` (`policy_failure_ids`, ids reales — no un
    conteo, para poder deduplicar), O si está vencida contra la política
    de SU plataforma (`platform_days`, ver `platform_policy_days`) o el
    default del tenant si no define una — gestionada o no
    (`disabled_stale`/`active_overdue`, ver `analyze_accounts`). Al
    calcularlo cuenta por cuenta en vez de sumar conteos que pueden
    solaparse, el % nunca queda mal por doble conteo (ej. una cuenta que
    ya está en `policy_failure_ids` Y además vencida contra su propia
    plataforma antes sólo restaba una vez de más).

    `filter_counts` (`DisabledPasswordByUser`/`DisabledPasswordByCPM`/
    `Locked`) siguen siendo sólo informativos en el desglose — igual que
    antes, no restan del % (CyberArk no los cuenta como fallo de
    política, y una cuenta recién deshabilitada no debería contar como
    no-compliant todavía).

    `exempt_ids`: cuentas con una excepción manual activa
    (`compliance_exemptions`, ver docs/specs/compliance-exemptions.md) —
    nunca cuentan como vencidas/no-compliant, sin importar la fecha. Las
    llaves SSH (`secretType == "key"`) quedan exentas automáticamente, sin
    necesidad de estar en este set (ver `_is_rotation_exempt`).
    """
    total = len(accounts)
    if total == 0:
        return {"compliant": 0, "compliant_pct": 0.0, "categories": {}}

    platform_days = platform_days or {}
    now = datetime.now(UTC)
    non_compliant_ids: set = set()
    disabled_stale = 0
    active_overdue = 0

    for acc in accounts:
        acc_id = acc.get("id")
        sm = acc.get("secretManagement") or {}
        platform_id = acc.get("platformId") or ""
        overdue, _ = _account_overdue(sm, platform_id, platform_days, default_stale_days, now)
        overdue = overdue and not _is_rotation_exempt(acc, exempt_ids)
        is_policy_failure = acc_id in policy_failure_ids
        if overdue:
            if sm.get("automaticManagementEnabled"):
                active_overdue += 1
            else:
                disabled_stale += 1
        if overdue or is_policy_failure:
            non_compliant_ids.add(acc_id)

    compliant = total - len(non_compliant_ids)
    return {
        "compliant": compliant,
        "compliant_pct": round(compliant / total * 100, 1),
        "categories": {
            "Compliant": compliant,
            "PolicyFailures": len(policy_failure_ids),
            "DisabledStale": disabled_stale,
            "ActiveOverdue": active_overdue,
            "DisableByUser": filter_counts.get("DisabledPasswordByUser", 0),
            "DisableByCPM": filter_counts.get("DisabledPasswordByCPM", 0),
            "Locked": filter_counts.get("Locked", 0),
        },
    }


# columnas curadas comunes a las filas navegables de Compliance
def _compliance_row(acc: dict) -> dict:
    sm = acc.get("secretManagement") or {}
    return {
        "id": acc.get("id"),
        "name": acc.get("name"),
        "userName": acc.get("userName"),
        "address": acc.get("address"),
        "safeName": acc.get("safeName"),
        "platformId": acc.get("platformId"),
        "lastModifiedTime": sm.get("lastModifiedTime") or sm.get("lastModifiedDateTime"),
    }


def compliance_rows(accounts: list[dict]) -> list[dict]:
    """Filas navegables para una lista de cuentas YA filtrada por un
    savedFilter de CyberArk (PolicyFailures/DisabledPasswordByUser/
    DisabledPasswordByCPM/Locked) — alimenta la sección Compliance
    (distinto de `failed_accounts_rows`, que es sólo errores de CPM). El
    filtro que trajo la lista YA es el motivo, no hace falta repetirlo por
    fila."""
    return [_compliance_row(acc) for acc in accounts]


def _overdue_rows(
    accounts: list[dict],
    platform_days: dict[str, int],
    default_stale_days: int,
    *,
    managed: bool,
    exempt_ids: set | None = None,
) -> list[dict]:
    now = datetime.now(UTC)
    rows: list[dict] = []
    for acc in accounts:
        sm = acc.get("secretManagement") or {}
        if bool(sm.get("automaticManagementEnabled")) != managed:
            continue
        if _is_rotation_exempt(acc, exempt_ids):
            continue
        platform_id = acc.get("platformId") or ""
        overdue, age_days = _account_overdue(
            sm, platform_id, platform_days, default_stale_days, now
        )
        if not overdue:
            continue
        row = _compliance_row(acc)
        row["ageDays"] = age_days
        row["thresholdDays"] = platform_days.get(platform_id, default_stale_days)
        rows.append(row)
    return rows


def disabled_stale_rows(
    accounts: list[dict],
    default_stale_days: int,
    platform_days: dict[str, int] | None = None,
    exempt_ids: set | None = None,
) -> list[dict]:
    """Filas navegables de la categoría `DisabledStale` de
    `compliance_breakdown` — mismo criterio exacto (gestión deshabilitada
    + vencida contra la política de su plataforma, o el default del
    tenant), pero devolviendo las cuentas en vez de sólo el número.
    `ageDays` es `None` si la cuenta nunca rotó. `exempt_ids`: ver
    `compliance_breakdown`."""
    return _overdue_rows(
        accounts, platform_days or {}, default_stale_days, managed=False, exempt_ids=exempt_ids
    )


def active_overdue_rows(
    accounts: list[dict],
    default_stale_days: int,
    platform_days: dict[str, int] | None = None,
    exempt_ids: set | None = None,
) -> list[dict]:
    """Filas navegables de la categoría `ActiveOverdue` (2026-09-17) —
    cuentas con gestión automática ACTIVA que igual están vencidas contra
    la política de su plataforma (o el default del tenant): CyberArk
    debería marcarlas en su propio `PolicyFailures`, esto es la
    verificación independiente y explicable de PAM Web Console contra el mismo
    umbral. `exempt_ids`: ver `compliance_breakdown`."""
    return _overdue_rows(
        accounts, platform_days or {}, default_stale_days, managed=True, exempt_ids=exempt_ids
    )


def account_compliance(
    account: dict,
    *,
    is_policy_failure: bool,
    default_stale_days: int,
    platform_days: dict[str, int] | None = None,
    last_changed: datetime | None = None,
    exempt_ids: set | None = None,
    ssh_keys_exempt: bool = True,
) -> dict:
    """Compliance de UNA cuenta puntual — mismo criterio EXACTO que
    `compliance_breakdown`, para el badge/card del detalle de cuenta.
    Devuelve `{"compliant", "age_days", "threshold_days", "reasons"}` —
    `reasons` es una lista de las categorías que aplican (puede tener más
    de una: ej. `PolicyFailures` y `ActiveOverdue` a la vez).

    `last_changed`: fecha del último cambio exitoso (ver
    `last_password_change`); sin ella se usa `lastModifiedTime`, que se
    mueve también con un intento fallido del CPM. Además devuelve
    `last_changed` (epoch), `last_changed_source` ("versions"/"account") y
    `cpm_failed` (el último intento del CPM falló) para que la UI explique
    bien el motivo: con `cpm_failed`, CyberArk incluye la cuenta en
    `PolicyFailures` aunque la contraseña esté dentro del plazo (el círculo
    "Days ago" del PVWA sólo mira la antigüedad y la muestra "Compliant").
    `exempt_ids`: ver `compliance_breakdown`. `ssh_keys_exempt=False`: ver
    `_is_rotation_exempt` (lo usa `app.reports.non_rotating_accounts`, que
    quiere ver las llaves SSH como "no rotan")."""
    sm = account.get("secretManagement") or {}
    platform_id = account.get("platformId") or ""
    platform_days = platform_days or {}
    threshold = platform_days.get(platform_id, default_stale_days)
    source = "versions" if last_changed is not None else "account"
    if last_changed is None:
        last_changed = _to_datetime(sm.get("lastModifiedTime") or sm.get("lastModifiedDateTime"))
    overdue, age_days = _account_overdue(
        sm, platform_id, platform_days, default_stale_days, datetime.now(UTC), last_changed
    )
    overdue = overdue and not _is_rotation_exempt(
        account, exempt_ids, ssh_keys_exempt=ssh_keys_exempt
    )
    reasons: list[str] = []
    if is_policy_failure:
        reasons.append("PolicyFailures")
    if overdue:
        reasons.append(
            "DisabledStale" if not sm.get("automaticManagementEnabled") else "ActiveOverdue"
        )
    return {
        "compliant": not reasons,
        "age_days": age_days,
        "threshold_days": threshold,
        "reasons": reasons,
        "last_changed": int(last_changed.timestamp()) if last_changed else None,
        "last_changed_source": source,
        "cpm_failed": str(sm.get("status") or "").lower() in ("failure", "failed"),
    }


def analyze_cpm_errors(accounts: list[dict], failed_ids: set) -> dict:
    """
    Desglosa las cuentas en estado Failure entre las que el CPM sigue
    reintentando (automaticManagementEnabled=true) y las que ya desactivó,
    con el motivo de desactivación. `failed_ids` viene de `analyze_accounts`.
    """
    active_retry = 0
    given_up = 0
    reasons: Counter = Counter()
    for acc in accounts:
        if acc.get("id") not in failed_ids:
            continue
        sm = acc.get("secretManagement") or {}
        if sm.get("automaticManagementEnabled"):
            active_retry += 1
            continue
        given_up += 1
        raw = (sm.get("manualManagementReason") or "").strip()
        if raw.lower() in _CPM_REASON_EXCLUDED:
            continue
        label = _CPM_REASON_LABELS.get(raw, raw)
        if len(label) > 40:
            label = label[:37] + "..."
        reasons[label] += 1
    return {
        "failed_total": len(failed_ids),
        "active_retry": active_retry,
        "given_up": given_up,
        "by_reason": reasons,
    }


def failed_accounts_rows(accounts: list[dict], failed_ids: set) -> list[dict]:
    """Detalle por cuenta de las que están en `failed_ids` (mismo criterio
    de "qué es un error" que `analyze_cpm_errors` — una sola fuente de
    verdad). Alimenta la sección Troubleshooting: a diferencia de
    `analyze_cpm_errors`, que sólo agrega, esto devuelve filas navegables.
    """
    rows: list[dict] = []
    for acc in accounts:
        if acc.get("id") not in failed_ids:
            continue
        sm = acc.get("secretManagement") or {}
        retrying = bool(sm.get("automaticManagementEnabled"))
        raw_reason = (sm.get("manualManagementReason") or "").strip()
        reason_label = None
        if not retrying and raw_reason.lower() not in _CPM_REASON_EXCLUDED:
            reason_label = _CPM_REASON_LABELS.get(raw_reason, raw_reason)
        rows.append(
            {
                "id": acc.get("id"),
                "name": acc.get("name"),
                "userName": acc.get("userName"),
                "address": acc.get("address"),
                "safeName": acc.get("safeName"),
                "platformId": acc.get("platformId"),
                "retrying": retrying,
                "reason": reason_label,
                "lastModifiedTime": sm.get("lastModifiedTime") or sm.get("lastModifiedDateTime"),
            }
        )
    return rows


# component_id -> nombre de display (portado de ITR)
COMPONENT_NAMES = {
    "PVWA": "PVWA (Portal)",
    "SessionManagement": "PSM / Session Management",
    "CPM": "CPM",
    "AIM": "AIM / Credential Provider",
}


def analyze_sessions(sessions: list[dict] | None) -> dict:
    """
    sessions: `client.live_sessions()` (None si la llamada falló — degrada,
    mismo criterio que `analyze_components`).
    Devuelve {available, total, by_protocol}. Sólo el conteo EN VIVO ahora
    mismo, sin tendencia histórica (necesitaría muestreo periódico / jobs,
    fuera de alcance — ver docs/PLAN.md §7 Fase 1, "dashboard v3").
    """
    if sessions is None:
        return {"available": False, "total": 0, "by_protocol": Counter()}
    by_protocol = Counter(s.get("Protocol") or "—" for s in sessions)
    return {"available": True, "total": len(sessions), "by_protocol": by_protocol}


def analyze_components(components_raw: dict) -> dict:
    """
    components_raw: {component_id: [ {...instancia...}, ... ] | None}
    Devuelve {component_id: {available, total_instances, connected,
    disconnected}} — sin el detalle de instancias.
    """
    summary: dict[str, dict[str, Any]] = {}
    for comp_id, instances in components_raw.items():
        if instances is None:
            summary[comp_id] = {"available": False}
            continue
        connected = sum(1 for i in instances if i.get("IsLoggedOn"))
        summary[comp_id] = {
            "available": True,
            "total_instances": len(instances),
            "connected": connected,
            "disconnected": len(instances) - connected,
        }
    return summary
