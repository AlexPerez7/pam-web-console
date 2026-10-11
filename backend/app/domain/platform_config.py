"""Configuración completa de una plataforma, leída del paquete de "Export
Platform" de CyberArk (`.zip` con `Policy-<id>.ini` + `Policy-<id>.xml`).

El PVWA la muestra en `editor.aspx` (árbol "Target Account Platform"),
pero esa página se autentica con la sesión del navegador del operador,
no con un Bearer token — PAM Web Console no puede llamarla. El paquete de
exportación trae los mismos datos:

- el `.xml` es "UI & Workflows" (Properties, Linked Accounts, Usages,
  Ticketing System, Privileged Session Management, Connection
  Components…);
- el `.ini` es "Automatic Password Management" (política del CPM).

Todo acá es puro (bytes -> dict), sin I/O. El parseo del XML es
**genérico** a propósito: no se conoce un esquema oficial del paquete y
CyberArk suma nodos según la versión, así que cada elemento se vuelve un
nodo del árbol (atributos = filas, hijos = sub-nodos) y sólo se traducen
los nombres conocidos. Ver `docs/specs/platform-config-viewer.md`.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import Any

# Tope por archivo descomprimido: un paquete real pesa unos pocos KB; esto
# sólo evita que un zip malformado (o una "zip bomb") llene la memoria.
MAX_MEMBER_BYTES = 5 * 1024 * 1024

Node = dict[str, Any]

# --- nombres legibles ---------------------------------------------------

XML_LABELS: dict[str, str] = {
    "Policy": "General",
    "Properties": "Propiedades",
    "Required": "Obligatorias",
    "Optional": "Opcionales",
    "LinkedPasswords": "Cuentas vinculadas",
    "Usages": "Usos",
    "TicketingSystem": "Sistema de tickets",
    "PrivilegedSessionManagement": "Gestión de sesiones (PSM)",
    "ConnectionComponents": "Connection Components",
    "ConnectionComponent": "Connection Component",
    "Override": "Override",
    "Overrides": "Overrides",
    "Parameters": "Parámetros",
    "UserParameters": "Parámetros de usuario",
    "PSMServerID": "Servidor PSM",
    "Device": "Dispositivo",
}

# Grupos del .ini, en el mismo orden que el árbol del PVWA.
INI_GROUPS: list[tuple[str, str, str]] = [
    ("general", "General", "General"),
    ("plugin", "Plug-in del CPM", "CPM Plug-in"),
    ("change", "Cambio de contraseña", "Password Change"),
    ("verification", "Verificación", "Password Verification"),
    ("reconciliation", "Reconciliación", "Password Reconciliation"),
    ("notifications", "Notificaciones", "Notifications"),
    ("generate", "Generación de contraseñas", "Generate Password"),
]

_PLUGIN_KEYS = {"DllName", "XMLFile", "ExeName", "PluginVersion"}
_CHANGE_KEYS = {
    "AllowManualChange",
    "PerformPeriodicChange",
    "HeadStartInterval",
    "FromHour",
    "ToHour",
    "ChangeNotificationPeriod",
    "DaysNotifyPriorExpiration",
    "ExecutionDays",
    "ChangePasswordInResetMode",
}
_GENERATE_KEYS = {
    "PasswordLength",
    "MinUpperCase",
    "MinLowerCase",
    "MinDigit",
    "MinSpecial",
    "PasswordForbiddenChars",
    "PasswordEffectiveLength",
}

# Descripciones en español de los parámetros más consultados. El resto
# muestra el comentario que trae el propio .ini (si lo trae).
INI_HINTS: dict[str, str] = {
    "PolicyID": "Identificador de la plataforma.",
    "PolicyName": "Nombre visible de la plataforma.",
    "AllowedSafes": "Expresión regular de las safes cuyas cuentas puede usar esta plataforma.",
    "MinValidityPeriod": "Minutos mínimos entre un retiro de la contraseña y el próximo cambio.",
    "Timeout": "Segundos que espera el CPM una respuesta del target.",
    "UnlockIfFail": "Desbloquea la cuenta en la Vault si el cambio falla.",
    "MaximumRetries": "Reintentos del CPM antes de rendirse (la cuenta queda deshabilitada por el CPM).",
    "MinDelayBetweenRetries": "Minutos entre reintentos del CPM.",
    "ImmediateInterval": "Minutos entre revisiones de tareas inmediatas del CPM.",
    "Interval": "Minutos entre revisiones de tareas periódicas del CPM.",
    "AllowManualChange": "Permite que un usuario dispare el cambio a mano.",
    "PerformPeriodicChange": "El CPM cambia la contraseña automáticamente según la política.",
    "HeadStartInterval": "Días antes del vencimiento en que el CPM ya puede cambiarla.",
    "FromHour": "Hora desde la que el CPM puede ejecutar cambios (-1 = sin restricción).",
    "ToHour": "Hora hasta la que el CPM puede ejecutar cambios (-1 = sin restricción).",
    "ExecutionDays": "Días de la semana en que el CPM puede ejecutar cambios.",
    "VFAllowManualVerification": "Permite que un usuario dispare la verificación a mano.",
    "VFPerformPeriodicVerification": "El CPM verifica la contraseña periódicamente.",
    "RCAllowManualReconciliation": "Permite que un usuario dispare la reconciliación a mano.",
    "RCAutomaticReconcileWhenUnsynched": "Reconcilia automáticamente cuando detecta la contraseña desincronizada.",
    "RCReconcileReasons": "Códigos de error que disparan la reconciliación automática.",
    "PasswordLength": "Largo de las contraseñas que genera el CPM.",
    "MinUpperCase": "Mínimo de mayúsculas.",
    "MinLowerCase": "Mínimo de minúsculas.",
    "MinDigit": "Mínimo de dígitos.",
    "MinSpecial": "Mínimo de caracteres especiales (-1 = ninguno).",
    "PasswordForbiddenChars": "Caracteres que el CPM nunca usa al generar una contraseña.",
    "DllName": "Plug-in (DLL) que usa el CPM para esta plataforma.",
    "XMLFile": "Indica si el plug-in usa un archivo de proceso XML (TPC).",
}


def _ini_group(key: str) -> str:
    if key in _PLUGIN_KEYS:
        return "plugin"
    if key in _CHANGE_KEYS:
        return "change"
    if key in _GENERATE_KEYS:
        return "generate"
    if key.startswith("VF"):
        return "verification"
    if key.startswith("RC"):
        return "reconciliation"
    if key.startswith("NF"):
        return "notifications"
    return "general"


# --- decodificación -----------------------------------------------------


def _decode(data: bytes) -> str:
    """Los .ini que exporta CyberArk pueden venir en UTF-16 con BOM o en
    UTF-8 (con o sin BOM). Sin BOM se prueba UTF-8 y, si no es válido,
    latin-1 (nunca falla)."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


# --- .ini ---------------------------------------------------------------

_SECTION_RE = re.compile(r"^\[(?P<name>[^\]]+)\]\s*$")
# el comentario inline de CyberArk va después de un espacio: "Key=Value ;texto".
# Un ";" pegado al valor (ej. PasswordForbiddenChars=;:) es parte del valor.
_INLINE_COMMENT_RE = re.compile(r"\s+;(?P<comment>.*)$")


def parse_ini(text: str) -> list[Node]:
    """`.ini` de la política del CPM -> nodos del árbol, agrupados como el
    PVWA. Las secciones explícitas (`[ExtraInfo]`, etc.) cuelgan del
    "Plug-in del CPM" — son parámetros propios del plug-in."""
    groups: dict[str, list[dict]] = {g[0]: [] for g in INI_GROUPS}
    sections: list[tuple[str, list[dict]]] = []
    current: list[dict] | None = None  # None = claves sueltas, antes de toda sección

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith((";", "#")):
            continue
        m = _SECTION_RE.match(line)
        if m:
            current = []
            sections.append((m.group("name").strip(), current))
            continue
        if "=" not in line:
            continue
        key, _, rest = line.partition("=")
        key = key.strip()
        if not key:
            continue
        comment = ""
        cm = _INLINE_COMMENT_RE.search(rest)
        if cm:
            comment = cm.group("comment").strip()
            rest = rest[: cm.start()]
        row = {
            "name": key,
            "value": rest.strip(),
            "hint": INI_HINTS.get(key) or comment or None,
        }
        if current is None:
            groups[_ini_group(key)].append(row)
        else:
            current.append(row)

    nodes: list[Node] = []
    for gid, label, source in INI_GROUPS:
        node: Node = {
            "id": f"ini/{gid}",
            "label": label,
            "source_name": source,
            "rows": groups[gid],
            "children": [],
        }
        if gid == "plugin":
            node["children"] = [
                {
                    "id": f"ini/section/{i}",
                    "label": name,
                    "source_name": name,
                    "rows": rows,
                    "children": [],
                }
                for i, (name, rows) in enumerate(sections)
            ]
        if node["rows"] or node["children"]:
            nodes.append(node)
    return nodes


# --- .xml ---------------------------------------------------------------

_NAME_ATTRS = ("Name", "Id", "ID", "Value")


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _leaf_row(el: ET.Element) -> dict:
    """Un elemento sin hijos dentro de una lista (ej. `<Property
    Name="Address"/>`) -> una fila: nombre = su atributo identificador,
    valor = el resto de atributos."""
    attrs = dict(el.attrib)
    name = None
    for a in _NAME_ATTRS:
        if a in attrs:
            name = attrs.pop(a)
            break
    text = (el.text or "").strip()
    value = " · ".join(f"{k}: {v}" for k, v in attrs.items())
    if text:
        value = f"{value} · {text}" if value else text
    return {"name": name or _local(el.tag), "value": value, "hint": None}


def _xml_node(el: ET.Element, path: str) -> Node:
    tag = _local(el.tag)
    rows: list[dict] = [{"name": k, "value": v, "hint": None} for k, v in el.attrib.items()]
    text = (el.text or "").strip()
    if text:
        rows.append({"name": "Valor", "value": text, "hint": None})
    children: list[Node] = []
    # un contenedor-lista tiene todos sus hijos del mismo tipo (Required ->
    # Property, LinkedPasswords -> Link…): sus hijos hoja son filas. En un
    # elemento con hijos distintos (Policy), cada hijo es su propia sección.
    is_list = len({_local(c.tag) for c in el}) == 1
    for i, child in enumerate(el):
        if is_list and len(child) == 0 and any(a in child.attrib for a in _NAME_ATTRS):
            # elemento-lista (Property, Link, ConnectionComponent sin overrides…)
            rows.append(_leaf_row(child))
        else:
            children.append(_xml_node(child, f"{path}/{i}"))
    # etiqueta: si el elemento tiene nombre propio (un ConnectionComponent
    # con overrides, por ejemplo), se muestra ése; si no, la traducción.
    own = next((el.attrib[a] for a in ("Name", "Id") if a in el.attrib), None)
    label = XML_LABELS.get(tag, tag)
    if own and tag not in ("Policy", "Device"):
        label = f"{label}: {own}"
    return {"id": path, "label": label, "source_name": tag, "rows": rows, "children": children}


def _parse_xml(data: bytes) -> ET.Element:
    # El XML viene de CyberArk por un canal autenticado (no de un usuario),
    # y el `expat` de Python 3.12 no resuelve entidades externas y limita
    # la expansión de entidades — no hace falta `defusedxml` para esto.
    try:
        return ET.fromstring(data)  # noqa: S314
    except ET.ParseError:
        return ET.fromstring(_decode(data))  # noqa: S314


def parse_xml(data: bytes) -> list[Node]:
    """`.xml` de "UI & Workflows" -> nodos del árbol. Se saltean los
    envoltorios con un único hijo (`<Device><Policies><Policy>`) para que
    el primer nivel sean las secciones reales (Properties, Linked
    Accounts, …), igual que el árbol del PVWA."""
    root = _parse_xml(data)
    el = root
    while _local(el.tag) in ("Device", "Policies") and len(el) == 1:
        el = el[0]
    node = _xml_node(el, "xml")
    general: Node = {
        "id": "xml/general",
        "label": "General",
        "source_name": _local(el.tag),
        "rows": node["rows"],
        "children": [],
    }
    return ([general] if general["rows"] else []) + node["children"]


# --- paquete ------------------------------------------------------------


def parse_platform_package(package: bytes) -> dict:
    """`.zip` de "Export Platform" -> `{"files": [...], "tree": [...]}`.

    `tree` tiene dos raíces, en el mismo orden que el PVWA: "Interfaz y
    flujos" (el .xml) y "Gestión automática de contraseñas" (el .ini). Un
    archivo que no se puede leer no aborta el resto: queda en `errors`.
    Levanta `ValueError` si el paquete no es un zip."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(package))
    except zipfile.BadZipFile as e:
        raise ValueError("el paquete de exportación no es un .zip válido") from e

    files: list[str] = []
    errors: list[str] = []
    xml_nodes: list[Node] = []
    ini_nodes: list[Node] = []
    with zf:
        for info in zf.infolist():
            name = info.filename
            lower = name.lower()
            if info.is_dir() or not lower.endswith((".ini", ".xml")):
                continue
            files.append(name)
            if info.file_size > MAX_MEMBER_BYTES:
                errors.append(f"{name}: demasiado grande para mostrarlo")
                continue
            data = zf.read(info)
            try:
                if lower.endswith(".xml"):
                    xml_nodes.extend(parse_xml(data))
                else:
                    ini_nodes.extend(parse_ini(_decode(data)))
            except (ET.ParseError, ValueError) as e:
                errors.append(f"{name}: no se pudo leer ({e})")

    tree: list[Node] = []
    if xml_nodes:
        tree.append(
            {
                "id": "xml",
                "label": "Interfaz y flujos",
                "source_name": "UI & Workflows",
                "rows": [],
                "children": xml_nodes,
            }
        )
    if ini_nodes:
        tree.append(
            {
                "id": "ini",
                "label": "Gestión automática de contraseñas",
                "source_name": "Automatic Password Management",
                "rows": [],
                "children": ini_nodes,
            }
        )
    return {"files": files, "tree": tree, "errors": errors}
