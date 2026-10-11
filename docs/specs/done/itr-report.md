# itr-report — generar el reporte ITR desde PAM Web Console

- **Estado:** aprobada
- **Roadmap:** `docs/PLAN.md §7` Fase 1 "Falta" ("migración del reporte ITR
  — no planificada por ahora") — se desparkea a pedido de Alex
  (2026-09-17): "un spec grande y muy útil es copiar el ITR al PAM Web Console, para
  poder generarlo desde el espacio de reportes... así mi equipo lo genera
  desde allí y no depende de que lo genere yo por comando".
- **Fecha:** 2026-09-17

## Problema

El reporte ITR mensual (el "Vault Analysis Report" reconstruido vía API,
que reemplaza parcialmente al de CyberArk) hoy sale de
`Telemetry-Reporter` (ITR), un script de línea de comandos que sólo
Alex corre a mano (`python generate_report.py --config
clientes/<cliente>/config.json`). Eso lo convierte en un cuello de botella:
el equipo PAM no puede generarlo sin pedírselo a Alex, ni un día que él no
esté disponible.

PAM Web Console ya es la webapp con auth/RBAC/audit del equipo — el lugar natural
para que cualquier operador con el permiso adecuado dispare la generación
sin depender de una terminal ni de credenciales de CyberArk en un archivo
de config local.

## Qué es ITR hoy (para quien no lo conozca)

Pipeline lineal de 4 módulos (`Telemetry-Reporter/CLAUDE.md`):

```
cyberark_api.py  →  analysis.py  →  report_builder.py  →  templates/*.j2
(fetch crudo)       (transforma)     (arma "contexto")     (HTML/CSS/JS)
```

Salida: **un único archivo HTML autocontenido** (sin backend propio —
Chart.js vía CDN se carga recién cuando alguien lo abre en el navegador),
con pestañas: Resumen, Sesiones, Componentes, Users, Safes, Platforms,
Accounts, KPI's, Hallazgos, Infraestructura. Reusa datos de CyberArk vía
API REST Gen2 (Accounts/Safes/Platforms/Components/Users) + Identity
(licencias, usuarios) + Connector Management + SIA Connectors.

PAM Web Console ya reusó de acá `cyberark_api.py` (base async del cliente) y
`analysis.py` (algunas funciones) desde el día 0 — ver el encabezado de
`CLAUDE.md`, "de ITR se reutiliza cyberark_api.py y analysis.py; NO
report_builder.py ni las plantillas". **Esta spec reconsidera esa segunda
mitad de la decisión**, a propósito y sólo para este caso puntual: no
para que PAM Web Console se convierta en un generador de HTML en general, sino
porque el reporte ITR es un documento específico que el negocio ya
conoce, ya confía y no tiene sentido rediseñar desde cero como dashboards
nativos de React — el pedido es sacarle la dependencia de la CLI, no
reinventarlo.

## Alcance

**Entra (v1):**

- Pestañas que se pueden armar con datos que PAM Web Console ya sabe pedir rápido
  (sin fetch por cuenta): **Resumen, Compliance (desglose), Errores de
  CPM, Componentes, Safes, Platforms, Accounts, Users, KPI's**.
- **Sesiones** (2026-09-18, agregado a pedido de Alex — "qué nos falta
  para dejarlo como el ITR original") — opt-in vía
  `ItrReportIn.include_sessions`, checkbox "Incluir Sesiones" en
  `ItrReportSection.tsx`. Necesita `GET /Accounts/{id}/Activities` **por
  cada cuenta** (para Globex, ~1900 llamadas) — se hace igual,
  síncrono, sólo si se pide explícitamente (mismo criterio que
  `account.reveal.bulk`/`credentials_backup.py`: una llamada por cuenta,
  ninguna aborta el resto). Sin tildar el checkbox, se comporta como
  antes (más rápido, sin esa pestaña ni la columna "Last Logon" de
  Accounts).
- **Infraestructura** (2026-09-18, mismo pedido) — los
  `HealthCheck_<host>_<timestamp>.json` que deja el `.ps1` corrido a mano
  en cada servidor del cliente ahora se suben por tenant
  (`ItrInfraFile`, migración `0012`) desde una sección nueva en
  `ItrReportSection.tsx`. `app/api/itr.py` los vuelca a un directorio
  temporal antes de llamar a `app.itr.infra_data.load_infra_data`
  (portado de ITR verbatim — sólo sabe leer una CARPETA). Sin archivos
  subidos, la pestaña sale con el mismo "sin datos" que ya usa ITR — no
  hace falta ocultarla de la nav.
- Nuevas integraciones de API que ITR tiene y PAM Web Console todavía no
  (`client.py` gana métodos, `analysis.py` gana funciones — ver Diseño
  técnico): **Licencias** (Identity), **Connector Management**, **SIA
  Connectors**. Estas pestañas hoy sólo existen en ITR; van al catálogo
  de funciones ya existente en PAM Web Console, no a un módulo aparte.
- `report_builder.py` + `templates/*.j2` se **portan casi tal cual**
  (son Jinja2 + HTML/CSS/JS puro, no dependen de la arquitectura CLI de
  ITR) a `backend/app/itr/` — ver Diseño técnico para qué cambia y qué no.
- Generación **on-demand desde la página Reportes** — un botón, como
  `CredentialsBackupSection.tsx` (mismo patrón visual: razón opcional →
  "Generar reporte ITR" → descarga el `.html`). **Síncrono, sin cola de
  jobs** (igual que `account.reveal.bulk`): puede tardar 1-2 minutos en
  un tenant grande (todas las membresías de todas las Safes, además de
  Accounts/Platforms/Users/Components) — bastante más si se tilda
  "Incluir Sesiones" (1 request extra por cuenta). El `proxy_read_timeout`
  de nginx (600s) ya extendido para operaciones similares alcanza para
  ambos casos.
- RBAC `tenant_admin+` — mismo techo que `credentials_backup`: no revela
  secretos, pero agrega TODO el estado del tenant en un solo documento
  pensado para compartir con el cliente. `reason` opcional (para el
  audit, no bloquea la generación). Sin feature-flag (es lectura, no
  toca CyberArk).
- Sin caché — se genera fresco cada vez (mismo criterio que
  `credentials_backup.py`; es una acción ocasional, no una vista que se
  visita seguido).
- Branding: el logo del MSP (embebido como asset de ITR) **+ el logo
  del cliente** (2026-09-18, agregado a pedido de Alex — ITR ya lo tenía
  vía `client_logo_path` en `config.json`, quedó afuera del corte inicial
  de esta spec y se sumó apenas se notó la falta). `Tenant.client_logo` +
  `client_logo_content_type` (bytes en la DB, migración `0011`) — mismo
  patrón que `compliance_stale_days`: editable en runtime, no vive en
  `seed/tenants.json`. `PUT`/`DELETE`/`GET /tenants/{id}/logo`
  (`app/api/tenants.py`), `platform_admin` para subir/borrar, `viewer+`
  para leer (sólo branding, nada sensible). PNG/JPEG, hasta 2 MB.
  `app/api/itr.py` vuelca los bytes a un archivo temporal antes de llamar
  a `build_html_report` (que sólo sabe leer un logo desde un PATH) y lo
  borra apenas termina — no se tocó esa parte verbatim de ITR.
- Auditado `report.itr.generate` (ok/error), una vez por generación.
- **Pestañas a incluir + caché** (2026-09-18, mismo pedido — ITR ya tenía
  ambos por CLI): `ItrReportIn.excluded_tabs` (checklist en
  `ItrReportSection.tsx`, calcado de `select_tabs_interactively()` de
  `generate_report.py` — los datos de una pestaña excluida se siguen
  calculando igual, sólo cambia la nav) + `ItrReportIn.use_cache`
  (checkbox "Usar caché de datos crudos", calcado de `cache_utils.
  cached_fetch()` — cachea en Redis, opt-in, pensado para iterar rápido
  sobre cambios al template sin repegarle a CyberArk, no para el reporte
  real).

**Fuera de esta spec (a propósito):**

- **Modo `self_hosted`** — PAM Web Console v1 sólo opera Privilege Cloud (banner de
  alcance de todo el proyecto). Todo el código de `SelfHostedAuth`/
  `get_all_user_details` (LastLogin por usuario sin Identity) de ITR no
  aplica.
- **Layout/plantillas personalizadas por cliente**
  (`clientes/<cliente>/templates/` de ITR) — escape hatch que hoy nadie
  usa en producción según el repo de ITR; no se porta. **Adenda
  2026-09-29: esta premisa era parcialmente falsa** — comparando contra el
  repo de ITR se encontró que Acme/Hooli tienen un override chico de
  header (logo con fondo propio, sin la placa blanca del estilo base) e
  Initech un layout propio sustancial (`report.html.j2` + 8 pestañas,
  "dashboard jerarquizado"). Se portó **sólo el fix de header** de
  Acme/Hooli (`app/itr/clientes/<tenant_id>/templates/partials/
  _header.html.j2` + `report_builder.client_templates_dir_for()`, ver
  `app/api/itr.py`) — el mecanismo en sí (`_make_jinja_env`/`ChoiceLoader`)
  ya venía portado sin usarse, sólo faltaba invocarlo. **El layout de Initech
  a propósito sigue sin portarse** (decisión de Alex, mismo día): el
  reporte de un tenant generado desde PAM Web Console tiene que salir tan completo
  como el de cualquier otro (mismas 10 pestañas, layout genérico) — para
  sacar una pestaña puntual ya está `excluded_tabs`, no un template por
  cliente que reorganiza dónde vive cada dato.
- **Exclusiones configurables** (`excluded_summary_components`/
  `excluded_active_components` de ITR — cuentas de servicio internas que
  no aportan al análisis) — v1 muestra todo. Si hace falta, es un campo
  más de `Tenant` a futuro.
- **`entra_id_cert_expires_on`** (vencimiento del certificado de
  federación Entra ID, dato manual) — sin lugar obvio en el modelo de
  PAM Web Console todavía; queda afuera.

## Requisitos

1. **Generar el reporte, camino feliz.** Un `tenant_admin+` en Reportes
   hace clic en "Generar reporte ITR" → PAM Web Console pega a CyberArk (Accounts,
   Safes + miembros, Platforms, Components, Users, Identity licenses,
   Connector Management, SIA Connectors, en paralelo con el mismo
   semáforo de concurrencia que el resto del cliente) → arma el HTML →
   lo devuelve como descarga (`Content-Disposition: attachment;
   filename="itr_report_<tenant>_<fecha>.html"`).
2. **RBAC insuficiente.** Un `operator`/`approver` (no `tenant_admin+`)
   no ve el botón ni puede pegarle directo al endpoint (403).
3. **Una fuente que falla no rompe todo el reporte.** Si Connector
   Management o SIA Connectors devuelven 403 (rol insuficiente del
   service account, caso real ya documentado en ITR) o cualquier otra
   fuente no-crítica falla, esa pestaña se genera "sin datos" (mismo
   texto que ya usa ITR) y el resto del reporte sale igual — sólo
   Accounts/Platforms son "imprescindibles" (mismo criterio que
   `REQUIRED_TASKS` de ITR); si esas dos fallan, 502 sin generar nada.
4. **Auditoría.** Cada generación (éxito o error) deja un
   `report.itr.generate` con el tenant, resultado, y `reason` si se
   cargó una.
5. **Sin secretos.** El HTML generado no contiene ninguna contraseña ni
   valor de secreto — mismos datos que ya expone el resto de PAM Web Console
   (metadata), nunca `retrieve_password`.
6. **Aislamiento por tenant.** Igual que cualquier otro endpoint: un
   `CyberArkClient` nuevo por request, sin compartir entre tenants.

## Diseño técnico

### Nuevos métodos de `app/cyberark/client.py`

Portados de `Telemetry-Reporter/cyberark_api.py` (adaptados a
async, mismo patrón `_request`/retry/re-auth que el resto del cliente):

```python
async def get_license_report(self) -> dict: ...        # Identity, licencias
async def list_connectors(self) -> list: ...            # Connector Management
async def list_sia_connectors(self) -> list: ...         # SIA (subdominio .dpa.)
async def get_sia_connector_details(self, connector_id: str) -> dict | None: ...
async def get_all_sia_connector_details(self, connector_ids: list[str]) -> dict: ...  # en paralelo
async def get_identity_users_full(self, page_size: int = 200) -> list[dict]: ...  # Redrock, paginado
```

`connector_management_url`/`sia_connector_url` ya son campos de `Tenant`
(existen desde el día 0, sin usar todavía) — no hace falta tocar el
modelo ni `seed/tenants.json`.

### `backend/app/itr/` (paquete nuevo) — **corrección de diseño
2026-09-17, ver abajo**

- `analysis.py` — **copiado de ITR CASI TAL CUAL (no sólo unas funciones
  sueltas), self-contained** (sólo `re`/`collections`/`datetime`, ningún
  import de `app.*`). Único cambio: ninguno — se usa exactamente como
  produce ITR hoy.
- `report_builder.py` — copiado de ITR casi tal cual. Cambios reales:
  - `build_html_report(...)`: `output_path` pasa a ser opcional
    (`None` por default) — si no se pasa, devuelve el HTML como `str`
    en vez de escribirlo a disco (PAM Web Console lo sirve como `Response`, no
    genera un archivo local).
  - El resto (armado de contexto, `TAB_ORDER`, `_image_to_data_uri`,
    `client_logo_path=None`/`infra_data=None`/`client_templates_dir=None`
    por default, etc.) se copia igual — es Jinja2 puro, no toca red ni
    CyberArk.
- `templates/` — carpeta copiada tal cual de ITR (2595 líneas,
  `report.html.j2` + partials).
- `logos/logo_brand*.png` — copiados de ITR (branding propio del
  proyecto, ya se commitea en ITR igual que en PAM Web Console).

**Por qué el port es completo y NO selectivo dentro de `app/analysis.py`
(corrige el plan original de esta spec):** al revisar `report_builder.py`
en detalle se confirmó que consume `accounts_analysis`/`platforms_analysis`/
`safes_analysis`/`cpm_errors` con MUCHAS más claves de las que producen
hoy los `analyze_accounts`/`analyze_platforms`/`analyze_safes`/
`analyze_cpm_errors` YA EVOLUCIONADOS de `app/analysis.py` (ej.
`platforms_analysis["no_periodic_verification"]`,
`safes_analysis["concentrated_admin_count"]`,
`accounts_analysis["age_buckets_verify"]` — ninguno existe en la versión
de PAM Web Console, que divergió de la de ITR durante todo este proyecto para sus
propios fines de Dashboard/Compliance). Intentar reconciliar campo por
campo es fràgil y arriesga romper el Dashboard existente. En cambio,
`app/itr/analysis.py` es un módulo PARALELO, usado sólo por el reporte
ITR — reproduce exactamente el pipeline que ITR ya corre en producción,
sin tocar `app/analysis.py` ni el Dashboard. La única excepción: el `%`
de Compliance del reporte reusa `app.analysis.compliance_breakdown` (la
versión de PAM Web Console, EXACTA por cuenta y consciente de la política de cada
plataforma — mejor que el `analyze_compliance`/`analyze_compliance_breakdown`
aproximado de ITR) porque la forma de su resultado
(`{"categories", "compliant_pct"}`) sí coincide con lo que espera el
template.

### `app/api/itr.py` (router nuevo)

```
POST /tenants/{tenant_id}/reports/itr/export
```

Funciones que usa de `app.itr.analysis` (ITR, verbatim): `analyze_accounts`,
`analyze_safes`, `analyze_platforms`, `analyze_cpm_errors`,
`analyze_components`, `analyze_components_detailed`, `analyze_connectors`,
`analyze_sia_connectors`, `analyze_accounts_created_by_month`,
`analyze_accounts_table`, `analyze_compliance_by_platform`,
`analyze_users`, `analyze_safe_members`, `analyze_licenses`,
`analyze_account_activity`, `analyze_concurrent_sessions` (estos dos
últimos con `activities_by_id={}` si `include_sessions` es `false` —
mismo criterio de siempre, sólo para no romper la firma no-opcional de
`build_html_report`). De `app.analysis` (PAM Web Console): `platform_policy_days` +
`compliance_breakdown`, para el % de Compliance real del tenant. De
`app.itr.infra_data` (ITR, verbatim): `load_infra_data(dir)`.

Mismo esqueleto que `app/api/credentials_backup.py`: `tenant_client`,
`asyncio.gather` de las fuentes (accounts/safes+members/platforms/
components/users/licenses/connectors/sia — con `return_exceptions=True`
salvo accounts/platforms), arma el contexto con las funciones de arriba,
llama a `app.itr.report_builder.build_html_report` (en un hilo —
`asyncio.to_thread`, es CPU-bound: Jinja2 render de un HTML de +1MB),
devuelve `Response` con el HTML y `Content-Disposition: attachment`.
Auditado `report.itr.generate` antes de responder (ok con conteos
básicos en `detail`, o error).

**Sesiones (2026-09-18):** `_all_account_activities(client, accounts)` —
`client.account_activities(id)` (ya existía, la usa `GET /accounts/{id}/
activities`) para TODAS las cuentas EN PARALELO, mismo criterio de
degradación por cuenta que el resto del router. Sólo se llama si
`ItrReportIn.include_sessions` es `true`.

**Infraestructura (2026-09-18):** `ItrInfraFile` (migración `0012`) —
tabla con los `HealthCheck_*.json` subidos por tenant (bytes en la DB,
como el logo del cliente). El endpoint principal los lee, los vuelca a
un `tempfile.mkdtemp()` y llama a `load_infra_data(dir)` — mismo patrón
que `client_logo_path` (delegar a una función verbatim de ITR que sólo
sabe leer del filesystem, sin tocarla). Gestión propia en el mismo
router: `GET/POST /tenants/{id}/itr/infra-files` (listar/subir, multipart
con varios archivos a la vez) + `DELETE .../itr/infra-files/{file_id}` —
mismo RBAC `tenant_admin+` que generar el reporte. El nombre debe
matchear `^HealthCheck_.*\.json$` (el patrón real que usa el
`glob.glob` de `load_infra_data`) — se valida al subir, TODO el lote
se rechaza si un archivo no matchea, para no dejar una carga a medias.
1 MB máximo por archivo.

**Pestañas a incluir (2026-09-18):** `ItrReportIn.excluded_tabs` — ids de
`report_builder.TAB_ORDER`, validados contra esa misma fuente (422 si
alguno no existe). Se combina con la exclusión automática de "sessions"
cuando `include_sessions` es `false`. Pasa directo a `excluded_tabs` de
`build_html_report`.

**Caché de datos crudos (2026-09-18):** `ItrReportIn.use_cache` — clave
`pam:itr-raw:{tenant_id}:sessions={0|1}` en Redis (`app.core.cache`, TTL
`PAM_ITR_CACHE_TTL`, default 1800s), separada por `include_sessions` a
propósito (un hit guardado SIN Sesiones no debe usarse para una corrida
CON Sesiones — mostraría esa pestaña vacía por error). Guarda TODOS los
datos crudos (accounts/platforms/safes/users/licencias/connectors/sia/
identity/los 4 savedFilters/component_details/safe_members/activities) —
`filter_lists`/`component_lists` se resuelven (`_ok()`) ANTES de cachear,
para que el blob sea JSON-serializable de punta a punta. Con un hit, el
único request real a CyberArk es la autenticación de `tenant_client`
(no vale la pena duplicar su validación de tenant/deployment para
ahorrarse esa sola llamada). A diferencia del resto de `app.core.cache`
(sólo métricas agregadas), esta caché SÍ guarda metadata cruda de
cuentas — nunca secretos, pero es la primera excepción a ese criterio;
por eso queda apagada por default y el checkbox del frontend aclara que
es sólo para testear cambios al reporte, no para el que se comparte con
el cliente.

`app/main.py`: registrar el router nuevo.

### Frontend

- `components/ItrReportSection.tsx` (nuevo, calcado de
  `CredentialsBackupSection.tsx`): razón opcional → `apiBlob` POST →
  `saveBlob(blob, "itr_report_<tenant>_<fecha>.html")`. Mismo patrón de
  aviso "puede tardar 1-2 minutos". **(2026-09-18)** checkbox "Incluir
  Sesiones" (`include_sessions` en el body) + `InfraFilesSection`
  interna: lista de `HealthCheck_*.json` ya subidos (`GET .../
  infra-files`) + botón "Subir archivo(s)" (`<input type="file"
  multiple>` → `apiForm`, POST multipart) + "Quitar" por fila (`DELETE`)
  + checklist "Pestañas a incluir" (`OPTIONAL_TABS`, mismos ids que
  `report_builder.TAB_ORDER` menos "sessions" — esa la controla el
  checkbox de Sesiones) + checkbox "Usar caché de datos crudos"
  (`use_cache` en el body, con aviso de que es sólo para testear).
- `pages/Reports.tsx`: agrega la sección, gateada a `tenant_admin+`
  (igual que `CredentialsBackupSection`).
- Sin tipos nuevos relevantes — la respuesta es un blob, no JSON.
- `pages/Tenants.tsx` (2026-09-18) — `LogoSettings`: subir/reemplazar/
  quitar el logo del cliente, calcado de `ComplianceSettings` en estilo.
  Vista previa vía `apiBlob` + `URL.createObjectURL` (el `<img>` no puede
  mandar el Bearer token en `src`, así que no alcanza con apuntarlo
  directo a `GET /tenants/{id}/logo`). Subida con `apiForm` (multipart,
  ya existía para `platform.import`). Sólo visible dentro de `Tenants.tsx`
  (`platform_admin`, la página entera ya está gateada así).
- **Gotcha real probando en el server (2026-09-18):** la vista previa
  mostraba el ícono de imagen rota — mismo bug que `<video>` en
  `recording-player.md`: el `blob:` URL del `<img src>` lo bloqueaba la
  CSP (`img-src` sólo tenía `'self' data:`). Fix: `img-src 'self' data:
  blob:` en `frontend/security-headers.conf`.

## Decisiones confirmadas con Alex (2026-09-17)

1. **RBAC `tenant_admin+`** — confirmado.
2. **Licencias/Connector Management/SIA Connectors SÍ entran en el v1**
   — confirmado, reporte completo desde el principio.
3. **Botón en la página Reportes**, junto a "Backup de credenciales" —
   confirmado.
4. **Mantener el nombre "ITR"** (2026-09-17, mismo día) — Alex prefirió
   conservar el nombre que el negocio ya conoce ("ITR") en vez de
   renombrar la función a "HealthCheck": endpoint, módulo, componente,
   auditoría y textos de UI usan "ITR"/"reporte ITR" de punta a punta
   (`app/itr/`, `app/api/itr.py`, `POST .../reports/itr/export`,
   `report.itr.generate`, `ItrReportSection.tsx`, filename
   `itr_report_<tenant>_<fecha>.html`).
5. **Logo del cliente** (2026-09-18) — Alex probó el reporte y notó que
   faltaba el logo del cliente junto al logo del equipo, tal como lo hacía
   ITR. Se agregó como parte de esta misma spec (no una spec aparte, es
   chico y acota el mismo alcance de branding).
6. **Sesiones e Infraestructura SÍ entran** (2026-09-18, mismo día —
   "qué nos falta para dejarlo como el ITR original, así lo llevamos
   directo desde acá sin depender del otro proyecto"). Sesiones: síncrona
   pero **opt-in** (checkbox), no bloquea el flujo normal — evita esperar
   a tener jobs en background. Infraestructura: sí, con un mecanismo de
   carga de archivos nuevo (`ItrInfraFile`) — es la única pestaña que
   sigue dependiendo de que alguien corra el `.ps1` a mano en cada
   servidor del cliente, eso no lo resuelve PAM Web Console ni lo resolvía ITR.
7. **Pestañas a incluir + caché SÍ entran** (2026-09-18, mismo día — Alex:
   "cuando lo generaba desde consola tenía la opción de escoger si quería
   ocultar alguna pestaña... y lo del caché por si quería generarlo más
   rápido cuando testeaba cambios en el reporte"). Ambos son opt-in y no
   cambian el comportamiento por default (todas las pestañas, sin caché).

## Tareas

- [x] `client.py`: `get_license_report`/`list_connectors`/
      `list_sia_connectors`/`get_sia_connector_details`/
      `get_all_sia_connector_details`/`get_identity_users_full`.
- [x] `backend/app/itr/`: copiar `analysis.py` (completo, ver
      "corrección de diseño" arriba) + `report_builder.py` (adaptado) +
      `templates/` + logos de ITR.
- [x] `app/api/itr.py`: `POST /tenants/{id}/reports/itr/export`.
- [x] `app/main.py`: registrar el router.
- [x] Tests unit (`tests/unit/test_itr.py`, 15): RBAC (`tenant_admin` ok,
      `operator` 403), camino feliz (200, HTML real,
      `Content-Disposition`), `reason` opcional, una fuente no-crítica
      caída (Connector Management) no rompe el reporte, accounts/platforms
      caídos sí abortan (404/502), auditoría ok/error
      (`report.itr.generate`), logo del cliente embebido si está cargado,
      Sesiones (sin pedirla no pega a `Activities`; pidiéndola, pega una
      vez por cuenta), infra-files (rechaza nombre inválido, RBAC,
      subir/listar/borrar, aparece en el HTML si hay un archivo cargado).
- [x] `ruff` + `ruff format` + `mypy` + `pytest` verdes. `jinja2==3.1.6`
      nuevo en `requirements.txt` — no era una dependencia de PAM Web Console hasta
      ahora, sólo de ITR. `pyproject.toml`: `app.itr.*` excluido de
      `mypy` (`ignore_errors`) — es un port verbatim de código sin type
      hints, mismo criterio que vendorizar `scpr_decode/` sin
      reescribirlo.
- [x] `components/ItrReportSection.tsx` + entrada en `pages/Reports.tsx`
      (junto a `CredentialsBackupSection`, mismo patrón visual).
- [x] `lint` + `tsc` + `build` verdes.
- [x] Rename "HealthCheck" -> "ITR" de punta a punta (endpoint, módulo,
      router, tests, componente, filename, docs) — a pedido de Alex.
- [x] Logo del cliente (2026-09-18): `Tenant.client_logo`/
      `client_logo_content_type` (migración `0011`), `PUT`/`DELETE`/
      `GET /tenants/{id}/logo` (`app/api/tenants.py`), `LogoSettings` en
      `pages/Tenants.tsx`, `app/api/itr.py` lo vuelca a un temporal antes
      de renderizar. 6 tests nuevos en `test_features.py` + 1 en
      `test_itr.py`. `ruff`/`mypy`/`pytest` y `lint`/`tsc`/`build` verdes.
- [x] Fix: la CSP bloqueaba la vista previa del logo (`img-src` sin
      `blob:`) y la grilla de `Tenants.tsx` mostraba una card vecina
      "abierta pero en blanco" (`align-items: stretch` de CSS Grid —
      fix: `items-start`).
- [x] Sesiones e Infraestructura (2026-09-18): `ItrReportIn.
      include_sessions` (opt-in) + `_all_account_activities` en
      `app/api/itr.py`. `ItrInfraFile` (migración `0012`) +
      `GET/POST /tenants/{id}/itr/infra-files` + `DELETE .../{file_id}` +
      `app/itr/infra_data.py` (verbatim de ITR) + `InfraFilesSection` en
      `ItrReportSection.tsx`. 6 tests nuevos en `test_itr.py`.
      `ruff`/`mypy`/`pytest` y `lint`/`tsc`/`build` verdes.
- [x] Pestañas a incluir + caché (2026-09-18): `ItrReportIn.
      excluded_tabs` (validado contra `report_builder.TAB_ORDER`, 422 si
      no existe) + `ItrReportIn.use_cache` (Redis, `PAM_ITR_CACHE_TTL`,
      clave separada por `include_sessions`) en `app/api/itr.py`.
      Checklist "Pestañas a incluir" + checkbox "Usar caché" en
      `ItrReportSection.tsx`. 5 tests nuevos en `test_itr.py` (20 en
      total). `ruff`/`mypy`/`pytest` y `lint`/`tsc`/`build` verdes.
- [x] Probado contra Globex (2026-09-18) — HTML generado comparado
      contra el `itr_report_globex_*.html` real, pestaña por pestaña.
      Spec cerrada.
- [x] Revisión previa al envío mensual (2026-10-05), fixes sin spec nueva:
      banner "Datos incompletos" arriba del reporte (`data_warnings` en
      `build_html_report`) cuando falla una fuente no crítica (antes se veía
      como un cero real), con aviso explícito de "Compliance parcial" si
      falla `PolicyFailures` (antes inflaba el % sin aviso), cuando se usó la
      caché, y cuando se ignora un `HealthCheck_*.json` inválido
      (`load_infra_data` ya no rompe todo el reporte por un archivo). Fuentes
      caídas en el audit (`source_errors`). Connector Management/SIA sin URL
      configurada ya no cuentan como falla. Fallas al analizar/renderizar
      quedan auditadas (`phase: "render"`, 500). Subida de infra-files: sin
      separadores de ruta en el nombre (path traversal), repetidos en el lote
      rechazados y mismo nombre reemplaza al anterior. Textos de "sin datos"
      que mencionaban `config.json` de la CLI, reemplazados. 12 tests nuevos
      en `test_itr.py` (32 en total).
