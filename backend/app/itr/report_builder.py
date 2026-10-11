"""
report_builder.py - Telemetry Reporter (ITR)

Prepara los datos a partir de las métricas calculadas en analysis.py y
renderiza el reporte con Jinja2 (plantillas en templates/, ver
templates/report.html.j2). Este módulo solo arma el "contexto" (dicts/listas
listas para iterar en el template) - el HTML/CSS/JS en sí no vive acá.
Usa Chart.js vía CDN (se carga en el navegador del que abre el HTML, no en
este script). La navegación por pestañas (Overview / Sesiones / Components /
Users / Safes / Platforms / Accounts / KPI's / Findings) imita la estructura
del reporte que reemplaza (el "Vault Analysis Report" de CyberArk) para que
el resultado sea más fácil de comparar y de presentarle al cliente.
"""

import base64
import mimetypes
import os
import re
from collections import Counter
from datetime import UTC, datetime

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, select_autoescape

from app.itr.analysis import COMPONENT_NAME_MAP

REPORT_TITLE = "Telemetry Reporter"
REPORT_ACRONYM = "ITR"

# Orden y labels de las pestañas del reporte, en el mismo orden en que
# aparecen en la nav (ver templates/partials/_header.html.j2). Única fuente
# de verdad también para la selección interactiva de pestañas a excluir en
# generate_report.py, así ambos lados no se pueden desincronizar.
TAB_ORDER = [
    ("overview", "Resumen"),
    ("sessions", "Sesiones"),
    ("components", "Componentes"),
    ("users", "Users"),
    ("safes", "Safes"),
    ("platforms", "Platforms"),
    ("accounts", "Accounts"),
    ("kpis", "KPI's"),
    ("findings", "Hallazgos"),
    ("infra", "Infraestructura"),
]

# Logo propio del equipo que emite el reporte (opcional): si existe
# logos/logo_brand.{png,jpg} se embebe en el header. Prueba varias
# extensiones comunes.
_LOGOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logos")

# El HTML/CSS/JS del reporte vive en templates/ (Jinja2), separado de este
# módulo que solo prepara los datos. FileSystemLoader anclado con
# dirname(abspath(__file__)) para que funcione sin importar desde qué
# directorio se invoque generate_report.py.
_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

# Overrides de layout por cliente: `clientes/<tenant_id>/templates/`
# coincide con el `id` del tenant (seed/tenants.json), así que no hace
# falta un mapeo aparte. `clientes/acme/` es un ejemplo (fix de header para
# un logo con fondo propio).
_CLIENTES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clientes")


def client_templates_dir_for(tenant_id):
    """Carpeta de override de `tenant_id`, o `None` si no tiene uno. Se le
    pasa tal cual a `build_html_report(client_templates_dir=...)` —
    `_make_jinja_env` ya tolera `None`/una carpeta inexistente."""
    path = os.path.join(_CLIENTES_DIR, tenant_id, "templates")
    return path if os.path.isdir(path) else None


def _make_jinja_env(client_templates_dir=None):
    """
    Arma el Environment de Jinja2 para un reporte puntual. Si
    client_templates_dir apunta a una carpeta existente (convención
    clientes/<cliente>/templates/, ver clientes/README.md), sus .j2 tienen
    prioridad sobre los de templates/ vía ChoiceLoader - así un cliente
    puede tener un layout personalizado (mover un gráfico a otra pestaña,
    ocultar una sección, etc.) pisando solo los archivos que necesita
    cambiar, sin bifurcar el proyecto: el resto sigue cayendo en la
    plantilla base y recibe sus fixes/mejoras normalmente.
    """
    loaders = [FileSystemLoader(_TEMPLATES_DIR)]
    if client_templates_dir and os.path.isdir(client_templates_dir):
        loaders.insert(0, FileSystemLoader(client_templates_dir))
    env = Environment(
        loader=ChoiceLoader(loaders) if len(loaders) > 1 else loaders[0],
        # autoescape=True es la única defensa XSS del reporte: los datos que
        # arma vienen de un tenant de CyberArk (usernames, addresses,
        # descripciones de Safe, hostnames de conectores, etc.) y no son de
        # confianza - cualquiera con permiso para crear una cuenta/Safe/
        # plataforma podría nombrarla con HTML/JS embebido. NUNCA desactivar.
        # Los valores que van dentro de <script> (gráficos) no pasan por acá -
        # esos usan el filtro |tojson, pensado justo para ese contexto.
        autoescape=select_autoescape(enabled_extensions=("html", "j2"), default_for_string=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["si_no"] = lambda v: "Sí" if v else "No"
    env.filters["finding_headline"] = _finding_headline
    return env


def _find_logo(base_name):
    for ext in ("jpg", "jpeg", "png"):
        candidate = os.path.join(_LOGOS_DIR, f"{base_name}.{ext}")
        if os.path.isfile(candidate):
            return candidate
    return None


# Logo "compacto" (header, junto al del cliente) y logo "completo" (footer,
# a modo de firma del reporte) - dos archivos separados en logos/ porque
# pueden ser variantes de marca distintas. Ambos opcionales.
_BRAND_LOGO_PATH = _find_logo("logo_brand")
_BRAND_FOOTER_LOGO_PATH = _find_logo("logo_brand_footer")


def _image_to_data_uri(path):
    """Lee un archivo de imagen y lo devuelve como data URI (base64) para
    embeberlo directo en el HTML, sin depender de un archivo externo.
    Devuelve None si el archivo no existe o no se puede leer."""
    if not path or not os.path.isfile(path):
        return None
    mime, _ = mimetypes.guess_type(path)
    mime = mime or "image/png"
    try:
        with open(path, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime};base64,{data}"
    except OSError:
        return None


def _counter_to_chart_data(counter):
    labels = list(counter.keys())
    values = [counter[k] for k in labels]
    return labels, values


# Orden de categorías en la pestaña Findings - las que suelen ser más
# urgentes primero. Cualquier categoría no listada acá (no debería pasar)
# se agrega al final, en el orden en que aparezca.
_FINDINGS_CATEGORY_ORDER = [
    "Cumplimiento",
    "Infraestructura",
    "Safes",
    "Platforms",
    "Componentes",
    "Accounts",
    "Actividad (KPI's)",
    "Users",
    "Licencias",
    "General",
]
_FINDINGS_SEVERITY_ORDER = {"crit": 0, "warn": 1, "info": 2, "ok": 3}
_FINDINGS_SEVERITY_LABELS = {"crit": "Crítico", "warn": "A revisar", "info": "Info", "ok": "OK"}
# Severidad "visual" de las tarjetas de Findings: solo 3 buckets (crítico/
# a revisar/informativo) - "ok" se muestra como informativo, ya que la franja
# de color y el badge no distinguen "informativo" de "sin hallazgos".
_FINDINGS_SEVERITY_CSS = {"crit": "crit", "warn": "warn", "info": "info", "ok": "info"}
_FINDINGS_SEVERITY_BADGE_LABELS = {
    "crit": "crítico",
    "warn": "a revisar",
    "info": "informativo",
    "ok": "informativo",
}
_FINDINGS_CATEGORY_ICONS = {
    "Cumplimiento": "\U0001f6e1️",
    "Infraestructura": "\U0001f5a5️",
    "Safes": "\U0001f5c4️",
    "Platforms": "\U0001f9e9",
    "Componentes": "⚙️",
    "Accounts": "\U0001f464",
    "Actividad (KPI's)": "\U0001f4c8",
    "Users": "\U0001f465",
    "Licencias": "\U0001f4c4",
    "General": "\U0001f4cc",
}
_FINDINGS_CATEGORY_ICON_DEFAULT = "\U0001f4cc"

# Extrae la cifra/frase clave de un hallazgo para destacarla en tamaño grande
# en la tarjeta (ej. "55.9%", "468 cuentas", "13 de 561 Safes"), probando
# patrones de más a menos específicos. Es puramente de presentación: no
# cambia el texto original, que se sigue mostrando completo debajo como
# contexto.
_HEADLINE_LEADING_PATTERNS = [
    re.compile(r"^\d[\d.,]*\s+de\s+\d[\d.,]*\s+\S+"),  # "13 de 561 Safes"
    re.compile(r"^\d[\d.,]*%"),  # "34.2%..."
    re.compile(r"^\d[\d.,]*\s+\S+"),  # "468 cuentas"
]
_HEADLINE_FALLBACK_PATTERNS = [
    re.compile(r"\d[\d.,]*\s?%"),  # primer porcentaje en cualquier parte
    re.compile(r"\d[\d.,]*"),  # primer número en cualquier parte
]


def _finding_headline(text):
    for pattern in _HEADLINE_LEADING_PATTERNS:
        match = pattern.match(text)
        if match:
            return match.group(0)
    for pattern in _HEADLINE_FALLBACK_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def _build_findings(
    accounts_analysis,
    compliance_breakdown,
    cpm_errors,
    components_summary,
    licenses,
    platforms_analysis,
    safes_analysis,
    users_analysis,
    connectors_analysis,
    sia_connectors_analysis,
    account_activity,
    concurrent_sessions,
    accounts_never_logon,
    accounts_never_logon_pct,
    infra_summary=None,
    is_cloud=True,
):
    """Genera hallazgos automáticos a partir de umbrales simples, recorriendo
    todas las pestañas del reporte (no solo Accounts/Compliance como antes).
    Cada hallazgo lleva categoría (para agrupar en la pestaña Findings) y
    severidad (crit = algo roto que actuar, warn = vale la pena revisar,
    info = dato de contexto, no necesariamente un problema)."""
    findings = []

    def add(category, severity, text, tip=None):
        findings.append(
            {
                "category": category,
                "severity": severity,
                "severity_label": _FINDINGS_SEVERITY_LABELS.get(severity, severity.upper()),
                "severity_css": _FINDINGS_SEVERITY_CSS.get(severity, "info"),
                "severity_badge_label": _FINDINGS_SEVERITY_BADGE_LABELS.get(severity, severity),
                "text": text,
                "tip": tip,
            }
        )

    total = accounts_analysis["total"] or 1

    never_verified_pct = round((accounts_analysis["never_verified"] / total) * 100, 1)
    if accounts_analysis["never_verified"] > 0:
        add(
            "Accounts",
            "warn",
            f"{accounts_analysis['never_verified']} cuentas ({never_verified_pct}%) nunca fueron verificadas.",
            tip="Revisar esas cuentas en la pestaña Accounts y forzar una verificación manual (o vía CPM) para confirmar que la contraseña almacenada coincide con la real.",
        )

    never_changed_pct = round((accounts_analysis["never_changed"] / total) * 100, 1)
    if accounts_analysis["never_changed"] > 0:
        add(
            "Accounts",
            "warn",
            f"{accounts_analysis['never_changed']} cuentas ({never_changed_pct}%) nunca fueron gestionadas por el CPM.",
            tip="Confirmar que esas cuentas tengan una plataforma con rotación habilitada y que el CPM tenga permisos sobre su Safe; si es intencional (cuentas excluidas de rotación), documentarlo.",
        )

    if accounts_analysis["no_platform"] > 0:
        add(
            "Accounts",
            "warn",
            f"{accounts_analysis['no_platform']} cuentas no tienen plataforma asignada.",
            tip="Asignarles una plataforma válida - sin plataforma no pueden ser gestionadas ni rotadas por el CPM.",
        )

    if accounts_never_logon > 0:
        add(
            "Accounts",
            "warn",
            f"{accounts_never_logon} cuentas ({accounts_never_logon_pct}%) nunca se conectaron vía PSM en el "
            f"historial disponible - candidatas a revisión (¿siguen siendo necesarias?).",
            tip="Confirmar con el dueño del Safe si la cuenta sigue en uso; si no, darla de baja para reducir superficie de ataque.",
        )

    if compliance_breakdown["compliant_pct"] < 80:
        add(
            "Cumplimiento",
            "crit",
            f"El compliance general es de {compliance_breakdown['compliant_pct']}%, por debajo del 80% recomendado.",
            tip="Revisar el desglose en la pestaña KPI's (PolicyFailures/DisableByUser/DisableByCPM/Locked) para identificar la causa principal y priorizar esas cuentas.",
        )

    if cpm_errors["accounts_with_failed_status"] > 0:
        add(
            "Cumplimiento",
            "crit",
            f"{cpm_errors['accounts_with_failed_status']} cuentas tienen status Failure (error real de CPM).",
            tip="Revisar el log del CPM (PMTerminal.log / ITALog en el servidor CPM) para identificar la causa raíz (credenciales incorrectas, conectividad, permisos del usuario remoto, etc.).",
        )

    for comp_id, info in components_summary.items():
        if info["available"] and info["disconnected"] > 0:
            add(
                "Componentes",
                "crit",
                f"{info['disconnected']} instancia(s) de {comp_id} aparecen desconectadas.",
                tip="Verificar que el servicio de Windows correspondiente esté corriendo en el servidor y que tenga conectividad de red hacia el Vault/Privilege Cloud.",
            )

    connectors_not_ok = sum(1 for c in connectors_analysis if not c["is_ok"])
    if connectors_not_ok:
        add(
            "Componentes",
            "crit",
            f"{connectors_not_ok} componente(s) de Connector Management no están en estado operativo (Active/Initializing/Downloaded).",
            tip="Revisar el estado detallado en la pestaña Components (o el portal de Connector Management) y reiniciar/reinstalar los conectores que sigan fallando.",
        )

    connectors_update_pending = sum(
        1 for c in connectors_analysis if c["update_available"] not in ("Al día", "-")
    )
    if connectors_update_pending:
        add(
            "Componentes",
            "info",
            f"{connectors_update_pending} componente(s) de Connector Management tienen actualización pendiente.",
            tip="Planificar una ventana de mantenimiento para actualizar esos componentes a la última versión disponible.",
        )

    sia_not_ok = sum(1 for c in (sia_connectors_analysis or []) if not c["is_ok"])
    if sia_not_ok:
        add(
            "Componentes",
            "crit",
            f"{sia_not_ok} SIA Connector(s) no están en estado operativo.",
            tip="Revisar el estado del conector en el portal de Secure Infrastructure Access y validar conectividad del servidor donde está instalado.",
        )

    sia_outdated = sum(1 for c in (sia_connectors_analysis or []) if not c["is_latest_version"])
    if sia_outdated:
        add(
            "Componentes",
            "info",
            f"{sia_outdated} SIA Connector(s) no están en la última versión disponible.",
            tip="Actualizar esos conectores a la última versión disponible desde el portal de SIA.",
        )

    for lic in licenses:
        if lic["pct"] >= 90:
            add(
                "Licencias",
                "crit",
                f"Licencias de '{lic['name']}' al {lic['pct']}% de uso — cerca del límite.",
                tip="Revisar en Users la lista de candidatos a liberar licencia (usuarios sin login o inactivos hace 30+ días) antes de solicitar licencias adicionales.",
            )

    if platforms_analysis["no_periodic_rotation"] > 0:
        platforms_total = platforms_analysis["total"] or 1
        rotation_pct = round(
            (platforms_analysis["no_periodic_rotation"] / platforms_total) * 100, 1
        )
        add(
            "Platforms",
            "warn",
            f"{platforms_analysis['no_periodic_rotation']} de {platforms_analysis['total']} plataformas "
            f"({rotation_pct}%) permiten cambio manual de contraseña pero no tienen rotación periódica "
            f"automática habilitada.",
            tip="Habilitar 'Automatic Password Management > Scheduled tasks > Change password every X days' en esas plataformas, salvo que la rotación manual sea intencional.",
        )

    if platforms_analysis.get("no_periodic_verification", 0) > 0:
        add(
            "Platforms",
            "warn",
            f"{platforms_analysis['no_periodic_verification']} plataforma(s) permiten verificación manual "
            f"pero no verifican la contraseña periódicamente - una contraseña desincronizada puede pasar "
            f"desapercibida hasta que falla una conexión.",
            tip="Habilitar 'Automatic Password Management > Scheduled tasks > Verify password every X days' para detectar desincronizaciones antes de que rompan un acceso.",
        )

    if platforms_analysis.get("no_psm_monitoring", 0) > 0:
        add(
            "Platforms",
            "crit",
            f"{platforms_analysis['no_psm_monitoring']} plataforma(s) activas con cuentas no tienen "
            f"'Privileged Session Monitoring and Isolation' - las sesiones de esas cuentas no pasan por el "
            f"PSM (sin aislamiento ni grabación garantizada).",
            tip="Activar 'Session Management > Require privileged session monitoring and isolation' salvo que el acceso directo sin PSM sea intencional para esa plataforma.",
        )

    if platforms_analysis.get("unused_active", 0) > 0:
        add(
            "Platforms",
            "info",
            f"{platforms_analysis['unused_active']} plataforma(s) activas no tienen ninguna cuenta asignada.",
            tip="Desactivar las plataformas que ya no se usan reduce la superficie de configuración a mantener y auditar.",
        )

    inactive_with_accounts = sum(
        1 for r in platforms_analysis["rows"] if not r["active"] and r["accounts"] > 0
    )
    if inactive_with_accounts:
        add(
            "Platforms",
            "crit",
            f"{inactive_with_accounts} plataforma(s) inactivas tienen cuentas asignadas - esas cuentas no "
            f"pueden rotar ni conectarse vía PSM mientras la plataforma siga inactiva.",
            tip="Reactivar la plataforma si las cuentas siguen en uso, o migrar esas cuentas a una plataforma activa equivalente.",
        )

    if safes_analysis["no_cpm_count"] > 0:
        safes_total = safes_analysis["total"] or 1
        no_cpm_pct = round((safes_analysis["no_cpm_count"] / safes_total) * 100, 1)
        add(
            "Safes",
            "crit",
            f"{safes_analysis['no_cpm_count']} de {safes_analysis['total']} Safes ({no_cpm_pct}%) no tienen "
            f"un CPM asignado (Managing CPM vacío) — las cuentas en esas Safes no rotan automáticamente.",
            tip="Asignar un CPM a esos Safes (propiedades del Safe > Managing CPM) para que las cuentas que contienen puedan rotar automáticamente.",
        )

    if safes_analysis["concentrated_admin_count"] > 0:
        add(
            "Safes",
            "warn",
            f"{safes_analysis['concentrated_admin_count']} Safes tienen la administración concentrada en 0 o 1 "
            f"solo miembro con permiso Manage Safe.",
            tip="Agregar al menos un miembro adicional con permiso Manage Safe en esos Safes, para evitar un punto único de falla si esa persona/cuenta deja de estar disponible.",
        )

    if safes_analysis["empty_count"] > 0:
        add(
            "Safes",
            "warn",
            f"{safes_analysis['empty_count']} Safe(s) están vacíos (0 cuentas).",
            tip="Revisar si esos Safes siguen siendo necesarios; si no, eliminarlos para reducir la superficie de administración.",
        )

    suspended_users = sum(1 for r in users_analysis["rows"] if r["status"] == "Suspended")
    if suspended_users:
        add(
            "Users",
            "warn",
            f"{suspended_users} usuario(s) están con estado Suspended.",
            tip="Confirmar el motivo de la suspensión y reactivar o dar de baja definitivamente según corresponda.",
        )

    never_login_users = sum(
        1
        for r in users_analysis["rows"]
        if r["last_login"] == "Nunca" and (is_cloud or not r.get("system_user"))
    )
    if never_login_users:
        add(
            "Users",
            "warn",
            (
                f"{never_login_users} usuario(s) (Identity) nunca iniciaron sesión."
                if is_cloud
                else f"{never_login_users} usuario(s) nunca iniciaron sesión."
            ),
            tip=(
                "Confirmar si esos usuarios siguen siendo necesarios; si no, deshabilitarlos para "
                + (
                    "liberar licencia y reducir superficie de ataque."
                    if is_cloud
                    else "reducir la superficie de ataque."
                )
            ),
        )

    activity_total_known = account_activity["total_known"] or 1
    if account_activity["unused"] > 0:
        unused_pct = round((account_activity["unused"] / activity_total_known) * 100, 1)
        add(
            "Actividad (KPI's)",
            "warn",
            f"{account_activity['unused']} cuentas ({unused_pct}%) sin actividad vía PSM en los últimos "
            f"{account_activity['window_days']} días.",
            tip="Evaluar con el dueño del Safe si esas cuentas siguen siendo necesarias, dado que no se usaron vía PSM en el período relevado.",
        )

    if concurrent_sessions["long_session_count"] > 0:
        add(
            "Actividad (KPI's)",
            "warn",
            f"{concurrent_sessions['long_session_count']} sesión(es) PSM duraron "
            f"{concurrent_sessions['long_session_hours']}+ horas - revisar si quedaron abiertas sin cerrarse "
            f"correctamente.",
            tip="Revisar la configuración de timeout de sesión inactiva en PSM (Server Configuration > Timeout) para cerrar automáticamente sesiones colgadas.",
        )

    if concurrent_sessions["off_hours_pct"] >= 30:
        add(
            "Actividad (KPI's)",
            "info",
            f"{concurrent_sessions['off_hours_pct']}% de las sesiones PSM se iniciaron fuera del horario "
            f"laboral asumido (Lun-Vie 08:00-18:00 UTC).",
            tip="Confirmar si ese uso fuera de horario corresponde a guardias/automatizaciones esperadas, o si amerita revisar accesos inusuales.",
        )

    by_server = concurrent_sessions.get("by_server") or []
    if len(by_server) > 1:
        total_sessions = sum(s["sessions_counted"] for s in by_server) or 1
        top = by_server[0]
        top_pct = round((top["sessions_counted"] / total_sessions) * 100, 1)
        if top_pct >= 65:
            add(
                "Actividad (KPI's)",
                "info",
                f"El servidor PSM {top['server']} concentra el {top_pct}% de las sesiones - desbalanceo entre "
                f"nodos, evaluar redistribuir carga.",
                tip="Revisar el connection component / load balancer para distribuir mejor las conexiones entre los servidores PSM disponibles.",
            )

    if infra_summary:
        if infra_summary["services_stopped"] > 0:
            add(
                "Infraestructura",
                "crit",
                f"{infra_summary['services_stopped']} servicio(s) de CyberArk aparecen detenidos en los "
                f"servidores relevados.",
                tip="Iniciar los servicios detenidos desde el servidor correspondiente y revisar el Event Viewer de Windows para la causa raíz antes de reiniciarlos.",
            )
        if infra_summary["certs_expiring"] > 0:
            add(
                "Infraestructura",
                "crit",
                f"{infra_summary['certs_expiring']} certificado(s) (RDP/Entra ID) vencen en menos de 30 "
                f"días o ya vencieron.",
                tip="Renovar esos certificados antes de su vencimiento para evitar cortes de conectividad RDP o de la federación con Entra ID.",
            )

    if not findings:
        add("General", "ok", "No se detectaron desvíos relevantes con los umbrales configurados.")

    return findings


def _group_findings(findings):
    """Agrupa los hallazgos por categoría (orden fijo, ver
    _FINDINGS_CATEGORY_ORDER) y los ordena por severidad dentro de cada
    grupo, para que la pestaña Findings sea escaneable en vez de una lista
    plana de 20+ líneas sin prioridad."""
    by_category = {}
    for f in findings:
        by_category.setdefault(f["category"], []).append(f)
    for entries in by_category.values():
        entries.sort(key=lambda f: _FINDINGS_SEVERITY_ORDER.get(f["severity"], 9))

    ordered = [c for c in _FINDINGS_CATEGORY_ORDER if c in by_category]
    ordered += [c for c in by_category if c not in ordered]
    # "entries", no "items" - group.items chocaría con el método dict.items()
    # de Python al acceder por atributo desde Jinja2 (obj.items devuelve el
    # método bound, no la clave "items" del dict).
    return [
        {
            "category": c,
            "icon": _FINDINGS_CATEGORY_ICONS.get(c, _FINDINGS_CATEGORY_ICON_DEFAULT),
            "has_crit": any(f["severity"] == "crit" for f in by_category[c]),
            "entries": by_category[c],
        }
        for c in ordered
    ]


def build_html_report(
    client_name,
    environment_name,
    accounts_analysis,
    accounts_table,
    account_activity,
    concurrent_sessions,
    accounts_created_labels,
    accounts_created_values,
    compliance,
    compliance_breakdown,
    cpm_errors,
    components_summary,
    components_detailed,
    connectors_analysis,
    licenses,
    users_analysis,
    safes_analysis,
    safe_members_analysis,
    platforms_analysis,
    compliance_by_platform,
    output_path=None,
    client_logo_path=None,
    sia_connectors_analysis=None,
    sia_connectors_error=None,
    infra_data=None,
    excluded_tabs=None,
    client_templates_dir=None,
    deployment="privilege_cloud",
    data_warnings=None,
):

    generated_on = datetime.now().strftime("%d-%m-%Y %H:%M")
    excluded_tabs = excluded_tabs or set()
    # is_cloud=False (PAM Self-Hosted): no hay Identity ni endpoint de
    # licencias; las plantillas ocultan/reemplazan las secciones de Cloud
    # (Connector Management, KPIs y tabla de licencias de la pestaña Users).
    is_cloud = (deployment or "privilege_cloud").strip().lower() != "self_hosted"
    # Los datos de una pestaña excluida se siguen calculando y quedan en el
    # HTML igual (accesibles inspeccionando el DOM/código fuente) - lo único
    # que cambia es que no aparece su botón en la nav ni puede quedar activa
    # por defecto. No se saca su <section> del HTML a propósito: los
    # gráficos Chart.js y las tablas interactivas de _scripts.html.j2
    # referencian esos elementos por id sin chequear si existen, así que
    # sacar la sección rompería el reporte entero, no solo esa pestaña.
    first_visible_tab_id = next(
        (tab_id for tab_id, _ in TAB_ORDER if tab_id not in excluded_tabs), "overview"
    )

    brand_logo_uri = _image_to_data_uri(_BRAND_LOGO_PATH)
    brand_footer_logo_uri = _image_to_data_uri(_BRAND_FOOTER_LOGO_PATH)
    client_logo_uri = _image_to_data_uri(client_logo_path)

    status_labels, status_values = _counter_to_chart_data(accounts_analysis["status_counts"])
    status_color_map = {
        "Success": "#2e7d54",
        "Failure": "#c0392b",
        "In Process": "#b8860b",
        "Desconocido": "#8a9095",
    }
    status_colors = [status_color_map.get(label, "#8a9095") for label in status_labels]

    top_platforms = accounts_analysis["by_platform"].most_common(15)
    platform_labels = [p[0] for p in top_platforms]
    platform_values = [p[1] for p in top_platforms]

    change_labels = accounts_analysis["age_bucket_labels"]
    change_values = [accounts_analysis["age_buckets_change"].get(b, 0) for b in change_labels]
    verify_values = [accounts_analysis["age_buckets_verify"].get(b, 0) for b in change_labels]

    # ---- Accounts: cuentas sin Last Logon (PSM) registrado en el historial ----
    accounts_never_logon = sum(1 for r in accounts_table if r["last_logon"] == "Nunca")
    accounts_never_logon_pct = (
        round((accounts_never_logon / len(accounts_table)) * 100, 1) if accounts_table else 0.0
    )

    # ---- Sesiones: actividad de cuentas (PSM Connect, últimos N días) ----
    activity_view = []
    for r in account_activity["rows"]:
        v = dict(r)
        v["sessions_txt"] = "-" if r["sessions_30d"] is None else r["sessions_30d"]
        v["sessions_cls"] = (
            "muted" if r["sessions_30d"] is None else ("crit" if r["sessions_30d"] == 0 else "ok")
        )
        activity_view.append(v)

    top_active_labels = [r["account_name"] for r in account_activity["top_active"]]
    top_active_values = [r["sessions_30d"] for r in account_activity["top_active"]]

    # % nunca verificadas, para el gauge
    total_accounts = accounts_analysis["total"] or 1
    never_verified_pct = round((accounts_analysis["never_verified"] / total_accounts) * 100, 1)

    cat = compliance_breakdown["categories"]
    compliance_cat_labels = list(cat.keys())
    compliance_cat_values = [cat[k] for k in compliance_cat_labels]

    # ---- Vista de plataformas: agrega al dict de cada fila los campos
    # presentacionales (clases CSS, fallbacks "-") que antes se calculaban
    # inline en el loop de armado de HTML - el template solo hace {{ campo }}.
    platforms_view = []
    for p in platforms_analysis["rows"]:
        v = dict(p)
        v["active_cls"] = "ok" if p["active"] else "crit"
        v["rotation_cls"] = "crit" if p["no_periodic_rotation"] else "ok"
        v["auto_rotation"] = not p["no_periodic_rotation"]
        v["verify_cls"] = "warn" if p["no_periodic_verification"] else "ok"
        # "de sistema" real (no group): resaltar la ausencia de auto-reconcile
        # y de PSM monitoring solo donde aplica
        v["reconcile_auto_cls"] = (
            "warn" if (p["allow_manual_reconcile"] and not p["auto_reconcile"]) else "ok"
        )
        v["psm_monitoring_cls"] = (
            "crit"
            if (
                p["active"]
                and p["accounts"] > 0
                and not p["is_group"]
                and not p["psm_monitoring"]
                and (p["system_type"] or "").lower() != "application"
            )
            else "ok"
        )
        v["change_every_days_txt"] = (
            p["change_every_days"] if p["change_every_days"] is not None else "-"
        )
        v["verify_every_days_txt"] = (
            p["verify_every_days"] if p["verify_every_days"] is not None else "-"
        )
        v["allowed_safes_txt"] = p["allowed_safes"] or "-"
        v["description_txt"] = p["description"] or "-"
        platforms_view.append(v)

    # Las plataformas platformType == "group" son Account Groups (agrupadores
    # administrativos, ej. "Sample SSH Key Group Platform"), no representan
    # un system type real - se excluyen del gráfico para no ensuciarlo con
    # una categoría que no es un tipo de sistema (confirmado contra un
    # tenant real: las 3 plataformas "Misc" eran todas Account Groups).
    platform_system_type_counts = Counter(
        p["system_type"] or "Sin definir"
        for p in platforms_analysis["rows"]
        if (p["platform_type"] or "").lower() != "group"
    )
    platform_system_type_labels = [k for k, _ in platform_system_type_counts.most_common()]
    platform_system_type_values = [
        platform_system_type_counts[k] for k in platform_system_type_labels
    ]

    # ---- Vista de safes (tabla) ----
    safes_view = []
    for s in safes_analysis["rows"]:
        v = dict(s)
        v["olac_cls"] = "ok" if s["olac_enabled"] else "muted"
        v["accounts_cls"] = "crit" if s["accounts"] == 0 else ""
        if s["retention_days"] is not None:
            v["retention_txt"] = f"{s['retention_days']} días"
        elif s["retention_versions"] is not None:
            v["retention_txt"] = f"{s['retention_versions']} versiones"
        else:
            v["retention_txt"] = "-"
        safes_view.append(v)

    # ---- Bloque de safes: gráfico de retención ----
    retention_counts = safes_analysis["retention_days_counts"]
    # numérico ascendente primero, "Sin definir" siempre al final
    retention_keys = sorted(
        retention_counts.keys(), key=lambda k: (k == "Sin definir", k if isinstance(k, int) else 0)
    )
    retention_labels = [f"{k} días" if isinstance(k, int) else k for k in retention_keys]
    retention_values = [retention_counts[k] for k in retention_keys]

    # ---- Bloque de safe members: gráfico por tipo ----
    member_type_counts = safe_members_analysis["by_type"]
    member_type_labels = [k for k, _ in member_type_counts.most_common()]
    member_type_values = [member_type_counts[k] for k in member_type_labels]

    # ---- Bloque de usuarios (Users Details + Users by Type) ----
    if is_cloud:
        # Cloud: whitelist de los tipos que interesan (el resto son ruido).
        USERS_BY_TYPE_FILTER = ["ExtUser", "EPVUser", "PSM", "CPM"]
        users_type_labels = [t for t in USERS_BY_TYPE_FILTER if t in users_analysis["by_type"]]
    else:
        # Self-Hosted: hay muchos tipos on-prem (Built-InAdmins, PSMPServer,
        # AppProvider, ENE, PTA...) - se muestran los ~8 más numerosos.
        users_type_labels = [t for t, _ in users_analysis["by_type"].most_common(8)]
    users_type_values = [users_analysis["by_type"][t] for t in users_type_labels]

    # ---- Users by Last Logon Activity (gráfico replicado del VAReport
    # original de CyberArk). Reusa el mismo last_login_dt ya calculado por
    # analyze_users() - no requiere requests extra. Se excluyen las cuentas
    # de componente ("No disponible": no existen en el directorio de
    # Identity, no tienen Last Login real) y se suma aparte "Nunca se
    # logueó" para no perder ese dato, aunque no exista en el VAReport
    # original.
    _now_utc = datetime.now(UTC)
    USERS_LAST_LOGON_BUCKETS = [
        ("Entre 0 y 7 días", 7, "#2e7d54"),
        ("Entre 8 y 30 días", 30, "#6f9c5a"),
        ("Entre 31 y 90 días", 90, "#b8860b"),
        ("Entre 91 y 180 días", 180, "#c17a3d"),
        ("Entre 181 y 365 días", 365, "#cf5a3a"),
        ("Más de 365 días", None, "#c0392b"),
    ]
    last_logon_counts = Counter()
    for r in users_analysis["rows"]:
        if r["last_login"] == "No disponible":
            continue
        if r["last_login"] == "Nunca" or r["last_login_dt"] is None:
            last_logon_counts["Nunca se logueó"] += 1
            continue
        days = (_now_utc - r["last_login_dt"]).days
        for label, max_days, _ in USERS_LAST_LOGON_BUCKETS:
            if max_days is None or days <= max_days:
                last_logon_counts[label] += 1
                break

    last_logon_labels = [label for label, _, _ in USERS_LAST_LOGON_BUCKETS] + ["Nunca se logueó"]
    last_logon_values = [last_logon_counts[label] for label in last_logon_labels]
    last_logon_colors = [color for _, _, color in USERS_LAST_LOGON_BUCKETS] + ["#9aa0a5"]

    # ---- Tabla de usuarios inactivos (nunca se loguearon o llevan >=
    # INACTIVE_LOGIN_DAYS días sin hacerlo). Reusa el last_login_dt ya
    # calculado por analyze_users(), sin requests extra.
    #  - Cloud: solo EPVUser/ExtUser (= Privileged Standard/External User),
    #    porque son los que consumen licencia -> "candidatos a liberar".
    #  - Self-Hosted: cualquier usuario real (no de componente), porque acá
    #    el encuadre es "limpieza de usuarios inactivos", no licencias.
    LICENSE_USER_TYPE_LABELS = {
        "EPVUser": "Privileged Standard User",
        "ExtUser": "Privileged External User",
    }
    INACTIVE_LOGIN_DAYS = 30
    _LOGIN_STATE_SORT = {"never": 0, "stale": 1, "active": 2, "unknown": 3}

    license_users_view = []
    license_users_candidates = 0
    for r in users_analysis["rows"]:
        if is_cloud:
            if r["user_type"] not in LICENSE_USER_TYPE_LABELS:
                continue
            user_type_label = LICENSE_USER_TYPE_LABELS[r["user_type"]]
        else:
            if r.get("system_user"):
                continue
            user_type_label = r["user_type"]
        v = dict(r)
        v["user_type_label"] = user_type_label
        dt = r["last_login_dt"]
        if r["last_login"] == "Nunca":
            v["login_state"] = "never"
            v["days_since_login"] = None
        elif dt is not None:
            v["days_since_login"] = (_now_utc - dt).days
            v["login_state"] = "stale" if v["days_since_login"] >= INACTIVE_LOGIN_DAYS else "active"
        else:
            v["login_state"] = "unknown"
            v["days_since_login"] = None
        v["days_since_login_txt"] = (
            str(v["days_since_login"]) if v["days_since_login"] is not None else "-"
        )
        v["is_candidate"] = v["login_state"] in ("never", "stale")
        v["login_cls"] = {"never": "crit", "stale": "warn", "active": "ok", "unknown": "muted"}[
            v["login_state"]
        ]
        if v["is_candidate"]:
            license_users_candidates += 1
        license_users_view.append(v)
    license_users_view.sort(
        key=lambda v: (_LOGIN_STATE_SORT[v["login_state"]], -(v["days_since_login"] or 0))
    )
    license_users_total = len(license_users_view)
    license_users_candidates_pct = (
        round((license_users_candidates / license_users_total) * 100, 1)
        if license_users_total
        else 0.0
    )

    # ---- KPIs de usuario (self_hosted: reemplazan los KPI-hero de licencias
    # del tope de la pestaña Users, que on-prem no aplican). Se calculan
    # siempre - es barato y no molesta si no se usan. system_user excluye
    # componentes + predefinidos de CyberArk (Built-InAdmins...) + el propio
    # Service User del reporte (ver analyze_users).
    _real_users = [r for r in users_analysis["rows"] if not r.get("system_user")]
    users_real_total = len(_real_users)
    users_enabled = sum(1 for r in _real_users if r["status"] == "Enabled")
    users_suspended = sum(1 for r in _real_users if r["status"] == "Suspended")
    users_disabled = sum(1 for r in _real_users if r["status"] == "Disabled")
    users_never_login = sum(1 for r in _real_users if r["last_login"] == "Nunca")
    users_stale = sum(
        1
        for r in _real_users
        if r["last_login_dt"] is not None
        and (_now_utc - r["last_login_dt"]).days >= INACTIVE_LOGIN_DAYS
    )
    users_inactive_pct = (
        round(((users_never_login + users_stale) / users_real_total) * 100, 1)
        if users_real_total
        else 0.0
    )

    # ---- Connector Management: vista de tabla ----
    connectors_view = []
    for c in connectors_analysis:
        v = dict(c)
        v["status_cls"] = "ok" if c["is_ok"] else "crit"
        v["cpu_txt"] = f"{c['used_cpu']}%" if c["used_cpu"] is not None else "-"
        v["mem_txt"] = f"{c['used_memory']} GB" if c["used_memory"] is not None else "-"
        v["update_cls"] = "muted" if c["update_available"] == "Al día" else "crit"
        v["component_label"] = (
            f"{c['component_name']} ({c['acronym']})" if c["acronym"] else c["component_name"]
        )
        connectors_view.append(v)
    connectors_ok_count = sum(1 for c in connectors_view if c["is_ok"])

    # ---- SIA Connectors (servicio separado, dpa.cyberark.cloud) ----
    sia_connectors_view = []
    for c in sia_connectors_analysis or []:
        v = dict(c)
        v["status_cls"] = "ok" if c["is_ok"] else "crit"
        v["update_txt"] = (
            "Al día"
            if c["is_latest_version"]
            else (c.get("upgrade_version") or "Actualización disponible")
        )
        v["update_cls"] = "muted" if c["is_latest_version"] else "crit"
        sia_connectors_view.append(v)
    sia_connectors_ok_count = sum(1 for c in sia_connectors_view if c["is_ok"])

    if sia_connectors_view:
        sia_connectors_empty_msg = None
    elif sia_connectors_error:
        # Un 403 aquí casi siempre es licencia/rol de SIA, no un bug - se
        # muestra el motivo real en vez del mensaje genérico de "no
        # configurado", para que quien lea el reporte no pierda tiempo
        # pensando que es un problema del script. sia_connectors_error va
        # crudo (sin escapar acá) - el autoescape de Jinja2 lo escapa una
        # sola vez al renderizarlo; escaparlo acá también lo dejaría
        # doble-escapado.
        sia_connectors_empty_msg = f"No se pudo consultar SIA Connectors: {sia_connectors_error}"
    else:
        sia_connectors_empty_msg = "SIA Connectors no está configurado para este tenant, o el tenant no tiene conectores SIA."

    # ---- Componentes activos (detalle, color-coded por antigüedad) ----
    bucket_class = {
        "recent": "row-recent",
        "stale": "row-stale",
        "month": "row-month",
        "old": "row-old",
        "never": "row-never",
    }
    active_components_view = []
    for c in components_detailed:
        v = dict(c)
        v["row_cls"] = bucket_class.get(c["bucket"], "")
        active_components_view.append(v)

    # ---- Resumen de componentes (usado en Overview y en la tabla Components) ----
    components_view = []
    for comp_id, info in components_summary.items():
        label = COMPONENT_NAME_MAP.get(comp_id, comp_id)
        available = info["available"]
        # ComponentVersion sí viene por instancia en ComponentsMonitoringDetails
        # (a diferencia de "actualización pendiente", que esta API no expone
        # para PVWA/CPM/PSM - eso solo existe para Connector Management/SIA,
        # que son on-prem y sí lo reportan) - se junta acá para mostrarla
        # junto a la barra de Componentes Core sin pegarle a la API de nuevo.
        versions = sorted(
            {
                i.get("ComponentVersion")
                for i in (info.get("instances") or [])
                if i.get("ComponentVersion")
            }
        )
        components_view.append(
            {
                "comp_id": comp_id,
                "label": label,
                "available": available,
                "total_instances": info.get("total_instances"),
                "connected": info.get("connected"),
                "disconnected": info.get("disconnected"),
                "value_cls": "value" if available else "value muted",
                "value_txt": info.get("total_instances") if available else "-",
                "disconnected_cls": "crit" if (available and info.get("disconnected")) else "ok",
                "versions_txt": " / ".join(versions) if versions else None,
            }
        )

    # ---- Licencias ----
    licenses_view = []
    for row in licenses:
        v = dict(row)
        v["bar_color"] = (
            "#c0392b" if row["pct"] >= 90 else ("#b8860b" if row["pct"] >= 70 else "#2e7d54")
        )
        licenses_view.append(v)

    # ---- KPIs "héroe" de la pestaña Users (Licencias) ----
    def _short_license_name(name):
        return (name or "").replace("Privileged ", "").replace(" User", "").strip() or (name or "")

    licenses_total_capacity = sum(row["total"] for row in licenses_view)
    licenses_capacity_subtitle = (
        " + ".join(f"{row['total']} {_short_license_name(row['name'])}" for row in licenses_view)
        if licenses_view
        else "Sin licencias configuradas"
    )
    licenses_avg_pct = (
        round(sum(row["pct"] for row in licenses_view) / len(licenses_view), 1)
        if licenses_view
        else 0.0
    )
    licenses_avg_pct_cls = (
        "crit" if licenses_avg_pct >= 90 else ("warn" if licenses_avg_pct >= 70 else "ok")
    )
    licenses_avg_pct_subtitle = (
        "Cerca del límite contratado"
        if licenses_avg_pct >= 90
        else ("Uso moderado" if licenses_avg_pct >= 70 else "Uso saludable")
    )

    # ---- KPIs "héroe" de la card de Resumen general (Overview) ----
    hero_failure_count = cpm_errors["accounts_with_failed_status"]
    hero_failure_pct = round((hero_failure_count / total_accounts) * 100, 1)
    # La licencia que se muestra en el KPI héroe es la de mayor % de uso
    # (la más cerca del límite) - no siempre es "Standard", depende del
    # tenant, así que se elige dinámicamente en vez de buscar por nombre.
    hero_top_license = max(licenses_view, key=lambda r: r["pct"]) if licenses_view else None
    hero_components_connected = sum(c["connected"] for c in components_view if c["available"])
    hero_components_total = sum(c["total_instances"] for c in components_view if c["available"])
    hero_components_down = sum(1 for c in components_view if c["available"] and c["disconnected"])

    # ---- KPIs "héroe" de la pestaña Components (mismo criterio que arriba,
    # sumando Connector Management/SIA que no entran en el hero de Overview) ----
    _CORE_COMPONENT_SHORT_NAME = {
        "PVWA": "PVWA",
        "SessionManagement": "PSM",
        "CPM": "CPM",
        "AIM": "AIM",
    }
    core_components_available = [c for c in components_view if c["available"]]
    core_instance_counts = {c["total_instances"] for c in core_components_available}
    if core_components_available and len(core_instance_counts) == 1:
        core_components_subtitle = (
            f"{' + '.join(_CORE_COMPONENT_SHORT_NAME.get(c['comp_id'], c['label']) for c in core_components_available)}"
            f", {core_instance_counts.pop()} instancias c/u"
        )
    elif core_components_available:
        core_components_subtitle = " + ".join(
            _CORE_COMPONENT_SHORT_NAME.get(c["comp_id"], c["label"])
            for c in core_components_available
        )
    else:
        core_components_subtitle = "Sin componentes core disponibles"

    connectors_hosts = {c["host_name"] for c in connectors_view}
    connectors_platform_types = {c["platform_type"] for c in connectors_view if c["platform_type"]}
    connectors_hosts_subtitle = (
        f"{len(connectors_hosts)} host(s) {connectors_platform_types.pop()}"
        if len(connectors_platform_types) == 1
        else f"{len(connectors_hosts)} host(s)"
    )

    # "no está al día" ya viene resuelto en update_cls (ver connectors_view/
    # sia_connectors_view más arriba) - se reusa en vez de recalcularlo.
    connectors_updates_pending = sum(1 for c in connectors_view if c["update_cls"] == "crit")
    sia_updates_pending = sum(1 for c in sia_connectors_view if c["update_cls"] == "crit")
    components_updates_pending = connectors_updates_pending + sia_updates_pending
    _updates_subtitle_parts = []
    if connectors_updates_pending:
        _updates_subtitle_parts.append(f"{connectors_updates_pending} Connector Mgmt")
    if sia_updates_pending:
        _updates_subtitle_parts.append(f"{sia_updates_pending} SIA")
    components_updates_subtitle = (
        " + ".join(_updates_subtitle_parts) if _updates_subtitle_parts else "Todo al día"
    )

    sia_active_sessions_total = sum(c["sessions"] for c in sia_connectors_view)
    sia_sessions_subtitle = (
        f"{len(sia_connectors_view)} conector(es), {sia_connectors_ok_count} activo(s)"
        if sia_connectors_view
        else ""
    )

    # ---- Antigüedad de contraseñas (Overview): mismos buckets que ya usa
    # cambiar/verificar en Accounts (age_bucket_labels), con un color por
    # bucket (verde -> rojo, más vieja = más roja) para el listado
    # compacto de la card "Antigüedad de contraseñas".
    _AGE_BUCKET_COLORS = {
        "0-30 días": "#2e7d54",
        "1-2 meses": "#6f9c5a",
        "2-3 meses": "#9bab4f",
        "3-6 meses": "#b8860b",
        "6-12 meses": "#c17a3d",
        "1-2 años": "#cf5a3a",
        "+2 años": "#c0392b",
        "Never": "#9aa0a5",
    }
    age_bucket_view = [
        {
            "label": "Nunca gestionada" if label == "Never" else label,
            "count": accounts_analysis["age_buckets_change"].get(label, 0),
            "color": _AGE_BUCKET_COLORS.get(label, "#9aa0a5"),
        }
        for label in accounts_analysis["age_bucket_labels"]
    ]

    # ---- Infraestructura (datos manuales de servidores, HealthCheck_*.json) ----
    infra_servers_view = []
    infra_services_view = []
    infra_disks_view = []
    services_running = services_stopped = 0
    for s in (infra_data or {}).get("servers", []):
        v = dict(s)
        v["cpu_txt"] = f"{s['cpu_pct']}%" if s["cpu_pct"] is not None else "-"
        v["ram_txt"] = (
            f"{s['ram_pct']}% ({s['ram_used_gb']}/{s['ram_total_gb']} GB)"
            if s["ram_pct"] is not None
            else "-"
        )
        cert = s.get("rdp_certificate")
        v["rdp_cert_cls"] = (
            "crit"
            if (cert and cert["expired"])
            else ("warn" if (cert and cert["expiring_soon"]) else "ok")
        )
        infra_servers_view.append(v)

        for svc in s["services"]:
            is_running = svc["running"]
            services_running += 1 if is_running else 0
            services_stopped += 0 if is_running else 1
            infra_services_view.append(
                {
                    "hostname": s["hostname"],
                    "name": svc["name"],
                    "status_txt": svc["status_txt"],
                    "status_cls": "ok" if is_running else "crit",
                }
            )

        for d in s["disks"]:
            infra_disks_view.append(
                {
                    "hostname": s["hostname"],
                    "unit": d["unit"],
                    "pct": d["pct"],
                    "used_gb": d["used_gb"],
                    "total_gb": d["total_gb"],
                }
            )

    rdp_certs_view = [
        {"hostname": s["hostname"], **s["rdp_certificate"], "cls": s["rdp_cert_cls"]}
        for s in infra_servers_view
        if s.get("rdp_certificate")
    ]

    entra_id_cert = (infra_data or {}).get("entra_id_certificate")
    entra_id_cert_view = None
    if entra_id_cert:
        entra_id_cert_view = dict(entra_id_cert)
        entra_id_cert_view["cls"] = (
            "crit"
            if entra_id_cert["expired"]
            else ("warn" if entra_id_cert["expiring_soon"] else "ok")
        )

    certs_expiring = sum(1 for c in rdp_certs_view if c["expiring_soon"])
    if entra_id_cert_view and entra_id_cert_view["expiring_soon"]:
        certs_expiring += 1

    infra_empty_msg = None
    if infra_data is None:
        infra_empty_msg = "Sin datos de infraestructura para este reporte."
    elif not infra_servers_view:
        infra_empty_msg = (
            "Sin datos de infraestructura: no se cargaron archivos HealthCheck_*.json "
            "de los servidores del cliente."
        )

    infra_host_labels = [s["hostname"] for s in infra_servers_view]
    infra_cpu_values = [s["cpu_pct"] for s in infra_servers_view]
    infra_ram_values = [s["ram_pct"] for s in infra_servers_view]
    infra_disk_labels = [f"{d['hostname']} {d['unit']}" for d in infra_disks_view]
    infra_disk_values = [d["pct"] for d in infra_disks_view]

    infra_summary = {"services_stopped": services_stopped, "certs_expiring": certs_expiring}

    # ---- Findings ----
    findings = _build_findings(
        accounts_analysis,
        compliance_breakdown,
        cpm_errors,
        components_summary,
        licenses,
        platforms_analysis,
        safes_analysis,
        users_analysis,
        connectors_analysis,
        sia_connectors_analysis,
        account_activity,
        concurrent_sessions,
        accounts_never_logon,
        accounts_never_logon_pct,
        infra_summary=infra_summary if infra_servers_view else None,
        is_cloud=is_cloud,
    )
    findings_grouped = _group_findings(findings)
    findings_crit = sum(1 for f in findings if f["severity"] == "crit")
    findings_warn = sum(1 for f in findings if f["severity"] == "warn")
    findings_info = sum(1 for f in findings if f["severity"] in ("info", "ok"))
    # Hallazgos críticos sin agrupar, para el resumen de Overview - ahí
    # interesa una lista corta de "lo más urgente" ya priorizado, no
    # navegar por categoría como en la pestaña Findings completa.
    findings_crit_list = [f for f in findings if f["severity"] == "crit"]

    context = {
        "report_title": REPORT_TITLE,
        "report_acronym": REPORT_ACRONYM,
        "client_name": client_name,
        "environment_name": environment_name,
        "generated_on": generated_on,
        # is_cloud=False (PAM Self-Hosted): Connector Management y SIA son
        # servicios SaaS de ISPSS que on-prem no existen - las plantillas
        # ocultan sus secciones en vez de mostrarlas vacías / "no configurado".
        "is_cloud": is_cloud,
        "brand_logo_uri": brand_logo_uri,
        "brand_footer_logo_uri": brand_footer_logo_uri,
        "client_logo_uri": client_logo_uri,
        "excluded_tabs": excluded_tabs,
        # Avisos de datos incompletos (fuentes que fallaron, caché, archivos
        # de infra ignorados) - se muestran como banner arriba del reporte
        # para que un cero o un % no se lea como dato real.
        "data_warnings": data_warnings or [],
        "first_visible_tab_id": first_visible_tab_id,
        "tab_order": TAB_ORDER,
        "accounts_analysis": accounts_analysis,
        "accounts_table": accounts_table,
        "accounts_never_logon": accounts_never_logon,
        "accounts_never_logon_pct": accounts_never_logon_pct,
        "account_activity": account_activity,
        "activity_view": activity_view,
        "concurrent_sessions": concurrent_sessions,
        "compliance": compliance,
        "compliance_breakdown": compliance_breakdown,
        "cpm_errors": cpm_errors,
        "components_view": components_view,
        "active_components_view": active_components_view,
        "connectors_view": connectors_view,
        "connectors_ok_count": connectors_ok_count,
        "sia_connectors_view": sia_connectors_view,
        "sia_connectors_ok_count": sia_connectors_ok_count,
        "sia_connectors_empty_msg": sia_connectors_empty_msg,
        "licenses_view": licenses_view,
        "licenses_total_capacity": licenses_total_capacity,
        "licenses_capacity_subtitle": licenses_capacity_subtitle,
        "licenses_avg_pct": licenses_avg_pct,
        "licenses_avg_pct_cls": licenses_avg_pct_cls,
        "licenses_avg_pct_subtitle": licenses_avg_pct_subtitle,
        "hero_failure_count": hero_failure_count,
        "hero_failure_pct": hero_failure_pct,
        "hero_top_license": hero_top_license,
        "hero_components_connected": hero_components_connected,
        "hero_components_total": hero_components_total,
        "hero_components_down": hero_components_down,
        "core_components_subtitle": core_components_subtitle,
        "connectors_hosts_subtitle": connectors_hosts_subtitle,
        "components_updates_pending": components_updates_pending,
        "components_updates_subtitle": components_updates_subtitle,
        "sia_active_sessions_total": sia_active_sessions_total,
        "sia_sessions_subtitle": sia_sessions_subtitle,
        "age_bucket_view": age_bucket_view,
        "users_analysis": users_analysis,
        "license_users_view": license_users_view,
        "license_users_total": license_users_total,
        "license_users_candidates": license_users_candidates,
        "license_users_candidates_pct": license_users_candidates_pct,
        "inactive_login_days": INACTIVE_LOGIN_DAYS,
        # KPIs de usuario para self_hosted (pestaña Users, reemplazan licencias)
        "users_real_total": users_real_total,
        "users_enabled": users_enabled,
        "users_suspended": users_suspended,
        "users_disabled": users_disabled,
        "users_never_login": users_never_login,
        "users_stale": users_stale,
        "users_inactive_pct": users_inactive_pct,
        "safes_analysis": safes_analysis,
        "safes_view": safes_view,
        "safe_members_analysis": safe_members_analysis,
        "platforms_analysis": platforms_analysis,
        "platforms_view": platforms_view,
        "compliance_by_platform": compliance_by_platform,
        "findings_grouped": findings_grouped,
        "findings_crit": findings_crit,
        "findings_warn": findings_warn,
        "findings_info": findings_info,
        "findings_crit_list": findings_crit_list,
        "never_verified_pct": never_verified_pct,
        "infra_servers_view": infra_servers_view,
        "infra_services_view": infra_services_view,
        "infra_disks_view": infra_disks_view,
        "rdp_certs_view": rdp_certs_view,
        "entra_id_cert_view": entra_id_cert_view,
        "infra_empty_msg": infra_empty_msg,
        "services_running": services_running,
        "services_stopped": services_stopped,
        "certs_expiring": certs_expiring,
        # ---- datos de gráficos (Chart.js, vía |tojson en el <script>) ----
        "status_labels": status_labels,
        "status_values": status_values,
        "status_colors": status_colors,
        "platform_labels": platform_labels,
        "platform_values": platform_values,
        "change_labels": change_labels,
        "change_values": change_values,
        "verify_values": verify_values,
        "compliance_cat_labels": compliance_cat_labels,
        "compliance_cat_values": compliance_cat_values,
        "platform_system_type_labels": platform_system_type_labels,
        "platform_system_type_values": platform_system_type_values,
        "retention_labels": retention_labels,
        "retention_values": retention_values,
        "member_type_labels": member_type_labels,
        "member_type_values": member_type_values,
        "accounts_created_labels": accounts_created_labels,
        "accounts_created_values": accounts_created_values,
        "users_type_labels": users_type_labels,
        "users_type_values": users_type_values,
        "last_logon_labels": last_logon_labels,
        "last_logon_values": last_logon_values,
        "last_logon_colors": last_logon_colors,
        "infra_host_labels": infra_host_labels,
        "infra_cpu_values": infra_cpu_values,
        "infra_ram_values": infra_ram_values,
        "infra_disk_labels": infra_disk_labels,
        "infra_disk_values": infra_disk_values,
        "top_active_labels": top_active_labels,
        "top_active_values": top_active_values,
    }

    env = _make_jinja_env(client_templates_dir)
    html_out = env.get_template("report.html.j2").render(**context)
    if output_path is None:
        return html_out
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_out)

    return output_path
