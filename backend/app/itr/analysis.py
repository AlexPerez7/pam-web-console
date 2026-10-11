"""
analysis.py

Toma los datos crudos que devuelve cyberark_api.py y los transforma en las
métricas agregadas que necesita el reporte (equivalentes a las secciones
del Vault Analysis Report que SÍ se pueden reconstruir vía API).
"""

import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta


def _to_datetime(epoch_value):
    """Convierte un timestamp epoch (seg) devuelto por la API a datetime UTC.
    Devuelve None si el valor es nulo, 0 o inválido."""
    if not epoch_value:
        return None
    try:
        return datetime.fromtimestamp(int(epoch_value), tz=UTC)
    except (ValueError, OSError, OverflowError):
        return None


def _parse_dotnet_date(value):
    """Convierte el formato de fecha .NET que devuelve Identity vía Redrock
    (ej. "/Date(1738952470261)/", milisegundos epoch) a datetime UTC.
    Devuelve None si el valor es nulo o no matchea el formato."""
    if not value or not isinstance(value, str):
        return None
    import re

    m = re.match(r"/Date\((\d+)\)/", value)
    if not m:
        return None
    ms = int(m.group(1))
    return _to_datetime(ms // 1000)


def analyze_accounts(accounts):
    """
    accounts: lista cruda devuelta por get_all_accounts()

    Devuelve un diccionario con:
      - total
      - status_counts: Success / Failure / inProcess
      - by_platform: Counter con cantidad de cuentas por platformId
      - by_safe: Counter con cantidad de cuentas por safeName
      - never_verified: cuentas sin lastVerifiedDateTime
      - never_changed: cuentas sin lastModifiedDateTime (nunca gestionadas)
      - age_buckets_change / age_buckets_verify: distribución en días
      - no_platform: cuentas sin platformId asignado
    """
    total = len(accounts)
    status_counts = Counter()
    by_platform = Counter()
    by_safe = Counter()
    never_verified = 0
    never_changed = 0
    no_platform = 0

    age_buckets_change = Counter()
    age_buckets_verify = Counter()

    now = datetime.now(UTC)

    AGE_BUCKET_LABELS = [
        "0-30 días",
        "1-2 meses",
        "2-3 meses",
        "3-6 meses",
        "6-12 meses",
        "1-2 años",
        "+2 años",
        "Never",
    ]

    def bucket_for(dt):
        if dt is None:
            return "Never"
        days = (now - dt).days
        if days <= 30:
            return "0-30 días"
        elif days <= 60:
            return "1-2 meses"
        elif days <= 90:
            return "2-3 meses"
        elif days <= 180:
            return "3-6 meses"
        elif days <= 365:
            return "6-12 meses"
        elif days <= 730:
            return "1-2 años"
        else:
            return "+2 años"

    status_display = {
        # variantes documentadas oficialmente (Get account details)
        "succeeded": "Success",
        "failed": "Failure",
        "inprocess": "In Process",
        # variantes observadas en tenants reales - distinta
        # capitalización/spelling de lo que documenta CyberArk
        "success": "Success",
        "failure": "Failure",
    }
    failed_ids = set()

    for acc in accounts:
        sm = acc.get("secretManagement", {}) or {}
        raw_status = (sm.get("status") or "").strip()
        # Se normaliza para que el KPI de arriba y el gráfico de torta
        # siempre coincidan, sin importar qué variante devuelva el tenant.
        status = status_display.get(raw_status.lower(), raw_status or "Desconocido")
        status_counts[status] += 1
        # ManagementStatus == "failure" es lo que PVWA usa para pintar la
        # cuenta en rojo, independientemente de si automaticManagementEnabled
        # está en true o false: una cuenta que el CPM desactivó automáticamente
        # tras agotar reintentos (manualManagementReason del estilo
        # "(CPM)MaxRetries") sigue en estado de error real, no es una
        # desactivación intencional del usuario.
        if status == "Failure":
            failed_ids.add(acc.get("id"))

        platform_id = acc.get("platformId") or ""
        if not platform_id:
            no_platform += 1
        else:
            by_platform[platform_id] += 1

        safe_name = acc.get("safeName", "Desconocida")
        by_safe[safe_name] += 1

        last_verified = _to_datetime(sm.get("lastVerifiedTime") or sm.get("lastVerifiedDateTime"))
        last_changed = _to_datetime(sm.get("lastModifiedTime") or sm.get("lastModifiedDateTime"))

        if last_verified is None:
            never_verified += 1
        age_buckets_verify[bucket_for(last_verified)] += 1

        if last_changed is None:
            never_changed += 1
        age_buckets_change[bucket_for(last_changed)] += 1

    return {
        "total": total,
        "status_counts": status_counts,
        "failed_ids": failed_ids,
        "by_platform": by_platform,
        "by_safe": by_safe,
        "never_verified": never_verified,
        "never_changed": never_changed,
        "no_platform": no_platform,
        "age_buckets_change": age_buckets_change,
        "age_buckets_verify": age_buckets_verify,
        "age_bucket_labels": AGE_BUCKET_LABELS,
    }


def analyze_compliance(total_accounts, policy_failures_count):
    """Calcula % de compliance a partir del total de cuentas y las que
    matchean el savedFilter PolicyFailures."""
    if total_accounts == 0:
        return {"compliant": 0, "non_compliant": 0, "compliance_pct": 0.0}
    non_compliant = policy_failures_count
    compliant = total_accounts - non_compliant
    pct = round((compliant / total_accounts) * 100, 1)
    return {
        "compliant": compliant,
        "non_compliant": non_compliant,
        "compliance_pct": pct,
    }


CPM_FAILURE_REASON_LABELS = {
    "(CPM)MaxRetries": "Reintentos agotados (CPM)",
    "(CPM)UnrecoverableError": "Error sin reintento (CPM)",
    "Desactivacion masiva de rotacion automatica via API": "Desactivación masiva (API)",
}

# manualManagementReason que no describen una falla técnica del CPM sino
# una desactivación administrativa intencional (o sin dato) - no aportan al
# diagnóstico de errores de CPM, así que se excluyen del desglose por motivo.
CPM_FAILURE_REASON_EXCLUDED = {"no reason", "solicitud del cliente", ""}


def analyze_cpm_errors(accounts, failed_ids=None):
    """
    accounts: lista cruda de get_all_accounts().
    failed_ids: set de IDs de cuentas con secretManagement.status == "Failure"
                (viene de analyze_accounts). Es la única fuente confiable:
                los savedFilters FailedChange/FailedVerify/FailedReconcile de
                Get Accounts están rotos en Privilege Cloud (SaaS/ISPSS) -
                confirmado contra un tenant real (Globex) que siempre
                devuelven count 0 aunque haya cuentas con
                automaticManagementEnabled=true y status=Failure ahora mismo.

    Además del total, desglosa las cuentas falladas entre las que el CPM
    sigue reintentando activamente (automaticManagementEnabled=true) y las
    que el CPM ya desactivó tras agotar reintentos (manualManagementReason),
    con el motivo de desactivación - dato real disponible en el propio
    Get Accounts, sin depender de los savedFilters rotos.
    """
    failed_ids = failed_ids or set()
    active_retry = 0
    given_up = 0
    reason_counts = Counter()
    for acc in accounts:
        if acc.get("id") not in failed_ids:
            continue
        sm = acc.get("secretManagement", {}) or {}
        if sm.get("automaticManagementEnabled"):
            active_retry += 1
        else:
            given_up += 1
            raw_reason = (sm.get("manualManagementReason") or "").strip()
            if raw_reason.lower() in CPM_FAILURE_REASON_EXCLUDED:
                continue
            reason = CPM_FAILURE_REASON_LABELS.get(raw_reason, raw_reason)
            # tenants pueden setear razones custom largas vía API - se
            # truncan para no romper el eje del gráfico horizontal
            if len(reason) > 40:
                reason = reason[:37] + "..."
            reason_counts[reason] += 1

    reason_items = reason_counts.most_common()
    return {
        "accounts_with_failed_status": len(failed_ids),
        "active_retry": active_retry,
        "given_up": given_up,
        "reason_labels": [r for r, _ in reason_items],
        "reason_values": [c for _, c in reason_items],
    }


# Nombres de display por component_id de ComponentsMonitoringDetails - se
# usan tanto para las etiquetas del reporte (report_builder.py) como para
# poder filtrar por nombre desde config.json (excluded_summary_components),
# así que viven aquí como fuente única en vez de duplicarse en los dos lados.
COMPONENT_NAME_MAP = {
    "PVWA": "PVWA (Portal)",
    "SessionManagement": "PSM / Session Management",
    "CPM": "CPM",
    "AIM": "AIM / Credential Provider",
}


def analyze_components(components_raw, excluded_names=None):
    """
    components_raw: dict {component_id: [ {...instancia...}, ... ] o None}
    excluded_names: lista opcional de nombres de display (ej. "PVWA (Portal)",
                     ver COMPONENT_NAME_MAP) a excluir del resumen - viene de
                     config.json (excluded_summary_components), para tenants
                     donde ese componente no aplica o no se quiere mostrar.
    Devuelve resumen de conectividad por componente.
    """
    excluded_names = set(excluded_names or [])
    summary = {}
    for comp_id, instances in components_raw.items():
        if COMPONENT_NAME_MAP.get(comp_id, comp_id) in excluded_names:
            continue
        if instances is None:
            summary[comp_id] = {"available": False, "instances": []}
            continue
        connected = sum(1 for i in instances if i.get("IsLoggedOn"))
        summary[comp_id] = {
            "available": True,
            "total_instances": len(instances),
            "connected": connected,
            "disconnected": len(instances) - connected,
            "instances": instances,
        }
    return summary


def analyze_platforms(platforms_raw, accounts_by_platform):
    """
    platforms_raw: lista cruda de get_platforms() (cada item con 'general',
                   'credentialsManagement', 'sessionManagement',
                   'privilegedAccessWorkflows', 'linkedAccounts', etc.)
    accounts_by_platform: Counter ya calculado en analyze_accounts() (by_platform)

    Devuelve un dict con:
      - total, active, custom (plataformas duplicadas/personalizadas de una
        base, id != platformBaseID - no la plataforma "de fábrica" tal cual)
      - contadores de "posture" (excluyen Account Groups): no_periodic_rotation
        / no_periodic_verification (permiten la acción manual pero no la
        corren periódicamente), no_auto_reconcile (permiten reconcile manual
        pero no auto-reconcile), no_psm_monitoring (activas sin PSM
        monitoring & isolation), unused_active (activas con 0 cuentas)
      - rows: filas para la tabla principal de Platforms, ordenadas por
        cantidad de cuentas (desc). Cada fila trae también auto_change /
        auto_verify / auto_reconcile / psm_monitoring / exclusive_access /
        description, del propio Get Platforms (sin requests extra)

    Nota: se probó exponer el detalle INI crudo por plataforma (Get
    Platform Details, 1 request por plataforma) pero contra un tenant real
    de Privilege Cloud el endpoint respondió 200 para las 196 plataformas
    sin lanzar ningún error, y aun así "Properties" vino vacío en todas -
    a diferencia de PAM Self-Hosted (que tiene acceso directo al Vault),
    Privilege Cloud no parece exponer las propiedades INI crudas por esta
    vía. Se sacó esa funcionalidad (ver historial de git). En cambio,
    PSMServerID y linkedAccounts (Reconcile/Logon Account) sí vienen
    poblados en el propio Get Platforms (platforms_raw) - no hacía falta
    ningún request extra para esos.
    """
    rows = []

    for p in platforms_raw:
        general = p.get("general", {}) or {}
        cred = p.get("credentialsManagement", {}) or {}
        sess = p.get("sessionManagement", {}) or {}
        wf = p.get("privilegedAccessWorkflows", {}) or {}
        linked_names = {la.get("name") for la in (p.get("linkedAccounts") or [])}

        platform_id = general.get("id", "")
        accounts_count = accounts_by_platform.get(platform_id, 0)
        is_group = (general.get("platformType") or "").lower() == "group"
        allow_manual_change = bool(cred.get("allowManualChange", False))
        periodic_change = bool(cred.get("performPeriodicChange", False))
        allow_manual_verify = bool(cred.get("allowManualVerification", False))
        periodic_verify = bool(cred.get("performPeriodicVerification", False))
        allow_manual_reconcile = bool(cred.get("allowManualReconciliation", False))
        auto_reconcile = bool(cred.get("automaticReconcileWhenUnsynched", False))

        rows.append(
            {
                "id": platform_id,
                "name": general.get("name", platform_id),
                "description": general.get("description", ""),
                "system_type": general.get("systemType", ""),
                "platform_type": general.get("platformType", ""),
                "is_group": is_group,
                "active": general.get("active", False),
                "accounts": accounts_count,
                "allowed_safes": cred.get("allowedSafes", ""),
                "change_every_days": cred.get("requirePasswordChangeEveryXDays"),
                "verify_every_days": cred.get("requirePasswordVerificationEveryXDays"),
                "auto_change": periodic_change,
                "auto_verify": periodic_verify,
                "allow_manual_reconcile": allow_manual_reconcile,
                "auto_reconcile": auto_reconcile,
                "dual_control": wf.get("requireDualControlPasswordAccessApproval", False),
                "otp": wf.get("enforceOnetimePasswordAccess", False),
                "exclusive_access": wf.get("enforceCheckinCheckoutExclusiveAccess", False),
                "record_sessions": sess.get("recordAndSaveSessionActivity", False),
                "psm_monitoring": sess.get("requirePrivilegedSessionMonitoringAndIsolation", False),
                "psm_server": sess.get("PSMServerID") or "-",
                "has_reconcile_account": "ReconcileAccount" in linked_names,
                "has_logon_account": "LogonAccount" in linked_names,
                "is_custom": bool(platform_id) and platform_id != general.get("platformBaseID"),
                # "sin rotación/verificación automática" = permite la acción manual
                # pero no la corre periódicamente el CPM (una plataforma que solo
                # permite manual a propósito no cuenta - por eso el allow_manual_*)
                "no_periodic_rotation": allow_manual_change and not periodic_change,
                "no_periodic_verification": allow_manual_verify and not periodic_verify,
            }
        )

    rows.sort(key=lambda r: r["accounts"], reverse=True)

    # Los Account Groups (platformType=group) no tienen política de gestión de
    # credenciales ni de sesión reales - se excluyen de los contadores de
    # "posture" para no inflarlos con una categoría que no aplica.
    real = [r for r in rows if not r["is_group"]]

    return {
        "total": len(rows),
        "active": sum(1 for r in rows if r["active"]),
        "custom": sum(1 for r in rows if r["is_custom"]),
        "no_periodic_rotation": sum(1 for r in real if r["no_periodic_rotation"]),
        "no_periodic_verification": sum(1 for r in real if r["no_periodic_verification"]),
        "no_auto_reconcile": sum(
            1 for r in real if r["allow_manual_reconcile"] and not r["auto_reconcile"]
        ),
        # activas, CON cuentas y sin PSM monitoring - se saca "Application"
        # (AIM/CCP app-to-app, sin sesión interactiva, no necesita PSM)
        "no_psm_monitoring": sum(
            1
            for r in real
            if r["active"]
            and r["accounts"] > 0
            and not r["psm_monitoring"]
            and (r["system_type"] or "").lower() != "application"
        ),
        # plataformas activas sin ninguna cuenta = config habilitada sin uso
        "unused_active": sum(1 for r in real if r["active"] and r["accounts"] == 0),
        "rows": rows,
    }


def analyze_components_detailed(components_raw, excluded_users=None):
    """
    components_raw: dict {component_id: [ {...instancia...}, ... ] o None}
                     (cada instancia trae ComponentIP, ComponentUserName,
                     ComponentVersion, IsLoggedOn, LastLogonDate - campos
                     documentados en "Privilege Cloud system health details")
    excluded_users: lista opcional de ComponentUserName a excluir (ej.
                     cuentas de servicio internas tipo PVWAAppUser que no
                     aportan al análisis) - viene de config.json
                     (excluded_active_components).

    Devuelve una lista plana de instancias (una fila por componente
    conectado alguna vez), con un bucket de antigüedad de conexión para
    poder colorear la fila igual que la leyenda del VAReport original:
      - "recent": LastLogon dentro de las últimas 48 horas
      - "stale": más de 48hs
      - "month": dentro del último mes
      - "old": dentro de los últimos 3 meses
      - "never": sin LastLogonDate / nunca conectado
    """
    excluded_users = set(excluded_users or [])
    now = datetime.now(UTC)
    rows = []
    for comp_id, instances in components_raw.items():
        if not instances:
            continue
        for inst in instances:
            if inst.get("ComponentUserName", "") in excluded_users:
                continue
            last_logon = _to_datetime(inst.get("LastLogonDate"))
            if last_logon is None:
                bucket = "never"
            else:
                hours = (now - last_logon).total_seconds() / 3600
                if hours <= 48:
                    bucket = "recent"
                elif hours <= 24 * 30:
                    bucket = "stale"
                elif hours <= 24 * 90:
                    bucket = "month"
                else:
                    bucket = "old"
            rows.append(
                {
                    "component_type": comp_id,
                    "component_user": inst.get("ComponentUserName", ""),
                    "component_ip": inst.get("ComponentIP", ""),
                    "version": inst.get("ComponentVersion", ""),
                    "is_logged_on": bool(inst.get("IsLoggedOn")),
                    "last_logon": last_logon.strftime("%d-%m-%Y %H:%M")
                    if last_logon
                    else "Nunca conectado",
                    "bucket": bucket,
                }
            )
    rows.sort(key=lambda r: (r["component_type"], r["component_user"]))
    return rows


def analyze_connectors(connectors_raw):
    """
    connectors_raw: lista cruda de get_all_connectors() (Connector Management).
    Cada connector trae host (hostname, privateIp, publicIp, os, etc.) y su
    lista de components (Management Agent, CPM Scanner, PSM, Password Manager)
    con usedCpu/usedMemory, version, componentStatus, upgradeVersions,
    installedAt/updatedAt (epoch EN MILISEGUNDOS, no segundos).

    Devuelve una lista plana de filas, una por componente, lista para tabla.
    """
    status_ok = {"Active", "Initializing", "Downloaded"}
    rows = []
    for conn in connectors_raw:
        host = conn.get("host", {}) or {}
        host_name = host.get("hostname") or conn.get("connectorId", "")
        host_ip = host.get("privateIp") or host.get("publicIp") or ""
        platform_type = conn.get("platformType", "")
        connector_status = conn.get("connectorStatus", "")
        connector_version = conn.get("version", "")

        components = conn.get("components", []) or []
        if not components:
            rows.append(
                {
                    "host_name": host_name,
                    "host_ip": host_ip,
                    "platform_type": platform_type,
                    "connector_status": connector_status,
                    "connector_version": connector_version,
                    "component_name": "-",
                    "acronym": "-",
                    "component_status": "-",
                    "component_version": "-",
                    "used_cpu": None,
                    "used_memory": None,
                    "update_available": "-",
                    "updated_at": "-",
                    "is_ok": connector_status in status_ok,
                }
            )
            continue

        for comp in components:
            # installedAt/updatedAt vienen en milisegundos, no en segundos
            updated_at_ms = comp.get("updatedAt")
            updated_at = _to_datetime(updated_at_ms // 1000) if updated_at_ms else None
            comp_status = comp.get("componentStatus", "")

            # sentinela -1 quiere decir "sin datos" (ej. componente
            # "Not Found": no está corriendo, no hay métrica real que mostrar)
            raw_cpu = comp.get("usedCpu")
            raw_mem = comp.get("usedMemory")
            used_cpu = None if raw_cpu is None or raw_cpu < 0 else raw_cpu
            used_memory = None if raw_mem is None or raw_mem < 0 else raw_mem

            upgrade_versions = comp.get("upgradeVersions") or []
            update_available = ", ".join(upgrade_versions) if upgrade_versions else "Al día"

            rows.append(
                {
                    "host_name": host_name,
                    "host_ip": host_ip,
                    "platform_type": platform_type,
                    "connector_status": connector_status,
                    "connector_version": connector_version,
                    "component_name": comp.get("componentName", ""),
                    "acronym": comp.get("acronym") or "",
                    "component_status": comp_status,
                    "component_version": comp.get("version", ""),
                    "used_cpu": used_cpu,
                    "used_memory": used_memory,
                    "update_available": update_available,
                    "updated_at": updated_at.strftime("%d-%m-%Y %H:%M") if updated_at else "-",
                    "is_ok": comp_status in status_ok,
                }
            )

    rows.sort(key=lambda r: (r["host_name"], r["component_name"]))
    return rows


# Schema confirmado contra un tenant real (Globex). A diferencia de
# Connector Management, esta API no expone connector pools ni "platform"
# en el listado - trae host/IP, tipo de host (ON-PREMISE/cloud), OS,
# versión (+ si está actualizada), sesiones activas y status. La API
# también trae "region", pero en el tenant de referencia (conectores
# on-prem) siempre vino vacía y no aporta al reporte - se descarta aquí
# a propósito, no se muestra en la tabla.
_SIA_STATUS_OK = {"active", "connected", "healthy", "running", "online"}


def analyze_sia_connectors(sia_connectors_raw, sia_connector_details=None):
    """
    sia_connectors_raw: lista cruda de get_all_sia_connectors() (items de
    la respuesta de SIA Connector API, dominio dpa.cyberark.cloud -
    servicio y schema separados de Connector Management). Cada item trae:
    id, hostName, hostIp, hostType, os, version, isLatestVersion,
    activeSessionsCount, region, status, statusCode (además de
    workspaceId/hostInstanceId/hostNetwork/hostSubnet/proxySettings, que
    en el tenant de referencia siempre vinieron vacíos). "region" no se
    incluye en las filas - ver comentario arriba.

    sia_connector_details: {id: detalle | None} de
    get_all_sia_connector_details() - opcional. La lista NO trae la
    versión objetivo de upgrade, solo isLatestVersion (true/false); el
    detalle por conector sí trae version_to_upgrade, confirmado contra un
    tenant real. Si no se pasa (o un conector puntual no se pudo
    consultar), upgrade_version queda en None.
    """
    sia_connector_details = sia_connector_details or {}
    rows = []
    for c in sia_connectors_raw:
        status = c.get("status") or "-"
        detail = sia_connector_details.get(c.get("id")) or {}
        rows.append(
            {
                "host_name": c.get("hostName") or "-",
                "host_ip": c.get("hostIp") or "-",
                "host_type": c.get("hostType") or "-",
                "os": c.get("os") or "-",
                "version": c.get("version") or "-",
                "is_latest_version": bool(c.get("isLatestVersion")),
                "upgrade_version": detail.get("version_to_upgrade") or None,
                "sessions": c.get("activeSessionsCount") or 0,
                "status": status,
                "is_ok": str(status).lower() in _SIA_STATUS_OK,
            }
        )
    rows.sort(key=lambda r: r["host_name"])
    return rows


def analyze_safes(safes_raw, safe_members_raw=None):
    """
    safes_raw: lista cruda devuelta por get_all_safes() (idealmente con
               includeAccounts=true para tener el conteo de cuentas real).
    safe_members_raw: lista cruda combinada de get_all_safe_members() (ya la
               traemos para la tabla Safe Members) - se cruza por safeName
               para detectar Safes con la administración concentrada en
               0 o 1 solo miembro con permiso manageSafe.
    Nota: el Service User solo ve los Safes de los que es miembro, así que
    este total puede ser menor al real del tenant si no se lo agregó a todos.

    Devuelve:
      - total, olac_count, no_cpm_count, empty_count, concentrated_admin_count
        (para los KPIs y findings)
      - retention_days_counts: Counter para el gráfico de retención
      - rows: una fila por Safe, lista para la tabla interactiva
    """
    safe_members_raw = safe_members_raw or []

    # cuántos miembros con permiso manageSafe tiene cada Safe, y de cuáles
    # Safes se pudo ver la lista de miembros (el Service User puede no tener
    # "View Safe Members" en algunos puntuales, ver get_all_safe_members)
    admin_counts = Counter()
    safes_with_visible_members = set()
    for m in safe_members_raw:
        safe_name = m.get("safeName")
        if not safe_name:
            continue
        safes_with_visible_members.add(safe_name)
        if (m.get("permissions") or {}).get("manageSafe"):
            admin_counts[safe_name] += 1

    total = len(safes_raw)
    olac_count = 0
    no_cpm_count = 0
    empty_count = 0
    concentrated_admin_count = 0
    rows = []

    for s in safes_raw:
        name = s.get("safeName", "")
        olac = bool(s.get("olacEnabled") or s.get("OLACEnabled"))
        if olac:
            olac_count += 1

        no_cpm = not s.get("managingCPM")
        if no_cpm:
            no_cpm_count += 1

        accounts_list = s.get("accounts") or []
        accounts_count = len(accounts_list)
        if accounts_count == 0:
            empty_count += 1

        admin_count = admin_counts.get(name, 0)
        # solo se cuenta como "concentrada" si además se pudo ver la lista de
        # miembros - si no hay visibilidad, no se sabe realmente cuántos hay
        concentrated_admin = name in safes_with_visible_members and admin_count <= 1
        if concentrated_admin:
            concentrated_admin_count += 1

        creation = _to_datetime(s.get("creationTime"))
        # lastModificationTime a veces viene en microsegundos en vez de segundos
        last_mod_raw = s.get("lastModificationTime")
        last_mod = _to_datetime(last_mod_raw)
        if last_mod is None and last_mod_raw:
            last_mod = _to_datetime(int(last_mod_raw) // 1_000_000)

        rows.append(
            {
                "name": name,
                "description": s.get("description", ""),
                "managing_cpm": s.get("managingCPM") or "-",
                "no_cpm": no_cpm,
                "olac_enabled": olac,
                "auto_purge": bool(s.get("autoPurgeEnabled")),
                "retention_days": s.get("numberOfDaysRetention"),
                "retention_versions": s.get("numberOfVersionsRetention"),
                "accounts": accounts_count,
                "creator": (s.get("creator") or {}).get("name", "-"),
                "creation_date": creation.strftime("%d-%m-%Y") if creation else "-",
                "last_modified": last_mod.strftime("%d-%m-%Y") if last_mod else "-",
                "admin_count": admin_count,
                "concentrated_admin": concentrated_admin,
            }
        )

    rows.sort(key=lambda r: r["accounts"], reverse=True)
    retention_days_counts = Counter(
        r["retention_days"] if r["retention_days"] is not None else "Sin definir" for r in rows
    )
    return {
        "total": total,
        "olac_count": olac_count,
        "no_cpm_count": no_cpm_count,
        "empty_count": empty_count,
        "concentrated_admin_count": concentrated_admin_count,
        "retention_days_counts": retention_days_counts,
        "rows": rows,
    }


def analyze_compliance_breakdown(total_accounts, breakdown_counts):
    """
    breakdown_counts: dict {"PolicyFailures": N, "DisabledPasswordByUser": N,
                             "DisabledPasswordByCPM": N, "Locked": N}
    Nota: las categorías pueden solaparse entre sí (una cuenta bloqueada
    también puede figurar en PolicyFailures), así que esto es una
    aproximación de proporciones, no una partición exacta como en el
    donut original.
    """
    if total_accounts == 0:
        return {"compliant_pct": 0.0, "categories": {}}
    policy_failures = breakdown_counts.get("PolicyFailures", 0)
    compliant = max(total_accounts - policy_failures, 0)
    compliant_pct = round((compliant / total_accounts) * 100, 1)
    return {
        "compliant": compliant,
        "compliant_pct": compliant_pct,
        "categories": {
            "Compliant": compliant,
            "PolicyFailures": policy_failures,
            "DisableByUser": breakdown_counts.get("DisabledPasswordByUser", 0),
            "DisableByCPM": breakdown_counts.get("DisabledPasswordByCPM", 0),
            "Locked": breakdown_counts.get("Locked", 0),
        },
    }


def analyze_compliance_by_platform(
    accounts, platforms_raw, safes_raw, breakdown_lists, failed_ids=None
):
    """
    Reconstruye una tabla de Compliance por plataforma parecida a la del
    VAReport original, SIN hacer ningún request nuevo: cruza los datos que
    ya se descargaron de Accounts, Platforms, Safes y los savedFilters de
    compliance/CPM errors.

    accounts: lista cruda de get_all_accounts()
    platforms_raw: lista cruda de get_platforms()
    safes_raw: lista cruda de get_all_safes() (para NoCPMOnSafe)
    breakdown_lists: dict con listas crudas de cuentas por savedFilter, ej.
        {"DisabledPasswordByUser": [...], "DisabledPasswordByCPM": [...]}

    Limitación conocida: NoCPMOnSafe solo se calcula para Safes que el
    Service User puede ver (get_all_safes) - si una cuenta vive en un Safe
    que el Service User no tiene permiso de listar, esa columna queda en
    False para esa cuenta (no se arriesga a marcar falsos positivos).
    """
    platform_map = {}
    for p in platforms_raw:
        g = p.get("general", {}) or {}
        cred = p.get("credentialsManagement", {}) or {}
        pid = g.get("id")
        if pid:
            platform_map[pid] = {
                "name": g.get("name", pid),
                "active": g.get("active", True),
                "change_every_days": cred.get("requirePasswordChangeEveryXDays"),
                "auto_change": bool(cred.get("performPeriodicChange", False)),
            }

    safe_cpm_map = {s.get("safeName"): (s.get("managingCPM") or "") for s in safes_raw}

    disable_by_user_ids = {a.get("id") for a in breakdown_lists.get("DisabledPasswordByUser", [])}
    disable_by_cpm_ids = {a.get("id") for a in breakdown_lists.get("DisabledPasswordByCPM", [])}
    # El status real de la cuenta (secretManagement.status == "Failure") es
    # la única fuente confiable de fallas de CPM: los savedFilters
    # FailedChange/FailedVerify/FailedReconcile están rotos en Privilege
    # Cloud (ver analyze_cpm_errors).
    cpm_failure_ids = set(failed_ids or set())

    now = datetime.now(UTC)
    stats = defaultdict(Counter)
    platform_label = {}

    for acc in accounts:
        pid = acc.get("platformId") or ""
        platform_label[pid] = platform_map.get(pid, {}).get("name", pid or "Sin plataforma")
        row = stats[pid]
        row["accounts"] += 1

        sm = acc.get("secretManagement", {}) or {}
        last_changed = _to_datetime(sm.get("lastModifiedTime") or sm.get("lastModifiedDateTime"))
        never_managed = last_changed is None

        inactive_platform = bool(pid) and pid in platform_map and not platform_map[pid]["active"]
        invalid_platform = bool(pid) and pid not in platform_map

        safe_name = acc.get("safeName")
        if safe_name in safe_cpm_map:
            no_cpm_on_safe = not safe_cpm_map[safe_name]
        else:
            no_cpm_on_safe = False  # Safe no visible para el Service User - no se arriesga el dato

        disable_by_user = acc.get("id") in disable_by_user_ids
        disable_by_cpm = acc.get("id") in disable_by_cpm_ids
        cpm_failure = acc.get("id") in cpm_failure_ids

        last_change_exp = False
        if not never_managed and pid in platform_map:
            threshold = platform_map[pid]["change_every_days"]
            if threshold and (now - last_changed).days > threshold:
                last_change_exp = True

        flags = {
            "inactive_platform": inactive_platform,
            "invalid_platform": invalid_platform,
            "last_change_exp": last_change_exp,
            "disable_by_user": disable_by_user,
            "disable_by_cpm": disable_by_cpm,
            "no_cpm_on_safe": no_cpm_on_safe,
            "cpm_failure": cpm_failure,
            "never_managed": never_managed,
        }
        for k, v in flags.items():
            if v:
                row[k] += 1
        if not any(flags.values()):
            row["compliant"] += 1

    rows = []
    for pid, row in stats.items():
        rows.append(
            {
                "platform_name": platform_label.get(pid, pid or "Sin plataforma"),
                "auto_change": "ENABLE"
                if platform_map.get(pid, {}).get("auto_change")
                else "DISABLE",
                "accounts": row["accounts"],
                "compliant": row["compliant"],
                "inactive_platform": row["inactive_platform"],
                "invalid_platform": row["invalid_platform"],
                "last_change_exp": row["last_change_exp"],
                "disable_by_user": row["disable_by_user"],
                "disable_by_cpm": row["disable_by_cpm"],
                "no_cpm_on_safe": row["no_cpm_on_safe"],
                "cpm_failure": row["cpm_failure"],
                "never_managed": row["never_managed"],
            }
        )
    rows.sort(key=lambda r: r["accounts"], reverse=True)
    return rows


def analyze_accounts_created_by_month(accounts):
    """
    accounts: lista cruda de get_all_accounts()
    Agrupa por mes de creación (createdTime) para un gráfico de tendencia.
    Devuelve (labels, values) ya ordenados cronológicamente, con los meses
    sin altas incluidos en 0 (para que el gráfico no tenga huecos raros).
    """
    from collections import Counter as _Counter

    by_month = _Counter()
    for acc in accounts:
        created = _to_datetime(acc.get("createdTime"))
        if created:
            by_month[created.strftime("%Y-%m")] += 1

    if not by_month:
        return [], []

    months_sorted = sorted(by_month.keys())
    # completar meses sin altas en el medio del rango, para que la línea
    # de tendencia no salte huecos
    start = datetime.strptime(months_sorted[0], "%Y-%m")
    end = datetime.strptime(months_sorted[-1], "%Y-%m")
    labels = []
    values = []
    current = start
    while current <= end:
        key = current.strftime("%Y-%m")
        labels.append(key)
        values.append(by_month.get(key, 0))
        # avanzar al mes siguiente
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)

    return labels, values


def analyze_accounts_table(accounts, activities_by_id=None):
    """
    accounts: lista cruda de get_all_accounts()
    activities_by_id: dict {accountId: [activities] | None} de
    get_all_account_activities() (el mismo dato ya usado en la pestaña
    KPI's) - opcional. Si se pasa, agrega "Last Logon" con el último "PSM
    Connect" registrado para esa cuenta - a diferencia del conteo de
    KPI's (limitado a los últimos 30 días), acá se busca el último uso
    real disponible en el historial, igual que LastLogin en Users Details.

    Arma las filas para la tabla completa de cuentas del VAReport:
    Username, Address, Platform, Safe, Created Time, Last Logon.
    """
    activities_by_id = activities_by_id or {}
    rows = []
    for acc in accounts:
        created = _to_datetime(acc.get("createdTime"))
        activities = activities_by_id.get(acc.get("id"))
        if activities is None:
            last_logon = "No disponible"
        else:
            connects = [
                a.get("Date")
                for a in activities
                if a.get("Action") == "PSM Connect" and a.get("Date")
            ]
            last_logon = (
                datetime.fromtimestamp(max(connects), tz=UTC).strftime("%d-%m-%Y %H:%M")
                if connects
                else "Nunca"
            )
        rows.append(
            {
                "username": acc.get("userName", ""),
                "address": acc.get("address", ""),
                "platform": acc.get("platformId", ""),
                "safe": acc.get("safeName", ""),
                "created": created.strftime("%d-%m-%Y") if created else "-",
                "last_logon": last_logon,
            }
        )
    rows.sort(key=lambda r: r["username"].lower())
    return rows


def analyze_account_activity(accounts, activities_by_id, window_days=30):
    """
    accounts: lista cruda de get_all_accounts().
    activities_by_id: dict {accountId: [activities] | None} de
    get_all_account_activities() - None significa que no se pudo consultar
    esa cuenta puntual (queda con sessions_30d=None, "no se pudo consultar",
    para no confundirlo con 0 conexiones reales).

    "Uso" se mide igual que el script .ps1 de referencia: actividades con
    Action que contiene "PSM Connect" y Date dentro de los últimos
    window_days días (Date es epoch UTC, igual que el resto de la API).
    """
    cutoff_epoch = (datetime.now(UTC) - timedelta(days=window_days)).timestamp()

    def count_recent_connects(activities):
        return sum(
            1
            for a in activities
            if (a.get("Date") or 0) >= cutoff_epoch and "PSM Connect" in (a.get("Action") or "")
        )

    rows = []
    for acc in accounts:
        aid = acc.get("id")
        activities = activities_by_id.get(aid)
        sessions = None if activities is None else count_recent_connects(activities)
        rows.append(
            {
                "account_id": aid,
                "account_name": acc.get("name", ""),
                "safe_name": acc.get("safeName", ""),
                "address": acc.get("address", ""),
                "user_name": acc.get("userName", ""),
                "platform_id": acc.get("platformId", ""),
                "sessions_30d": sessions,
            }
        )
    rows.sort(
        key=lambda r: (
            -(r["sessions_30d"] if r["sessions_30d"] is not None else -1),
            r["account_name"].lower(),
        )
    )

    known = [r["sessions_30d"] for r in rows if r["sessions_30d"] is not None]
    total_known = len(known)
    used = sum(1 for s in known if s > 0)
    unused = total_known - used
    used_pct = round((used / total_known) * 100, 1) if total_known else 0.0

    def bucket(n):
        if n == 0:
            return "0"
        if n <= 5:
            return "1-5"
        if n <= 20:
            return "6-20"
        return "21+"

    bucket_labels = ["0", "1-5", "6-20", "21+"]
    bucket_counts = Counter(bucket(s) for s in known)

    top_active = [r for r in rows if r["sessions_30d"]][:15]

    return {
        "rows": rows,
        "window_days": window_days,
        "total": len(rows),
        "total_known": total_known,
        "used": used,
        "unused": unused,
        "used_pct": used_pct,
        "bucket_labels": bucket_labels,
        "bucket_values": [bucket_counts.get(b, 0) for b in bucket_labels],
        "top_active": top_active,
    }


_SESSION_DURATION_RE = re.compile(r"Session Duration:\s*(\d+):(\d{2}):(\d{2})")
_PSM_SERVER_RE = re.compile(r"PSM Server:\s*([^,]+)")

# Horario laboral asumido para "fuera de horario" - UTC, sin config de
# timezone por cliente todavía (ver docstring de analyze_concurrent_sessions).
_BUSINESS_DAYS = range(0, 5)  # lunes=0 ... viernes=4
_BUSINESS_HOUR_START = 8
_BUSINESS_HOUR_END = 18

# Umbral fijo y fácil de explicar en un reporte (en vez de un outlier
# estadístico tipo IQR) para marcar sesiones "largas".
_LONG_SESSION_HOURS = 4

_WEEKDAY_NAMES_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def _format_duration(seconds):
    hours, rem = divmod(int(seconds), 3600)
    minutes = rem // 60
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def _sweep_max_concurrent(intervals):
    """intervals: lista de (start, end) epoch. Devuelve (pico, epoch del pico)."""
    if not intervals:
        return 0, None
    events = sorted(
        [(start, 1) for start, _ in intervals] + [(end, -1) for _, end in intervals],
        key=lambda e: (e[0], e[1]),
    )
    running = 0
    max_concurrent = 0
    max_concurrent_at = None
    for epoch, delta in events:
        running += delta
        if running > max_concurrent:
            max_concurrent = running
            max_concurrent_at = epoch
    return max_concurrent, max_concurrent_at


def analyze_concurrent_sessions(activities_by_id, accounts=None, window_days=30):
    """
    Reconstruye sesiones PSM (inicio/fin) a partir de las actividades "PSM
    Disconnect", que ya traen la duración exacta de la sesión en MoreInfo
    ("Session Duration: HH:MM:SS") - no hace falta correlacionar a mano con
    su "PSM Connect" (el Session ID puede repetirse o el Connect puede
    faltar en sesiones que no llegaron a cerrar). Sesiones sin Disconnect
    (todavía abiertas, o el evento se perdió) quedan afuera del cálculo -
    es una limitación conocida, no un intento de adivinar cuándo terminaron.

    Para toda la ventana (agregado de TODAS las cuentas - la pregunta es
    cuánta carga concurrente hay sobre la infraestructura de PSM en su
    conjunto) calcula: pico global de concurrencia, pico diario, pico por
    servidor PSM (MoreInfo trae "PSM Server: ..."), distribución de inicios
    de sesión por hora del día, sesiones fuera de horario laboral, y
    duración (promedio/mediana + sesiones largas).

    Las horas (distribución horaria, horario laboral) son en UTC - no hay
    timezone por cliente configurada todavía, así que en tenants fuera de
    UTC hay que leerlas con ese corrimiento en mente.
    """
    now = datetime.now(UTC)
    cutoff_epoch = (now - timedelta(days=window_days)).timestamp()
    account_by_id = {a.get("id"): a for a in (accounts or [])}

    def _account_name(account_id):
        acc = account_by_id.get(account_id)
        return acc.get("name") if acc else account_id

    def _account_address(account_id):
        acc = account_by_id.get(account_id)
        return acc.get("address", "-") if acc else "-"

    sessions = []
    for account_id, activities in (activities_by_id or {}).items():
        if not activities:
            continue
        for a in activities:
            if a.get("Action") != "PSM Disconnect":
                continue
            end = a.get("Date") or 0
            if end < cutoff_epoch:
                continue
            more_info = a.get("MoreInfo") or ""
            m = _SESSION_DURATION_RE.search(more_info)
            if not m:
                continue
            duration_seconds = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            if duration_seconds <= 0:
                continue
            server_match = _PSM_SERVER_RE.search(more_info)
            sessions.append(
                {
                    "account_id": account_id,
                    "user": a.get("User") or "-",
                    "server": server_match.group(1).strip() if server_match else "Desconocido",
                    "start": end - duration_seconds,
                    "end": end,
                    "duration_seconds": duration_seconds,
                }
            )

    day_seconds = 86400
    window_start_day = int(cutoff_epoch // day_seconds) * day_seconds
    window_end_day = int(now.timestamp() // day_seconds) * day_seconds
    day_starts = list(range(window_start_day, window_end_day + day_seconds, day_seconds))
    day_labels = [datetime.fromtimestamp(d, tz=UTC).strftime("%d-%m") for d in day_starts]
    hour_labels = [f"{h:02d}" for h in range(24)]

    if not sessions:
        return {
            "window_days": window_days,
            "sessions_counted": 0,
            "max_concurrent": 0,
            "max_concurrent_at": None,
            "day_labels": day_labels,
            "day_values": [0 for _ in day_starts],
            "by_server": [],
            "hour_labels": hour_labels,
            "hour_values": [0 for _ in hour_labels],
            "off_hours_count": 0,
            "off_hours_pct": 0.0,
            "off_hours_sessions": [],
            "avg_duration_minutes": 0,
            "median_duration_minutes": 0,
            "long_session_hours": _LONG_SESSION_HOURS,
            "long_session_count": 0,
            "long_sessions": [],
        }

    intervals = [(s["start"], s["end"]) for s in sessions]
    max_concurrent, max_concurrent_at = _sweep_max_concurrent(intervals)

    # ---- pico por día (para el gráfico) ----
    events = sorted(
        [(s["start"], 1) for s in sessions] + [(s["end"], -1) for s in sessions],
        key=lambda e: (e[0], e[1]),
    )
    running = 0
    changes = []  # (epoch, cantidad concurrente inmediatamente después de este evento)
    for epoch, delta in events:
        running += delta
        changes.append((epoch, running))

    # changes está ordenado cronológicamente - se recorre una sola vez,
    # drenando primero los eventos anteriores al inicio de la ventana
    # (sesiones que empezaron antes pero terminaron dentro) hacia
    # carry_in, y después el pico de cada día en orden.
    carry_in = 0
    idx = 0
    while idx < len(changes) and changes[idx][0] < window_start_day:
        carry_in = changes[idx][1]
        idx += 1

    day_values = []
    for day_start in day_starts:
        day_end = day_start + day_seconds
        peak = carry_in
        while idx < len(changes) and changes[idx][0] < day_end:
            carry_in = changes[idx][1]
            peak = max(peak, carry_in)
            idx += 1
        day_values.append(peak)

    # ---- pico por servidor PSM ----
    server_intervals = defaultdict(list)
    for s in sessions:
        server_intervals[s["server"]].append((s["start"], s["end"]))
    by_server = []
    for server, ivs in server_intervals.items():
        peak, _ = _sweep_max_concurrent(ivs)
        by_server.append({"server": server, "max_concurrent": peak, "sessions_counted": len(ivs)})
    by_server.sort(key=lambda r: -r["max_concurrent"])

    # ---- distribución de inicios de sesión por hora del día (UTC) ----
    hour_counts = Counter(datetime.fromtimestamp(s["start"], tz=UTC).hour for s in sessions)
    hour_values = [hour_counts.get(h, 0) for h in range(24)]

    # ---- fuera de horario laboral (lun-vie 08-18 UTC) ----
    def _is_off_hours(dt):
        return not (
            dt.weekday() in _BUSINESS_DAYS and _BUSINESS_HOUR_START <= dt.hour < _BUSINESS_HOUR_END
        )

    off_hours_sessions = []
    for s in sessions:
        dt = datetime.fromtimestamp(s["start"], tz=UTC)
        if not _is_off_hours(dt):
            continue
        off_hours_sessions.append(
            {
                "start_txt": dt.strftime("%d-%m-%Y %H:%M"),
                "weekday": _WEEKDAY_NAMES_ES[dt.weekday()],
                "user": s["user"],
                "account_name": _account_name(s["account_id"]),
                "address": _account_address(s["account_id"]),
                "server": s["server"],
                "duration_txt": _format_duration(s["duration_seconds"]),
                "_start": s["start"],
            }
        )
    off_hours_sessions.sort(key=lambda r: -r["_start"])
    for r in off_hours_sessions:
        del r["_start"]

    off_hours_count = len(off_hours_sessions)
    off_hours_pct = round((off_hours_count / len(sessions)) * 100, 1)

    # ---- duración: promedio/mediana + sesiones largas ----
    durations_min = sorted(s["duration_seconds"] / 60 for s in sessions)
    avg_duration_minutes = round(sum(durations_min) / len(durations_min), 1)
    mid = len(durations_min) // 2
    median_duration_minutes = round(
        durations_min[mid]
        if len(durations_min) % 2
        else (durations_min[mid - 1] + durations_min[mid]) / 2,
        1,
    )

    long_threshold_seconds = _LONG_SESSION_HOURS * 3600
    long_all = [s for s in sessions if s["duration_seconds"] >= long_threshold_seconds]
    long_sessions = [
        {
            "account_name": _account_name(s["account_id"]),
            "user": s["user"],
            "server": s["server"],
            "start_txt": datetime.fromtimestamp(s["start"], tz=UTC).strftime("%d-%m-%Y %H:%M"),
            "duration_txt": _format_duration(s["duration_seconds"]),
        }
        for s in sorted(long_all, key=lambda s: -s["duration_seconds"])[:15]
    ]

    return {
        "window_days": window_days,
        "sessions_counted": len(sessions),
        "max_concurrent": max_concurrent,
        "max_concurrent_at": (
            datetime.fromtimestamp(max_concurrent_at, tz=UTC).strftime("%d-%m-%Y %H:%M")
            if max_concurrent_at
            else None
        ),
        "day_labels": day_labels,
        "day_values": day_values,
        "by_server": by_server,
        "hour_labels": hour_labels,
        "hour_values": hour_values,
        "off_hours_count": off_hours_count,
        "off_hours_pct": off_hours_pct,
        "off_hours_sessions": off_hours_sessions,
        "avg_duration_minutes": avg_duration_minutes,
        "median_duration_minutes": median_duration_minutes,
        "long_session_hours": _LONG_SESSION_HOURS,
        "long_session_count": len(long_all),
        "long_sessions": long_sessions,
    }


# Códigos de SourceDs que CyberArk a veces no localiza bien (SourceDsLocalized
# y SourceDsType vuelven literalmente "Unknown" aunque el dato crudo esté).
# Confirmado con datos reales de un tenant de referencia: los conteos de
# Cloud Directory, Active Directory y Federated ya coincidían exactos con el
# portal sin necesidad de mapeo; solo AAD (Microsoft Entra ID) lo necesitaba.
# LDAP y Google Directory quedan sin confirmar (el tenant de referencia no
# tenía usuarios de esas fuentes) - si aparecen como "Unknown", agregar el
# SourceDs real de esos casos aquí.
KNOWN_SOURCE_DS_CODES = {
    "AAD": "Microsoft Entra ID",
    "FDS": "Federated",
}

# El portal de Identity solo muestra 4 estados de usuario: Active, Suspended,
# Invited, Not Invited. La tabla "Users" de Redrock devuelve "Created" en vez
# de "Not Invited" - inferido por eliminación (en un tenant real solo
# aparecieron Active/Suspended/Created, sin "Invited"), no confirmado 1:1
# contra un caso "Invited" real todavía.
KNOWN_STATUS_MAP = {
    "Created": "Not Invited",
}


# Orden de preferencia cuando un mismo username tiene varios registros en
# Identity (típico de migraciones AD -> Entra ID: la cuenta vieja queda
# Suspended y se crea una nueva Active) - menor valor = se prefiere.
_STATUS_PREFERENCE = {"Active": 0, "Suspended": 1, "Not Invited": 2, "Created": 2}

# Usuarios PREDEFINIDOS de CyberArk que NO son componentes (componentUser=false)
# y por lo tanto no los agarra ese flag: cuentas internas del Vault, no
# personas. Se marcan como system_user para sacarlos de los KPIs de usuario y
# de la tabla "Usuarios inactivos" (se espera que nunca se logueen). Los que
# SÍ son componentUser=true (PVWA/PSM/CPM/PSMPServer/AppProvider...) ya vienen
# marcados aparte. Comparación case-insensitive.
_BUILTIN_USERNAMES = frozenset(
    {
        "master",
        "administrator",
        "auditor",
        "backup",
        "batch",
        "dr",
        "operator",
        "notificationengine",
        "env console",
        "pvwagwuser",
        "accountsfeedapp",
        "epmagent",
        "cyberarksecureaccessservice",
    }
)


def analyze_users(
    users_raw,
    safe_members_raw,
    identity_users_raw=None,
    user_details_by_id=None,
    self_username=None,
):
    """
    users_raw: lista cruda de get_all_users() (Get Users - userType,
               username, source, enableUser, suspended, groupsMembership,
               componentUser). Solo cubre usuarios provisionados en el Vault.
    safe_members_raw: lista cruda de get_all_safe_members() (ya la teníamos
                       para la pestaña Safes) - se cruza por username para
                       calcular "Direct Safe Ownership" sin requests nuevos.
    identity_users_raw: lista cruda de get_identity_users() (tabla "Users"
                         de Identity vía Redrock) - cubre TODOS los usuarios
                         de Identity, incluyendo los que nunca se onboardearon
                         al Vault (ej. cuentas bot/de servicio). Vacío en
                         self_hosted (no hay Identity).
    user_details_by_id: {id: detalle de GET /API/Users/{id} | None} de
                         get_all_user_details() - SOLO se usa en self_hosted:
                         la lista GET /API/Users no trae last-logon, solo el
                         detalle de a uno lo hace (lastSuccessfulLoginDate,
                         enableUser, suspended). None/{} en cloud.
    self_username: username del Service User que corre el reporte (cfg) -
                         se marca como system_user para no listarse a sí
                         mismo entre los "usuarios inactivos".

    Devuelve la UNIÓN de ambas fuentes (no solo los que están en Get Users):
    un usuario que solo existe en Identity igual aparece, con User Type y
    Group Memberships limitados a lo que Identity puede dar (no tiene acceso
    al detalle de grupos de Vault que sí trae Get Users).

    Nota: Creation Date y los contadores de sesiones RDP/SSH/Other del
    VAReport original siguen sin aparecer en ninguna tabla de Identity que
    probamos (DSUsers, Users) - quedan pendientes.
    """
    identity_users_raw = identity_users_raw or []
    # el id de usuario de PVWA es un entero, pero al pasar por el cache a
    # disco (JSON) las claves quedan como string - se normaliza a str para
    # que el lookup funcione igual con datos frescos o cacheados.
    user_details_by_id = {str(k): v for k, v in (user_details_by_id or {}).items()}
    self_username_lc = (self_username or "").strip().lower()

    # cuenta cuántos Safes tiene cada username como miembro directo
    safe_ownership = Counter()
    for m in safe_members_raw:
        member_name = m.get("memberName")
        if member_name:
            safe_ownership[member_name] += 1

    # Lookup por username (case-insensitive) desde la tabla Users de Identity:
    # LastLogin, Status, Source y UserType - con manejo de duplicados (ej.
    # migración AD -> Entra ID: se prefiere siempre el registro Active).
    identity_lookup = {}
    for iu in identity_users_raw:
        username_exact = iu.get("Username") or iu.get("LoginName") or ""
        uname = username_exact.lower()
        if not uname:
            continue
        dt = _parse_dotnet_date(iu.get("LastLogin"))

        localized = iu.get("SourceDsLocalized")
        raw_code = iu.get("SourceDs") or ""
        if localized and localized != "Unknown":
            source = localized
        elif raw_code in KNOWN_SOURCE_DS_CODES:
            source = KNOWN_SOURCE_DS_CODES[raw_code]
        elif raw_code:
            source = raw_code  # mejor mostrar el código crudo que "Unknown"
        else:
            source = iu.get("SourceDsType") or None

        raw_status = KNOWN_STATUS_MAP.get(
            iu.get("StatusEnum") or iu.get("Status"), iu.get("StatusEnum") or iu.get("Status")
        )

        existing = identity_lookup.get(uname)
        if existing is not None:
            # ya hay un registro para este username (cuenta duplicada, ej.
            # migración AD -> Entra ID) - solo lo reemplazamos si el nuevo
            # tiene mejor status (Active > Suspended > Not Invited)
            new_rank = _STATUS_PREFERENCE.get(raw_status, 99)
            existing_rank = _STATUS_PREFERENCE.get(existing["status"], 99)
            if new_rank >= existing_rank:
                continue

        identity_lookup[uname] = {
            "username": username_exact,
            "last_login": dt.strftime("%d-%m-%Y %H:%M") if dt else "Nunca",
            "last_login_dt": dt,
            "status": raw_status,
            "source": source,
            "user_type": iu.get("UserType") or "Desconocido",
        }

    # Lookup de Get Users por username (case-insensitive)
    vault_lookup = {}
    for u in users_raw:
        uname = (u.get("username") or "").lower()
        if uname:
            vault_lookup[uname] = u

    all_usernames = set(vault_lookup.keys()) | set(identity_lookup.keys())

    user_type_counts = Counter()
    rows = []
    for uname_lower in all_usernames:
        vault_u = vault_lookup.get(uname_lower)
        identity_info = identity_lookup.get(uname_lower)
        in_vault = vault_u is not None

        if vault_u:
            username = vault_u.get("username", "")
            groups = vault_u.get("groupsMembership", []) or []
            group_count = len(groups)
        else:
            username = identity_info["username"]
            group_count = 0  # Identity no da el detalle de grupos de Vault

        # Detalle GET /API/Users/{id} (solo self_hosted): trae el last-logon
        # y el estado reales, que la lista GET /API/Users NO expone.
        vault_id = vault_u.get("id") if vault_u else None
        _detail_raw = user_details_by_id.get(str(vault_id)) if vault_id is not None else None
        detail = _detail_raw or {}
        # ¿se pudo LEER el detalle de este usuario? (distinto de "el detalle
        # vino pero sin lastSuccessfulLoginDate" -> eso significa que nunca se
        # logueó, no "dato no disponible"). None = la llamada falló.
        detail_fetched = _detail_raw is not None

        # LastLogin: del detalle si lo tenemos (self_hosted); si no, del
        # propio item de la lista (por si alguna versión sí lo trae). En
        # cloud el last-logon real sale de identity_info más abajo.
        vault_last_login_dt = _to_datetime(
            detail.get("lastSuccessfulLoginDate")
            or (vault_u.get("lastSuccessfulLoginDate") if vault_u else None)
        )

        if identity_info:
            status = identity_info["status"] or "Desconocido"
            source = identity_info["source"] or (
                vault_u.get("source") if vault_u else "Desconocido"
            )
            last_login_dt = identity_info["last_login_dt"] or vault_last_login_dt
            last_login = (
                identity_info["last_login"]
                if identity_info["last_login_dt"]
                else (
                    vault_last_login_dt.strftime("%d-%m-%Y %H:%M")
                    if vault_last_login_dt
                    else identity_info["last_login"]
                )
            )
            user_type = vault_u.get("userType") if vault_u else identity_info["user_type"]
        else:
            # usuario de componente (PVWAAppUser, service accounts) o, en
            # self_hosted, cualquier usuario del Vault - no existe en el
            # directorio de Identity, se usa lo que trae Get Users / el
            # detalle. OJO: el campo de Get Users es 'enableUser', NO
            # 'enabled' - con 'enabled' (bug) el estado de todo usuario del
            # Vault no suspendido salía "Desconocido" (afectaba cloud y
            # self_hosted; en self_hosted era todos).
            enabled = detail.get("enableUser")
            if enabled is None:
                enabled = vault_u.get("enableUser", vault_u.get("enabled"))
            suspended = detail.get("suspended", vault_u.get("suspended"))
            if suspended:
                status = "Suspended"
            elif enabled is False:
                status = "Disabled"
            elif enabled is True:
                status = "Enabled"
            else:
                status = "Desconocido"
            source = vault_u.get("source", "Desconocido")
            last_login_dt = vault_last_login_dt
            if vault_last_login_dt:
                last_login = vault_last_login_dt.strftime("%d-%m-%Y %H:%M")
            elif detail_fetched:
                # el detalle vino sin lastSuccessfulLoginDate -> nunca se logueó
                last_login = "Nunca"
            else:
                # no hay Identity y no se pudo leer GET /API/Users/{id}
                last_login = "No disponible"
            user_type = vault_u.get("userType", "Desconocido")

        user_type_counts[user_type] += 1

        # componentUser=true: PVWA/PSM/CPM/PSMPServer/AppProvider... son
        # cuentas de servicio de un componente, no inician sesión interactiva.
        component_user = bool(vault_u.get("componentUser")) if vault_u else False
        uname_lc = username.lower()
        # "de sistema" = componente, o usuario predefinido de CyberArk
        # (Built-InAdmins: Auditor/Backup/Batch/Operator/DR/Master/..., o un
        # nombre fijo conocido), o el propio usuario que corre el reporte.
        # Se excluyen de los KPIs de usuario y de "Usuarios inactivos".
        system_user = (
            component_user
            or user_type == "Built-InAdmins"
            or uname_lc in _BUILTIN_USERNAMES
            or (self_username_lc and uname_lc == self_username_lc)
        )

        rows.append(
            {
                "id": vault_id,
                "username": username,
                "user_type": user_type,
                "source": source,
                "status": status,
                "group_count": group_count,
                "direct_safe_ownership": safe_ownership.get(username, 0),
                "last_login": last_login,
                "last_login_dt": last_login_dt,
                "in_vault": in_vault,
                "component_user": component_user,
                "system_user": system_user,
            }
        )

    rows.sort(key=lambda r: r["username"].lower())
    return {
        "total": len(rows),
        "by_type": user_type_counts,
        "rows": rows,
    }


def analyze_safe_members(members_raw):
    """
    members_raw: lista cruda combinada de get_all_safe_members() (todas las
                 Safes juntas, cada item ya trae su propio safeName).
    Devuelve:
      - rows: una fila por membresía, lista para la tabla interactiva, con
        un subconjunto legible de los permisos (la API devuelve ~20 flags
        booleanos por miembro; se muestran los más relevantes para una
        revisión rápida)
      - by_type: Counter de memberType (Group vs User) para el gráfico -
        en general se espera mayoría "Group" (mejor práctica: asignar
        acceso vía grupos, no directo a usuarios individuales)
    """
    rows = []
    by_type = Counter()
    for m in members_raw:
        perms = m.get("permissions", {}) or {}
        expiration = m.get("membershipExpirationDate")
        member_type = m.get("memberType", "") or "Desconocido"
        by_type[member_type] += 1
        rows.append(
            {
                "safe_name": m.get("safeName", ""),
                "member_name": m.get("memberName", ""),
                "member_type": member_type,
                "is_predefined": bool(m.get("isPredefinedUser")),
                "expiration": expiration if expiration else "Sin vencimiento",
                "list_accounts": bool(perms.get("listAccounts")),
                "use_accounts": bool(perms.get("useAccounts")),
                "retrieve_accounts": bool(perms.get("retrieveAccounts")),
                "manage_safe": bool(perms.get("manageSafe")),
                "manage_safe_members": bool(perms.get("manageSafeMembers")),
            }
        )
    rows.sort(key=lambda r: (r["safe_name"], r["member_name"]))
    return {"rows": rows, "by_type": by_type}


def analyze_licenses(license_report):
    """Aplana la respuesta de /API/licenses/pcloud/ a una lista simple
    de {name, used, total, pct}."""
    rows = []
    for group in license_report.get("licensesData", []):
        for el in group.get("licencesElements", []):
            used = int(el.get("used", 0))
            total = int(el.get("total", 0)) or 0
            pct = round((used / total) * 100, 1) if total else 0.0
            rows.append(
                {
                    "name": el.get("name"),
                    "used": used,
                    "total": total,
                    "pct": pct,
                }
            )
    return rows
