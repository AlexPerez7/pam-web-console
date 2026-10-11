"""
Clasificación de `action` del audit log para la página Auditoría
(docs/specs/done/audit-page.md, Tanda 1 — señal sobre ruido). Puro, sin I/O.

`classify()` separa señal de ruido (R1/R2): una `action` nueva que no
matchea ninguna regla se trata como `"cambio"` — nunca se oculta algo por
no conocerlo. `category()` alimenta el filtro por categoría del frontend
(R4); una acción nueva sin categoría cae en `"Administración"`.

`KNOWN_ACTIONS` es el registro de toda `action` que existe hoy en el
código (mismo criterio que `test_audit_catalog.py`, que escanea el código
y falla si aparece una `action` nueva sin agregar acá) — así una acción
nueva que por casualidad matchee mal una regla de `classify`/`category`
(ej. terminar en `.list` sin ser una lectura) se nota en CI en vez de
quedar mal clasificada en silencio.

`label()` (Tanda 2, R6) traduce la `action` a español para la columna
"Acción" — una acción sin traducción explícita muestra el código tal cual
(nunca "undefined" ni una etiqueta inventada). `report.{name}` es dinámica
(un item por reporte del catálogo, `app/api/reports.py`): sin entrada
explícita en `_LABELS`, cae en un genérico "Corrió el reporte '{name}'".
"""

from __future__ import annotations

Kind = str  # "consulta" | "cambio" | "sensible" | "acceso"

CATEGORIES = (
    "Cuentas",
    "Safes",
    "Plataformas",
    "Contraseñas y secretos",
    "Sesiones y grabaciones",
    "Identity",
    "Alero",
    "SIA",
    "Reportes",
    "Acceso a PAM Web Console",
    "Administración",
)

_SENSIBLE = frozenset(
    {
        "account.reveal",
        "account.reveal.bulk",
        "account.reveal.history",
        "account.backup.reveal",
        "recording.play",
        "recording.play.mp4",
        "platform.backup.download",
    }
)

_ACCESO_PREFIXES = ("auth.login", "operator.")
_ACCESO_EXACT = frozenset({"auth.logout", "auth.refresh.reuse"})

_CONSULTA_PREFIXES = ("report.",)
_CONSULTA_EXACT = frozenset({"auth.refresh", "dashboard.view", "tenant.server_info"})
_CONSULTA_SUFFIXES = (
    ".list",
    ".get",
    ".view",
    ".search",
    ".activities",
    ".summary",
    ".members",
    ".export",
)


def classify(action: str) -> Kind:
    if action in _SENSIBLE:
        return "sensible"
    if action in _ACCESO_EXACT or action.startswith(_ACCESO_PREFIXES):
        return "acceso"
    if (
        action in _CONSULTA_EXACT
        or action.startswith(_CONSULTA_PREFIXES)
        or action.endswith(_CONSULTA_SUFFIXES)
    ):
        return "consulta"
    return "cambio"


def category(action: str) -> str:
    if (
        action.startswith("account.reveal")
        or action.startswith("account.backup")
        or action.startswith("secrets.")
    ):
        return "Contraseñas y secretos"
    if action.startswith("account.password_versions") or action in {
        "account.change",
        "account.change.next",
        "account.change.vault_only",
        "account.verify",
        "account.reconcile",
    }:
        return "Contraseñas y secretos"
    if (
        action.startswith("account.")
        or action.startswith("discovered_account.")
        or action.startswith("user.")
        or action.startswith("group.")
        or action == "member.search"
    ):
        return "Cuentas"
    if action.startswith("safe."):
        return "Safes"
    if action.startswith("platform."):
        return "Plataformas"
    if (
        action.startswith("session.")
        or action.startswith("recording.")
        or action.startswith("psm.")
    ):
        return "Sesiones y grabaciones"
    if action.startswith("identity_"):
        return "Identity"
    if action.startswith("alero_"):
        return "Alero"
    if action.startswith("sia."):
        return "SIA"
    if (
        action.startswith("report.")
        or action.startswith("dashboard.")
        or action.startswith("compliance.")
        or action.startswith("troubleshooting.")
        or action.startswith("system_health.")
        or action.startswith("vendor_status.")
        or action.startswith("alert.")
        or action == "component.list"
    ):
        return "Reportes"
    if action.startswith("auth."):
        return "Acceso a PAM Web Console"
    return "Administración"  # operator.*, tenant.*, campaign.*, kb.*, itr.*, approval.*, y lo no listado


_LABELS: dict[str, str] = {
    "account.activities": "Consultó la actividad de una cuenta",
    "account.backup.restore": "Restauró una cuenta desde un backup",
    "account.backup.reveal": "Reveló la contraseña de un backup",
    "account.change": "Disparó el cambio de contraseña (CPM)",
    "account.change.next": "Cambió la próxima contraseña programada",
    "account.change.vault_only": "Cambió la contraseña sólo en el Vault",
    "account.create": "Dio de alta una cuenta",
    "account.create.bulk": "Dio de alta cuentas en lote",
    "account.create.bulk.preview": "Previsualizó el alta de cuentas en lote",
    "account.delete": "Eliminó una cuenta",
    "account.delete.bulk": "Eliminó varias cuentas",
    "account.delete.bulk.preview": "Previsualizó la eliminación de varias cuentas",
    "account.export": "Exportó cuentas",
    "account.get": "Consultó una cuenta",
    "account.link": "Vinculó una cuenta logon/reconcile",
    "account.list": "Listó cuentas",
    "account.move": "Movió una cuenta de Safe",
    "account.move.bulk": "Movió varias cuentas de Safe",
    "account.move.bulk.preview": "Previsualizó el movimiento de varias cuentas",
    "account.move.rollback_failed": "Falló el rollback de un movimiento de cuenta",
    "account.password_versions.list": "Consultó el historial de versiones de contraseña",
    "account.reveal": "Reveló la contraseña",
    "account.reveal.bulk": "Reveló contraseñas en lote",
    "account.reveal.history": "Reveló una versión anterior de la contraseña",
    "account.search": "Buscó cuentas",
    "account.summary": "Consultó el resumen de cuentas",
    "account.sync": "Sincronizó el inventario de cuentas",
    "account.update": "Actualizó una cuenta",
    "account.verify": "Disparó la verificación de contraseña (CPM)",
    "alero_activity.list": "Consultó el feed de actividad de Alero",
    "alero_role.list": "Listó roles de Alero",
    "alero_user.get": "Consultó un usuario de Alero",
    "alero_user.list": "Listó usuarios de Alero",
    "alero_vendor.get": "Consultó un proveedor de Alero",
    "alero_vendor.list": "Listó proveedores de Alero",
    "alert.heartbeat.sent": "Se envió el heartbeat diario de las alertas",
    "alert.rule.escalated": "Una alerta activa empeoró",
    "alert.rule.notify_failed": "No se pudo entregar el aviso de una alerta (se reintenta)",
    "alert.rule.opened": "Se abrió una alerta",
    "alert.rule.renotified": "Se reenvió el aviso de una alerta activa",
    "alert.rule.resolved": "Se resolvió una alerta",
    "approval.cancel": "Canceló una solicitud de aprobación",
    "approval.reject": "Rechazó una solicitud de aprobación",
    "audit.chain_incident_documented": "Documentó un incidente de la cadena de auditoría",
    "audit.export": "Exportó el registro de auditoría",
    "auth.login": "Inició sesión",
    "auth.login.lockout_degraded": "Inició sesión (bloqueo por intentos fallidos degradado)",
    "auth.logout": "Cerró sesión",
    "auth.refresh": "Renovó la sesión",
    "auth.refresh.reuse": "Reutilizó un refresh token (posible robo de sesión)",
    "campaign.create": "Creó una campaña",
    "campaign.delete": "Eliminó una campaña",
    "campaign.get": "Consultó una campaña",
    "campaign.list": "Listó campañas",
    "compliance.account_status.view": "Consultó el cumplimiento de una cuenta",
    "compliance.accounts.view": "Consultó el desglose de cumplimiento",
    "compliance.search": "Buscó cuentas por cumplimiento",
    "compliance.exemption.create": "Excluyó una cuenta del % de cumplimiento",
    "compliance.exemption.delete": "Quitó la exclusión de cumplimiento de una cuenta",
    "component.list": "Consultó el estado de los componentes",
    "dashboard.view": "Consultó el Dashboard",
    "discovered_account.activities": "Consultó la actividad de una cuenta descubierta",
    "discovered_account.delete": "Eliminó una cuenta descubierta",
    "discovered_account.ignore": "Ignoró una cuenta descubierta",
    "discovered_account.list": "Listó cuentas descubiertas",
    "discovered_account.onboard": "Dio de alta una cuenta descubierta",
    "discovered_account.restore": "Restauró una cuenta descubierta ignorada",
    "group.create": "Creó un grupo",
    "group.delete": "Eliminó un grupo",
    "group.export": "Exportó grupos",
    "group.list": "Listó grupos",
    "group.member.add": "Agregó un miembro a un grupo",
    "group.member.remove": "Quitó un miembro de un grupo",
    "group.members": "Consultó los miembros de un grupo",
    "group.search": "Buscó grupos",
    "group.update": "Actualizó un grupo",
    "identity_principal.search": "Buscó usuarios/roles de Identity",
    "identity_role.create": "Creó un rol de Identity",
    "identity_role.list": "Listó roles de Identity",
    "identity_role.member.add": "Agregó un miembro a un rol de Identity",
    "identity_role.members": "Consultó los miembros de un rol de Identity",
    "identity_user.get": "Consultó un usuario de Identity",
    "identity_user.list": "Listó usuarios de Identity",
    "identity_user.lock": "Bloqueó o desbloqueó un usuario de Identity",
    "itr.infra_file.delete": "Eliminó un archivo de infraestructura del reporte ITR",
    "itr.infra_file.upload": "Subió un archivo de infraestructura del reporte ITR",
    "kb.create": "Creó un artículo de la base de conocimiento",
    "kb.delete": "Eliminó un artículo de la base de conocimiento",
    "kb.list": "Listó artículos de la base de conocimiento",
    "kb.update": "Actualizó un artículo de la base de conocimiento",
    "member.search": "Buscó miembros para agregar a una Safe",
    "operator.assignments": "Consultó o actualizó las asignaciones RBAC de un operador",
    "operator.create": "Creó un operador",
    "operator.password_change": "Cambió su contraseña",
    "operator.reset_password": "Reseteó la contraseña de un operador",
    "operator.reset_totp": "Reseteó el TOTP de un operador",
    "operator.update": "Actualizó un operador",
    "platform.activate": "Activó una plataforma",
    "platform.backup.download": "Descargó el backup de una plataforma",
    "platform.config.view": "Consultó la configuración completa de una plataforma",
    "platform.delete": "Eliminó una plataforma",
    "platform.duplicate": "Duplicó una plataforma",
    "platform.export": "Exportó plataformas",
    "platform.import": "Importó una plataforma",
    "platform.list": "Listó plataformas",
    "platform.psm.get": "Consultó el PSM de una plataforma",
    "platform.psm.rollback_failed": "Falló el rollback de un cambio de PSM",
    "platform.psm.update": "Actualizó el PSM de una plataforma",
    "platform.psm.update.bulk": "Reasignó el PSM de varias plataformas",
    "platform.psm.update.bulk.preview": "Previsualizó la reasignación de PSM en lote",
    "platform.search": "Buscó plataformas",
    "psm.servers.list": "Listó servidores PSM",
    "recording.activities": "Consultó la actividad de una grabación",
    "recording.list": "Listó grabaciones",
    "recording.play": "Reprodujo una grabación",
    "recording.play.mp4": "Descargó una grabación en MP4",
    "report.itr.generate": "Generó el reporte ITR",
    "safe.create": "Creó una Safe",
    "safe.defaults": "Consultó los valores por defecto de Safes",
    "safe.delete": "Eliminó una Safe",
    "safe.export": "Exportó Safes",
    "safe.list": "Listó Safes",
    "safe.member.add": "Agregó un miembro a una Safe",
    "safe.member.remove": "Quitó un miembro de una Safe",
    "safe.member.update": "Actualizó los permisos de un miembro de Safe",
    "safe.members": "Consultó los miembros de una Safe",
    "safe.search": "Buscó Safes",
    "safe.update": "Actualizó una Safe",
    "session.list": "Listó sesiones en vivo",
    "session.terminate": "Terminó una sesión",
    "setup.connector_pools.list": "Listó los pools de conectores",
    "setup.connectors.list": "Listó los conectores del sistema (Connector Management)",
    "setup.sia_connectors.list": "Listó los conectores SIA",
    "sia.policies.list": "Listó las políticas de acceso de SIA",
    "sia.policy.get": "Consultó una política de acceso de SIA",
    "sia.secrets.list": "Listó los secretos de SIA (sólo metadatos)",
    "secrets.entry.set": "Actualizó un secreto del almacén (consola)",
    "secrets.tenant_secret.update": "Cambió la credencial del service user de un tenant (consola)",
    "failback.import.start": "Inició la importación del audit del standby (fail-back)",
    "failback.import.end": "Terminó la importación del audit del standby (fail-back)",
    "sia.session.get": "Consultó una sesión de SIA",
    "sia.sessions.list": "Listó las sesiones de SIA",
    "sia.settings.get": "Consultó la configuración de SIA",
    "sia.strong_accounts.list": "Listó las strong accounts de SIA (sólo metadatos)",
    "sia.targets.list": "Listó los targets de SIA",
    "system_health.view": "Consultó el estado de los componentes (System Health)",
    "tenant.compliance_settings.update": "Actualizó la configuración de cumplimiento del tenant",
    "tenant.logo.delete": "Eliminó el logo del tenant",
    "tenant.logo.update": "Actualizó el logo del tenant",
    "tenant.seed": "Cargó o actualizó tenants desde el seed (consola)",
    "tenant.server_info": "Consultó la información del servidor",
    "troubleshooting.account_errors.view": "Consultó los errores de CPM",
    "user.activate": "Reactivó un usuario",
    "user.create": "Creó un usuario",
    "user.delete": "Eliminó un usuario",
    "user.export": "Exportó usuarios",
    "user.get": "Consultó un usuario",
    "user.list": "Listó usuarios",
    "user.search": "Buscó usuarios",
    "user.update": "Actualizó un usuario",
    "vendor_status.view": "Consultó el estado de CyberArk (status.cyberark.com)",
}


def label(action: str) -> str:
    if action in _LABELS:
        return _LABELS[action]
    if action.startswith("report."):
        name = action.removeprefix("report.")
        return f"Corrió el reporte '{name}'"
    return action  # sin traducción explícita: el código, nunca "undefined"


KNOWN_ACTIONS = frozenset(
    {
        "account.activities",
        "account.backup.restore",
        "account.backup.reveal",
        "account.change",
        "account.change.next",
        "account.change.vault_only",
        "account.create",
        "account.create.bulk",
        "account.create.bulk.preview",
        "account.delete",
        "account.delete.bulk",
        "account.delete.bulk.preview",
        "account.export",
        "account.get",
        "account.link",
        "account.list",
        "account.move",
        "account.move.bulk",
        "account.move.bulk.preview",
        "account.move.rollback_failed",
        "account.password_versions.list",
        "account.reveal",
        "account.reveal.bulk",
        "account.reveal.history",
        "account.search",
        "account.summary",
        "account.sync",
        "account.update",
        "account.verify",
        "alero_activity.list",
        "alero_role.list",
        "alero_user.get",
        "alero_user.list",
        "alero_vendor.get",
        "alero_vendor.list",
        "alert.heartbeat.sent",
        "alert.rule.escalated",
        "alert.rule.notify_failed",
        "alert.rule.opened",
        "alert.rule.renotified",
        "alert.rule.resolved",
        "approval.cancel",
        "approval.reject",
        "audit.chain_incident_documented",
        "audit.export",
        "failback.import.end",
        "failback.import.start",
        "secrets.entry.set",
        "secrets.tenant_secret.update",
        "tenant.seed",
        "auth.login",
        "auth.login.lockout_degraded",
        "auth.logout",
        "auth.refresh",
        "auth.refresh.reuse",
        "campaign.create",
        "campaign.delete",
        "campaign.get",
        "campaign.list",
        "compliance.account_status.view",
        "compliance.accounts.view",
        "compliance.search",
        "compliance.exemption.create",
        "compliance.exemption.delete",
        "component.list",
        "dashboard.view",
        "discovered_account.activities",
        "discovered_account.delete",
        "discovered_account.ignore",
        "discovered_account.list",
        "discovered_account.onboard",
        "discovered_account.restore",
        "group.create",
        "group.delete",
        "group.export",
        "group.list",
        "group.member.add",
        "group.member.remove",
        "group.members",
        "group.search",
        "group.update",
        "identity_principal.search",
        "identity_role.create",
        "identity_role.list",
        "identity_role.member.add",
        "identity_role.members",
        "identity_user.get",
        "identity_user.list",
        "identity_user.lock",
        "itr.infra_file.delete",
        "itr.infra_file.upload",
        "kb.create",
        "kb.delete",
        "kb.list",
        "kb.update",
        "member.search",
        "operator.assignments",
        "operator.create",
        "operator.password_change",
        "operator.reset_password",
        "operator.reset_totp",
        "operator.update",
        "platform.activate",
        "platform.backup.download",
        "platform.config.view",
        "platform.delete",
        "platform.duplicate",
        "platform.export",
        "platform.import",
        "platform.list",
        "platform.psm.get",
        "platform.psm.rollback_failed",
        "platform.psm.update",
        "platform.psm.update.bulk",
        "platform.psm.update.bulk.preview",
        "platform.search",
        "psm.servers.list",
        "recording.activities",
        "recording.list",
        "recording.play",
        "recording.play.mp4",
        "report.itr.generate",
        "safe.create",
        "safe.defaults",
        "safe.delete",
        "safe.export",
        "safe.list",
        "safe.member.add",
        "safe.member.remove",
        "safe.member.update",
        "safe.members",
        "safe.search",
        "safe.update",
        "session.list",
        "session.terminate",
        "setup.connector_pools.list",
        "setup.connectors.list",
        "setup.sia_connectors.list",
        "sia.policies.list",
        "sia.policy.get",
        "sia.secrets.list",
        "sia.session.get",
        "sia.sessions.list",
        "sia.settings.get",
        "sia.strong_accounts.list",
        "sia.targets.list",
        "system_health.view",
        "tenant.compliance_settings.update",
        "tenant.logo.delete",
        "tenant.logo.update",
        "tenant.server_info",
        "troubleshooting.account_errors.view",
        "user.activate",
        "user.create",
        "user.delete",
        "user.export",
        "user.get",
        "user.list",
        "user.search",
        "user.update",
        "vendor_status.view",
        # `report.{name}` dinámico (app/api/reports.py) — un item por reporte
        # del catálogo, no un literal en el código:
        "report.account-activity",
        "report.account-inventory",
        "report.account-inventory-full",
        "report.license-candidates",
        "report.non-rotating-accounts",
        "report.rotation-failures-by-system",
        "report.psm-balance",
        "report.safe-duplicates",
        "report.safe-permissions",
        "report.target-inactivity",
        "report.user-access",
    }
)
