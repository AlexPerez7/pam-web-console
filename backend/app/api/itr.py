"""Reporte ITR (Telemetry Reporter) de un tenant — el mismo
documento HTML autocontenido que hoy sólo genera `Telemetry-Reporter`
por CLI, ahora disparable desde la página Reportes por cualquier
`tenant_admin+`, sin depender de que alguien lo corra a mano. Ver
`docs/specs/done/itr-report.md`.

Reusa `app.itr.analysis` + `app.itr.report_builder` (portados de
Telemetry-Reporter CASI TAL CUAL — ver "corrección de diseño" en la
spec: `report_builder.py`/`templates/*.j2` esperan exactamente las formas
que produce el `analysis.py` ORIGINAL de ITR, no las versiones ya
evolucionadas de `app.analysis`, que divergieron para las necesidades
propias de Dashboard/Compliance de PAM Web Console). La única excepción es el % de
Compliance: se reusa `app.analysis.compliance_breakdown` (versión de
PAM Web Console — exacta por cuenta y consciente de la política de CADA plataforma,
mejor que la aproximación de ITR) porque la forma de su resultado sí
coincide con lo que espera el template.

Sesiones (2026-09-18, opt-in): necesita `GET /Accounts/{id}/Activities`
POR CADA CUENTA (~1900 en Globex) — se hace igual, síncrono, sólo si el
operador tilda "incluir Sesiones" (`ItrReportIn.include_sessions`), mismo
criterio que `credentials_backup.py` (una llamada por cuenta, ninguna
aborta el resto). Si no se pide, se llama a `analyze_account_activity`/
`analyze_concurrent_sessions` con datos vacíos sólo para satisfacer los
parámetros no-opcionales de `build_html_report`.

Infraestructura (2026-09-18): los `HealthCheck_<host>_<timestamp>.json`
que deja el `.ps1` corrido a mano en cada servidor del cliente se suben
por tenant (`ItrInfraFile`, `POST/GET/DELETE .../itr/infra-files` en este
mismo router) y se vuelcan a un directorio temporal antes de llamar a
`app.itr.infra_data.load_infra_data` (portado de ITR verbatim — sólo sabe
leer una CARPETA). Sin archivos subidos, la pestaña igual se arma con el
mensaje "sin datos" que ya trae ITR — no hace falta excluirla de la nav.

Logo del cliente (2026-09-18, a pedido de Alex — ITR sí lo tenía):
`Tenant.client_logo` (bytes en la DB, subido vía `PUT /tenants/{id}/logo`
en `app/api/tenants.py`) se vuelca a un archivo temporal antes de llamar a
`build_html_report`, porque esa función sólo sabe leer un logo desde un
PATH de archivo — no se tocó esa parte verbatim de ITR para que acepte
bytes directo.

Pestañas a incluir + caché (2026-09-18, Alex: "cuando lo generaba desde
consola tenía la opción de escoger si quería ocultar alguna pestaña... y
lo del caché para generarlo más rápido cuando testeaba cambios en el
reporte" — mismos dos extras de `generate_report.py`/`cache_utils.py` de
ITR):
- `ItrReportIn.excluded_tabs` — ids de `report_builder.TAB_ORDER` a sacar
  de la nav (además de "sessions", que ya se excluye solo si
  `include_sessions` es `false`). Los datos de una pestaña excluida se
  siguen calculando igual (mismo criterio que ITR) — sólo cambia la nav.
- `ItrReportIn.use_cache` — cachea en Redis (`app.core.cache`, TTL
  `PAM_ITR_CACHE_TTL`) los datos CRUDOS (antes de `analyze_*`): accounts/
  platforms/safes/users/licencias/connectors/sia/identity/los 4
  savedFilters + component_details + safe_members + activities (si
  `include_sessions`). Clave separada por `include_sessions` (activarlo
  con caché de una corrida SIN Sesiones no debe mostrar Sesiones vacías
  por error). Con un hit, ni siquiera se le pega a CyberArk por esos
  datos — sólo se autentica el cliente (siempre corre por `tenant_client`,
  no vale la pena duplicar su validación para ahorrarse una sola
  request). Apagado por default — a diferencia del resto de `app.core.
  cache`, esta caché SÍ guarda metadata cruda de cuentas (nunca
  secretos), pensada para iterar rápido sobre cambios al template, no
  para el uso normal del botón.

Fix de header por cliente (2026-09-29, alcance chico a propósito — Alex):
Acme/Hooli traen un logo con fondo sólido propio que se veía con un
borde blanco feo bajo el estilo base (`.client-logo` con placa blanca). Se
portó el override de header de ITR para esos dos (`app/itr/clientes/
<tenant_id>/templates/partials/_header.html.j2`), vía el mecanismo
`ChoiceLoader` que `report_builder.py` ya traía sin usar —
`report_builder.client_templates_dir_for(tenant_id)` resuelve la carpeta
(o `None` si el tenant no tiene una), mismo `id` que usa PAM Web Console para el
tenant, sin mapeo aparte. Hooli es `self_hosted` (fuera del alcance v1
de PAM Web Console) — su override queda copiado por si algún día se habilita ese
tenant.

**A propósito NO se portó el layout completo de Initech** ("dashboard
jerarquizado", `report.html.j2` + 8 pestañas reorganizadas) que sí existe
en el repo de ITR: el reporte de un tenant generado desde PAM Web Console tiene que
salir tan completo como el de cualquier otro (mismas 10 pestañas, layout
genérico) — si se quiere sacar una pestaña puntual al generarlo, ya existe
`excluded_tabs` para eso, no un template distinto por cliente que
reorganiza dónde vive cada dato.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import analysis
from app.config import get_settings
from app.core import audit, cache, compliance_exemptions
from app.core.deps import require_tenant_access
from app.core.tenants import tenant_client
from app.cyberark import CyberArkClient, CyberArkError, Forbidden, NotFound
from app.db import get_session
from app.itr import analysis as itr_analysis
from app.itr import infra_data as itr_infra_data
from app.itr import report_builder
from app.models import Deployment, ItrInfraFile, Operator, OperatorRole

router = APIRouter(prefix="/tenants/{tenant_id}", tags=["itr"])

_tenant_admin = require_tenant_access(OperatorRole.tenant_admin)

_COMPLIANCE_FILTERS = [
    "PolicyFailures",
    "DisabledPasswordByUser",
    "DisabledPasswordByCPM",
    "Locked",
]
_COMPONENT_IDS = ["PVWA", "SessionManagement", "CPM", "AIM"]
# PVWA/AIM en Privilege Cloud/ISPSS: infraestructura que administra
# CyberArk, no el equipo PAM — se sacan del reporte de un tenant cloud (ver
# comentario donde se usa esta constante).
_CLOUD_MANAGED_COMPONENT_IDS = {"PVWA", "AIM"}

# `Tenant.client_logo_content_type` -> extensión de archivo — mismo set que
# acepta `PUT /tenants/{id}/logo` (app/api/tenants.py). `report_builder.
# _image_to_data_uri` adivina el mimetype por extensión del PATH que se le
# pasa (no lee bytes ni recibe un content-type), así que la extensión del
# archivo temporal tiene que ser la real.
_LOGO_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg"}

# Mismo patrón que `infra_data.load_infra_data` espera encontrar con su
# `glob.glob("HealthCheck_*.json")` — se valida al subir para no aceptar un
# archivo que después el parser va a ignorar en silencio. Sin separadores de
# ruta: el nombre se usa tal cual para escribir en un directorio temporal
# (`HealthCheck_/../../x.json` escribiría fuera de él).
_INFRA_FILENAME_RE = re.compile(r"^HealthCheck_[^/\\\x00]*\.json$")
_MAX_INFRA_FILE_BYTES = 1 * 1024 * 1024  # 1 MB — de sobra, es un JSON chico

# ids válidos de `ItrReportIn.excluded_tabs` — misma fuente única que usa
# `report_builder` para armar la nav.
_KNOWN_TAB_IDS = {tab_id for tab_id, _ in report_builder.TAB_ORDER}


class ItrReportIn(BaseModel):
    reason: str | None = None
    include_sessions: bool = False
    excluded_tabs: list[str] = Field(default_factory=list)
    use_cache: bool = False


# Nombre legible de cada fuente no crítica, para el banner de "datos
# incompletos" del reporte (ver `_data_warnings`).
_SOURCE_LABELS = {
    "safes": "Safes",
    "users": "Usuarios del Vault",
    "licenses": "Licencias",
    "connectors": "Connector Management",
    "sia_connectors": "SIA Connectors",
    "identity_users": "Usuarios de Identity",
    **{f"filter:{f}": f"Filtro de cuentas {f}" for f in _COMPLIANCE_FILTERS},
    **{f"component:{c}": f"Componente {c}" for c in _COMPONENT_IDS},
}


def _ok(v):
    """None si el resultado del gather fue una excepción (degradación parcial) —
    mismo helper que `app.api.dashboards`."""
    return None if isinstance(v, BaseException) else v


async def _empty_list() -> list:
    return []


def _data_warnings(raw: dict, cache_hit: bool) -> list[str]:
    """Avisos que se muestran arriba del reporte: sin esto, una fuente caída
    se ve como un cero real ("0 Safes") y un `PolicyFailures` caído infla el
    % de Compliance sin que nadie lo note."""
    errors: dict[str, str] = raw.get("source_errors") or {}
    warnings: list[str] = []
    if "filter:PolicyFailures" in errors:
        warnings.append(
            "El % de Compliance es parcial: no se pudo consultar el filtro "
            "PolicyFailures, por lo que las cuentas fuera de política no se "
            "descuentan del porcentaje."
        )
    # SIA con 403 suele ser falta de licencia o de rol: la pestaña ya lo
    # explica en su propia sección, no hace falta repetirlo arriba.
    shown = {
        k: v
        for k, v in errors.items()
        if k != "filter:PolicyFailures"
        and not (k == "sia_connectors" and v.startswith("Forbidden"))
    }
    if shown:
        names = ", ".join(_SOURCE_LABELS.get(k, k) for k in sorted(shown))
        warnings.append(
            f"No se pudo consultar: {names}. Los valores de esas secciones "
            "aparecen en cero o vacíos y no reflejan el estado real."
        )
    if raw.get("safe_members_failed"):
        warnings.append(
            f"No se pudieron consultar los miembros de {raw['safe_members_failed']} "
            "Safe(s); los conteos de miembros y usuarios están incompletos."
        )
    activities = raw.get("activities_by_id") or {}
    failed_activities = sum(1 for v in activities.values() if v is None)
    if failed_activities:
        warnings.append(
            f"No se pudo consultar la actividad de {failed_activities} cuenta(s); "
            "la pestaña Sesiones está incompleta."
        )
    if cache_hit:
        fetched = raw.get("fetched_at") or "fecha desconocida"
        warnings.append(f"Datos tomados de la caché (obtenidos el {fetched} UTC), no en vivo.")
    return warnings


def _with_account_counts(safes: list[dict], by_safe: Counter) -> list[dict]:
    """`client.list_safes()` trae `includeAccounts=false` (Dashboard no lo
    necesita) — `itr_analysis.analyze_safes` sí cuenta cuentas por Safe,
    pero reusando el `by_safe` que YA calculó `itr_analysis.
    analyze_accounts` sobre la misma lista de Accounts, en vez de un
    segundo fetch paginado (25/página) con `includeAccounts=true`."""
    return [{**s, "accounts": [None] * by_safe.get(s.get("safeName"), 0)} for s in safes]


async def _all_account_activities(
    client: CyberArkClient, accounts: list[dict]
) -> dict[str, list | None]:
    """`client.account_activities(id)` para TODAS las cuentas, EN PARALELO
    (mismo semáforo de concurrencia que cualquier otra llamada del
    cliente) — mismo criterio que `get_all_account_activities`/
    `get_all_safe_members` de ITR: una cuenta puntual que falla queda en
    `None` (no se pudo consultar), no aborta el resto. Es la parte más
    lenta del reporte con Sesiones activado (1 request por cuenta — en
    Globex, ~1900)."""

    async def _one(acc_id: str) -> tuple[str, list | None]:
        try:
            return acc_id, await client.account_activities(acc_id)
        except CyberArkError:
            return acc_id, None

    ids = [a["id"] for a in accounts if a.get("id")]
    pairs = await asyncio.gather(*(_one(acc_id) for acc_id in ids))
    return dict(pairs)


@router.post("/reports/itr/export")
async def itr_report_export(
    tenant_id: str,
    body: ItrReportIn,
    request: Request,
    op: Operator = Depends(_tenant_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Genera el reporte ITR completo del tenant y lo devuelve como
    descarga (`Content-Disposition: attachment`). Puede tardar 1-2 minutos
    en un tenant grande (todas las membresías de todas las Safes, además
    de Accounts/Platforms/Users/Components) — el `proxy_read_timeout` de
    nginx (600s, ya extendido para `credentials-backup/export`) alcanza.
    Con `include_sessions=true` (opt-in, ver docstring del módulo) puede
    tardar bastante más — 1 request extra POR CUENTA.

    RBAC `tenant_admin+` (mismo techo que `credentials_backup`: no revela
    secretos, pero agrega TODO el estado del tenant en un documento
    pensado para compartir con el cliente). `reason` es OPCIONAL (a
    diferencia de `account.reveal`/`credentials_backup`) — sólo queda en
    el audit, no bloquea la generación: esto es lectura, no toca
    CyberArk. Sin feature-flag por el mismo motivo.

    Sólo Accounts/Platforms son imprescindibles (mismo criterio que
    `REQUIRED_TASKS` de ITR) — si esas dos fallan, 502 sin generar nada.
    Cualquier otra fuente (Safes, Components, Licencias, Connector
    Management, SIA Connectors, Identity) degrada a "sin datos" sin
    abortar el resto."""
    reason = (body.reason or "").strip()

    unknown_tabs = set(body.excluded_tabs) - _KNOWN_TAB_IDS
    if unknown_tabs:
        raise HTTPException(
            status_code=422, detail=f"pestañas desconocidas: {', '.join(sorted(unknown_tabs))}"
        )
    excluded_tabs = set(body.excluded_tabs)
    if not body.include_sessions:
        excluded_tabs.add("sessions")

    # Caché opt-in de los datos CRUDOS (ver docstring del módulo) — clave
    # separada por include_sessions para no mostrar Sesiones vacías desde
    # un hit que se guardó sin pedirlas.
    cache_key = f"pam:itr-raw:{tenant_id}:sessions={int(body.include_sessions)}"
    raw: dict | None = await cache.get_json(cache_key) if body.use_cache else None
    cache_hit = raw is not None

    async with tenant_client(tenant_id, session) as (tenant, client):
        if raw is None:
            try:
                accounts, platforms = await asyncio.gather(
                    client.list_accounts(), client.list_platforms()
                )
            except (NotFound, Forbidden) as e:
                await _audit(
                    session,
                    op,
                    request,
                    tenant_id,
                    "error",
                    {"error": str(e), "phase": "accounts_platforms"},
                    reason,
                )
                raise HTTPException(
                    status_code=403 if isinstance(e, Forbidden) else 404, detail=str(e)
                )
            except CyberArkError as e:
                await _audit(
                    session,
                    op,
                    request,
                    tenant_id,
                    "error",
                    {"error": str(e), "phase": "accounts_platforms"},
                    reason,
                )
                raise HTTPException(status_code=502, detail=str(e))

            # Connector Management / SIA sin URL configurada no es una falla
            # (el tenant no los usa): se piden sólo si están configurados,
            # para no ensuciar el banner de "datos incompletos".
            results = await asyncio.gather(
                client.list_safes(),
                client.list_users(),
                client.get_license_report(),
                client.list_connectors() if client.connector_management_url else _empty_list(),
                client.list_sia_connectors() if client.sia_connector_url else _empty_list(),
                client.get_identity_users_full(),
                *(client.saved_filter_accounts(f) for f in _COMPLIANCE_FILTERS),
                *(client.component_details(c) for c in _COMPONENT_IDS),
                return_exceptions=True,
            )
            source_names = [
                "safes",
                "users",
                "licenses",
                "connectors",
                "sia_connectors",
                "identity_users",
                *(f"filter:{f}" for f in _COMPLIANCE_FILTERS),
                *(f"component:{c}" for c in _COMPONENT_IDS),
            ]
            source_errors = {
                name: f"{type(r).__name__}: {r}"
                for name, r in zip(source_names, results, strict=True)
                if isinstance(r, BaseException)
            }
            safes = _ok(results[0]) or []
            users_raw = _ok(results[1]) or []
            license_report = _ok(results[2]) or {}
            connectors_raw = _ok(results[3]) or []
            sia_connectors_raw = _ok(results[4]) or []
            identity_users_raw = _ok(results[5]) or []
            n_filters = len(_COMPLIANCE_FILTERS)
            filter_lists = [_ok(r) for r in results[6 : 6 + n_filters]]
            component_lists = [_ok(r) for r in results[6 + n_filters :]]

            # Miembros de cada Safe visible, EN PARALELO — la parte más
            # lenta en tenants con muchas Safes (1 request por Safe). Una
            # Safe puntual sin "View Safe Members" se salta, no aborta el
            # resto (mismo criterio que `get_all_safe_members` de ITR).
            safe_ids = [s["safeName"] for s in safes if s.get("safeName")]
            member_lists = await asyncio.gather(
                *(client.safe_members(sid) for sid in safe_ids), return_exceptions=True
            )
            safe_members_raw = [m for lst in member_lists if isinstance(lst, list) for m in lst]
            safe_members_failed = sum(1 for lst in member_lists if isinstance(lst, BaseException))

            sia_ids = [c.get("id") for c in sia_connectors_raw if c.get("id")]
            sia_details = await client.get_all_sia_connector_details(sia_ids) if sia_ids else {}

            # Sesiones es opt-in (ver docstring del módulo) — 1 request POR
            # CUENTA, la parte más lenta del reporte por lejos si se pide.
            activities_by_id = (
                await _all_account_activities(client, accounts) if body.include_sessions else {}
            )

            raw = {
                "accounts": accounts,
                "platforms": platforms,
                "safes": safes,
                "users_raw": users_raw,
                "license_report": license_report,
                "connectors_raw": connectors_raw,
                "sia_connectors_raw": sia_connectors_raw,
                "identity_users_raw": identity_users_raw,
                "filter_lists": filter_lists,
                "component_lists": component_lists,
                "safe_members_raw": safe_members_raw,
                "sia_details": sia_details,
                "activities_by_id": activities_by_id,
                "source_errors": source_errors,
                "safe_members_failed": safe_members_failed,
                "fetched_at": datetime.now(UTC).strftime("%d-%m-%Y %H:%M"),
            }
            if body.use_cache:
                await cache.set_json(cache_key, raw, get_settings().itr_cache_ttl)

        stale_days = tenant.compliance_stale_days
        client_name = tenant.client
        logo_bytes = tenant.client_logo
        logo_content_type = tenant.client_logo_content_type
        is_privilege_cloud = tenant.deployment == Deployment.privilege_cloud
        # El encabezado ya muestra el cliente; "Entorno" describe la plataforma
        # (antes era `tenant.name`, que repetía el nombre del cliente).
        environment_name = "Privilege Cloud ISPSS" if is_privilege_cloud else "PAM Self-Hosted"

    try:
        html, stats = await _build_report(
            session,
            tenant_id,
            raw,
            stale_days=stale_days,
            client_name=client_name,
            environment_name=environment_name,
            logo_bytes=logo_bytes,
            logo_content_type=logo_content_type,
            is_privilege_cloud=is_privilege_cloud,
            excluded_tabs=excluded_tabs,
            data_warnings=_data_warnings(raw, cache_hit),
        )
    except Exception as e:
        # Cualquier falla al analizar/renderizar (un dato inesperado de
        # CyberArk, un archivo de infra raro, la plantilla) también queda
        # auditada — sin esto salía un 500 sin rastro en `audit_log`.
        await session.rollback()
        await _audit(
            session,
            op,
            request,
            tenant_id,
            "error",
            {"error": f"{type(e).__name__}: {e}", "phase": "render"},
            reason,
        )
        raise HTTPException(
            status_code=500, detail="no se pudo generar el reporte ITR (ver el audit log)"
        ) from e

    await _audit(
        session,
        op,
        request,
        tenant_id,
        "ok",
        {
            **stats,
            "source_errors": raw.get("source_errors") or {},
            "include_sessions": body.include_sessions,
            "excluded_tabs": sorted(excluded_tabs),
            "use_cache": body.use_cache,
            "cache_hit": cache_hit,
        },
        reason,
    )

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    filename = f"itr_report_{tenant_id}_{stamp}.html"
    return Response(
        content=html,
        media_type="text/html",
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


async def _build_report(
    session: AsyncSession,
    tenant_id: str,
    raw: dict,
    *,
    stale_days: int,
    client_name: str,
    environment_name: str,
    logo_bytes: bytes | None,
    logo_content_type: str | None,
    is_privilege_cloud: bool,
    excluded_tabs: set[str],
    data_warnings: list[str],
) -> tuple[str, dict]:
    """Análisis + render del reporte a partir de los datos crudos (recién
    pedidos o de caché). Devuelve el HTML y los conteos para el audit."""
    accounts = raw["accounts"]
    platforms = raw["platforms"]
    safes = raw["safes"]
    users_raw = raw["users_raw"]
    license_report = raw["license_report"]
    connectors_raw = raw["connectors_raw"]
    sia_connectors_raw = raw["sia_connectors_raw"]
    identity_users_raw = raw["identity_users_raw"]
    filter_lists = raw["filter_lists"]
    component_lists = raw["component_lists"]
    safe_members_raw = raw["safe_members_raw"]
    sia_details = raw["sia_details"]
    activities_by_id = raw["activities_by_id"]
    data_warnings = list(data_warnings)
    # La pestaña de SIA ya sabe mostrar el motivo real de la falla (403 por
    # licencia/rol, típicamente) en vez de un "no configurado" genérico.
    sia_connectors_error = (raw.get("source_errors") or {}).get("sia_connectors")

    # ---- Compliance: versión propia de PAM Web Console (ver docstring del módulo) ----
    # `filter_lists`/`component_lists` (del `raw` recién armado o de caché)
    # ya vienen resueltos (`_ok()` aplicado al armar `raw` — lista o `None`,
    # nunca una excepción), así que no hace falta volver a envolverlos acá.
    platform_days = analysis.platform_policy_days(platforms)
    policy_failure_rows = filter_lists[0]
    policy_failure_ids = (
        {a.get("id") for a in policy_failure_rows} if policy_failure_rows else set()
    )
    filter_counts = {
        name: len(rows or []) for name, rows in zip(_COMPLIANCE_FILTERS, filter_lists, strict=True)
    }
    compliance_exempt_ids = await compliance_exemptions.exempt_ids(session, tenant_id)
    compliance_result = analysis.compliance_breakdown(
        accounts,
        policy_failure_ids=policy_failure_ids,
        filter_counts=filter_counts,
        platform_days=platform_days,
        default_stale_days=stale_days,
        exempt_ids=compliance_exempt_ids,
    )
    compliance_simple = {
        "compliant": compliance_result["compliant"],
        "non_compliant": len(accounts) - compliance_result["compliant"],
        "compliance_pct": compliance_result["compliant_pct"],
    }

    # ---- El resto: pipeline de ITR, verbatim ----
    acc = itr_analysis.analyze_accounts(accounts)
    plat = itr_analysis.analyze_platforms(platforms, acc["by_platform"])
    safes_enriched = _with_account_counts(safes, acc["by_safe"])
    saf = itr_analysis.analyze_safes(safes_enriched, safe_members_raw)
    cpm_errors = itr_analysis.analyze_cpm_errors(accounts, acc["failed_ids"])

    components_raw = dict(zip(_COMPONENT_IDS, component_lists, strict=True))
    if is_privilege_cloud:
        # PVWA y AIM/Credential Provider son infraestructura que gestiona
        # CyberArk en Privilege Cloud/ISPSS, no el equipo PAM
        # (a diferencia de Self-Hosted, donde el cliente sí los administra) —
        # a pedido de Alex (2026-09-29), no aportan al reporte de un tenant
        # cloud. Al sacar la clave completa de `components_raw`, también
        # desaparecen sus cuentas de servicio (ej. PVWAAppUser) de la tabla
        # de conectividad detallada, que sólo itera lo que quede acá.
        components_raw = {
            k: v for k, v in components_raw.items() if k not in _CLOUD_MANAGED_COMPONENT_IDS
        }
    components_summary = itr_analysis.analyze_components(components_raw)
    components_detailed = itr_analysis.analyze_components_detailed(components_raw)
    connectors_analysis = itr_analysis.analyze_connectors(connectors_raw)
    sia_connectors_analysis = itr_analysis.analyze_sia_connectors(sia_connectors_raw, sia_details)
    licenses = itr_analysis.analyze_licenses(license_report)
    users_analysis = itr_analysis.analyze_users(users_raw, safe_members_raw, identity_users_raw)
    safe_members_analysis = itr_analysis.analyze_safe_members(safe_members_raw)
    accounts_table = itr_analysis.analyze_accounts_table(accounts, activities_by_id)
    accounts_created_labels, accounts_created_values = (
        itr_analysis.analyze_accounts_created_by_month(accounts)
    )
    compliance_by_platform = itr_analysis.analyze_compliance_by_platform(
        accounts,
        platforms,
        safes,
        {
            "DisabledPasswordByUser": filter_lists[1] or [],
            "DisabledPasswordByCPM": filter_lists[2] or [],
        },
        failed_ids=acc["failed_ids"],
    )
    # Vacío si no se pidió Sesiones — mismo criterio de siempre, no rompe
    # la firma no-opcional de `build_html_report`.
    account_activity = itr_analysis.analyze_account_activity(accounts, activities_by_id)
    concurrent_sessions = itr_analysis.analyze_concurrent_sessions(
        activities_by_id, accounts=accounts
    )

    # `report_builder` sólo sabe leer un logo desde un PATH de archivo (lo
    # convierte a data URI él mismo) — se vuelca a un temporal en vez de
    # tocar esa función verbatim para que acepte bytes directo.
    # Infraestructura: los HealthCheck_*.json subidos por tenant (ver los
    # endpoints de gestión más abajo) se vuelcan a una carpeta temporal —
    # `load_infra_data` (verbatim de ITR) sólo sabe leer un directorio, no
    # bytes sueltos. Sin archivos, igual arma la pestaña con "sin datos".
    infra_rows = (
        (await session.execute(select(ItrInfraFile).where(ItrInfraFile.tenant_id == tenant_id)))
        .scalars()
        .all()
    )
    infra_dir = tempfile.mkdtemp(prefix="pam-itr-infra-")
    try:
        for row in infra_rows:
            # Defensa extra ante filas viejas cargadas antes de validar el
            # nombre: nunca escribir fuera de `infra_dir`.
            name = Path(row.filename).name
            if name != row.filename or not _INFRA_FILENAME_RE.match(name):
                data_warnings.append(f"Archivo de infraestructura ignorado: {row.filename}")
                continue
            (Path(infra_dir) / name).write_bytes(row.content)
        infra_data = itr_infra_data.load_infra_data(infra_dir)
    finally:
        shutil.rmtree(infra_dir, ignore_errors=True)
    for skipped in infra_data.get("skipped_files") or []:
        data_warnings.append(f"Archivo de infraestructura ignorado por formato inválido: {skipped}")

    # `report_builder` sólo sabe leer un logo desde un PATH de archivo (lo
    # convierte a data URI él mismo) — se vuelca a un temporal en vez de
    # tocar esa función verbatim para que acepte bytes directo. Se crea
    # dentro del `try` para que el `finally` lo borre pase lo que pase.
    client_logo_path: str | None = None
    try:
        if logo_bytes:
            ext = _LOGO_EXT.get(logo_content_type or "", ".png")
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
                client_logo_path = tmp.name
                tmp.write(logo_bytes)

        html = await asyncio.to_thread(
            report_builder.build_html_report,
            client_name=client_name,
            environment_name=environment_name,
            accounts_analysis=acc,
            accounts_table=accounts_table,
            account_activity=account_activity,
            concurrent_sessions=concurrent_sessions,
            accounts_created_labels=accounts_created_labels,
            accounts_created_values=accounts_created_values,
            compliance=compliance_simple,
            compliance_breakdown=compliance_result,
            cpm_errors=cpm_errors,
            components_summary=components_summary,
            components_detailed=components_detailed,
            connectors_analysis=connectors_analysis,
            licenses=licenses,
            users_analysis=users_analysis,
            safes_analysis=saf,
            safe_members_analysis=safe_members_analysis,
            platforms_analysis=plat,
            compliance_by_platform=compliance_by_platform,
            sia_connectors_analysis=sia_connectors_analysis,
            sia_connectors_error=sia_connectors_error,
            client_logo_path=client_logo_path,
            infra_data=infra_data,
            excluded_tabs=excluded_tabs,
            client_templates_dir=report_builder.client_templates_dir_for(tenant_id),
            data_warnings=data_warnings,
        )
    finally:
        if client_logo_path:
            Path(client_logo_path).unlink(missing_ok=True)

    return html, {
        "accounts": acc["total"],
        "platforms": plat["total"],
        "safes": saf["total"],
        "compliance_pct": compliance_result["compliant_pct"],
        "infra_files": len(infra_rows),
        "data_warnings": data_warnings,
    }


async def _audit(
    session: AsyncSession,
    op: Operator,
    request: Request,
    tenant_id: str,
    result: str,
    detail: dict,
    reason: str,
) -> None:
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="report.itr.generate",
        result=result,
        tenant_id=tenant_id,
        source_ip=request.client.host if request.client else None,
        detail={**detail, "reason": reason},
    )
    await session.commit()


def _infra_file_dict(row: ItrInfraFile) -> dict:
    return {
        "id": row.id,
        "filename": row.filename,
        "size_bytes": row.size_bytes,
        "uploaded_by": row.uploaded_by,
        "uploaded_at": row.created_at.isoformat(),
    }


@router.get("/itr/infra-files")
async def list_infra_files(
    tenant_id: str,
    _: Operator = Depends(_tenant_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Los `HealthCheck_*.json` ya cargados para este tenant — alimentan la
    pestaña Infraestructura del reporte ITR (ver docstring del módulo).
    Mismo RBAC que generar el reporte: `tenant_admin+`."""
    rows = (
        (
            await session.execute(
                select(ItrInfraFile)
                .where(ItrInfraFile.tenant_id == tenant_id)
                .order_by(ItrInfraFile.filename)
            )
        )
        .scalars()
        .all()
    )
    return {"tenant": tenant_id, "files": [_infra_file_dict(r) for r in rows]}


@router.post("/itr/infra-files")
async def upload_infra_files(
    tenant_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    op: Operator = Depends(_tenant_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Sube uno o más `HealthCheck_<host>_<timestamp>.json` (el nombre que
    ya trae el `.ps1` de referencia) — uno por servidor del cliente. Un
    archivo con el nombre mal formado o que supere el tamaño máximo se
    rechaza SIN subir ninguno del lote, para no dejar una carga a medias."""
    if not files:
        raise HTTPException(status_code=422, detail="no se envió ningún archivo")

    to_create: list[ItrInfraFile] = []
    seen: set[str] = set()
    for f in files:
        filename = f.filename or ""
        if filename in seen:
            raise HTTPException(
                status_code=422, detail=f"'{filename}' está repetido en la misma carga"
            )
        seen.add(filename)
        if not _INFRA_FILENAME_RE.match(filename):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"'{filename}': el nombre debe seguir el patrón "
                    "HealthCheck_<host>_<timestamp>.json (el que ya genera el .ps1)"
                ),
            )
        content = await f.read()
        if not content:
            raise HTTPException(status_code=422, detail=f"'{filename}' está vacío")
        if len(content) > _MAX_INFRA_FILE_BYTES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"'{filename}' supera el máximo permitido "
                    f"({_MAX_INFRA_FILE_BYTES // (1024 * 1024)} MB)"
                ),
            )
        to_create.append(
            ItrInfraFile(
                tenant_id=tenant_id,
                filename=filename,
                content=content,
                size_bytes=len(content),
                uploaded_by=op.username,
            )
        )

    # Un archivo con el mismo nombre ya cargado se reemplaza (volver a subir
    # el mismo HealthCheck corregido), en vez de duplicarlo: al volcarlos a
    # la carpeta temporal uno pisaría al otro sin aviso.
    existing = (
        (
            await session.execute(
                select(ItrInfraFile).where(
                    ItrInfraFile.tenant_id == tenant_id, ItrInfraFile.filename.in_(seen)
                )
            )
        )
        .scalars()
        .all()
    )
    replaced = sorted(row.filename for row in existing)
    for row in existing:
        await session.delete(row)
    await session.flush()

    session.add_all(to_create)
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="itr.infra_file.upload",
        result="ok",
        tenant_id=tenant_id,
        source_ip=request.client.host if request.client else None,
        detail={"filenames": [f.filename for f in to_create], "replaced": replaced},
    )
    await session.commit()
    return {"tenant": tenant_id, "uploaded": len(to_create), "replaced": len(replaced)}


@router.delete("/itr/infra-files/{file_id}")
async def delete_infra_file(
    tenant_id: str,
    file_id: int,
    request: Request,
    op: Operator = Depends(_tenant_admin),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = await session.get(ItrInfraFile, file_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="archivo no encontrado")

    filename = row.filename
    await session.delete(row)
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="itr.infra_file.delete",
        result="ok",
        tenant_id=tenant_id,
        target=str(file_id),
        source_ip=request.client.host if request.client else None,
        detail={"filename": filename},
    )
    await session.commit()
    return {"tenant": tenant_id, "deleted": file_id}
