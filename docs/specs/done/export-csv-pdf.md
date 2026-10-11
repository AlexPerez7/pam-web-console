# export-csv-pdf

- **Estado:** hecha (probada en el server 2026-09-10)
- **Roadmap:** Fase 1, ítem "Export (CSV / PDF)"
- **Fecha:** 2026-09-10

## Problema

Hoy no hay forma de sacar datos de PAM Web Console. Un operador que quiere trabajar el
inventario en Excel, mandarle una lista a un cliente, o guardar un snapshot
del estado de un tenant, tiene que copiar a mano de la grilla (que además
está paginada de a 100 en Cuentas y Safes). Falta un "Exportar".

**No** es la migración del HealthCheck de ITR — eso queda para su propia spec
cuando se decida hacerla. Esta spec cubre lo mínimo útil: **CSV de las
grillas del inventario** y **imprimir / guardar PDF del dashboard** desde el
navegador.

## Alcance

**Entra:**
- **CSV** de los 5 recursos del inventario por tenant (Cuentas, Safes,
  Plataformas, Usuarios, Grupos) — dataset completo (no sólo la página
  visible), columnas curadas y planas, generado y **auditado** en el backend.
- **Dashboard → "Imprimir / PDF"**: `window.print()` + una hoja de estilo
  `@media print` que deja el dashboard presentable en una hoja (sin sidebar,
  sin topbar, sin botones). El usuario elige "Guardar como PDF" en el diálogo
  del navegador.
- Botón de export en cada tab del inventario y en el encabezado del
  dashboard.

**Fuera (explícito):**
- Export de la **búsqueda cross-tenant** — otra iteración (necesita decidir
  si es backend auditado o client-side).
- **PDF generado / reporte con marca** (portada con marca, gráficos
  renderados server-side) — eso **es** la migración del HealthCheck, su
  propia spec.
- `xlsx`, exports programados / por mail, "exportar selección".
- CSV de Sesiones y Componentes (transitorios; se agregan si hacen falta).

## Requisitos

### R1 — CSV de un recurso del inventario

`GET /tenants/{tenant_id}/export/{resource}` con
`resource ∈ {accounts, safes, platforms, users, groups}`. Rol `viewer` sobre
el tenant (`require_tenant_access`). Auditado `<recurso>.export` con
`detail = {"count": N, "format": "csv"}`.

Respuesta: `200` con `Content-Type: text/csv; charset=utf-8` y
`Content-Disposition: attachment; filename="pam-{tenant}-{resource}-{YYYY-MM-DD}.csv"`.
Cuerpo: cabecera + una fila por objeto, columnas curadas (§Diseño). Fechas
epoch → ISO 8601 UTC. Booleanos → `true`/`false`. Campos ausentes → vacío.

**Escenario:**
Dado Globex con 1894 cuentas,
Cuando el operador hace "Exportar CSV" en el tab Cuentas,
Entonces baja `pam-globex-accounts-2026-09-10.csv` con 1894 filas + la
cabecera, y queda un `account.export` en el audit con `count: 1894`.

**Escenario (sin permiso en CyberArk):**
Dado que el Service User no puede listar usuarios,
Cuando el operador exporta Usuarios,
Entonces recibe un `403` con el detalle del error y queda un
`user.export` con `result: "error"` (mismo patrón que el `_run` del
inventario).

**Escenario (RBAC):**
Dado un operador sin acceso al tenant `acme`,
Cuando pide `GET /tenants/acme/export/accounts`,
Entonces `403` antes de tocar CyberArk.

### R2 — Botón de export en el inventario

Cada tab del inventario (los 5 recursos de R1) muestra un botón "Exportar
CSV" en la barra superior de la grilla. Al clickear, baja el archivo (fetch
con el JWT → blob → descarga; no un `<a href>` porque la auth va por header).
Mientras baja, el botón muestra un spinner y queda deshabilitado.

**Escenario:**
Dado el tab Safes de Globex,
Cuando el operador clickea "Exportar CSV",
Entonces el botón muestra "Exportando…", baja el CSV, y vuelve a su estado
normal.

### R3 — Dashboard → Imprimir / PDF

El encabezado del dashboard suma un botón "Imprimir / PDF" junto a
"Actualizar". Llama a `window.print()`. Una hoja de estilo `@media print`:

- oculta `aside` (sidebar), la topbar, y los botones de acción
  (`.no-print`);
- expande el contenedor a ancho completo, evita cortar tarjetas por la
  mitad (`break-inside: avoid`);
- deja los `BarList` / gráficos legibles en gris/negro.

El encabezado impreso muestra el nombre del tenant y la fecha/hora de
generación (ya está en el DOM: "actualizado hace X" → mostrar también el
timestamp absoluto al imprimir).

**Escenario:**
Dado el dashboard de Globex,
Cuando el operador hace "Imprimir / PDF" y elige "Guardar como PDF",
Entonces obtiene un PDF de 1–2 páginas con los KPIs, compliance, errores de
CPM y salud de componentes, sin la navegación de la app.

## Diseño técnico

### Backend

- `app/core/csv_export.py` (nuevo): `to_csv(rows, columns) -> str`.
  `columns: list[tuple[str, Callable[[dict], object]]]` — (cabecera,
  extractor). Usa `csv.writer` sobre `io.StringIO`, `\r\n`, comillas
  automáticas. Helpers: `epoch_iso(v)` (epoch s → ISO UTC, vacío si nulo/0),
  `dotted(path)` (extractor `"general.id"` → `row["general"]["id"]`),
  `flag(v)` (`bool` → `"true"/"false"`).
- `app/api/exports.py` (nuevo router, `prefix="/tenants/{tenant_id}"`,
  `tags=["export"]`):
  - `_COLUMNS: dict[str, list[tuple[str, Callable]]]` por recurso.
  - `_FETCH: dict[str, Callable[[CyberArkClient], Awaitable[list]]]` →
    reusa `list_accounts()` / `list_safes()` / `list_platforms()` /
    `list_users()` / `list_user_groups()` (dataset completo).
  - `GET /export/{resource}`: valida `resource`, `async with
    tenant_client(...)`, trae la lista con el patrón de auditoría de
    `inventory._run` (ok/error, `action=f"{singular}.export"`), arma el CSV,
    devuelve `Response(content=csv, media_type="text/csv; charset=utf-8",
    headers={"content-disposition": ...})`.
  - `resource` inválido → `404`.
- `app/main.py`: incluir el router.
- Columnas curadas (v1):
  | recurso | columnas |
  |---|---|
  | accounts | id, name, userName, address, platformId, safeName, status (`secretManagement.status`), autoManaged (`secretManagement.automaticManagementEnabled`), lastModified (`secretManagement.lastModifiedTime`→ISO), createdTime→ISO |
  | safes | safeName, safeUrlId, description, managingCPM, numberOfDaysRetention, olacEnabled, creationTime→ISO |
  | platforms | id (`general.id`), name (`general.name`), systemType (`general.systemType`), active (`general.active`) |
  | users | id, username, userType, source, componentUser, firstName (`personalDetails.firstName`), lastName (`personalDetails.lastName`), suspended, enabled (`enableUser`), lastLogin (`lastSuccessfulLoginDate`→ISO) |
  | groups | id, groupName, groupType, directory, description |

### Frontend

- `lib/api.ts`: `apiBlob(path) -> Promise<{ blob: Blob; filename: string }>`
  — mismo manejo de token + un reintento con refresh que `api()`; saca el
  filename del `Content-Disposition` (fallback: último segmento del path).
- `lib/download.ts` (nuevo): `saveBlob(blob, filename)` — crea un
  `<a download>` temporal con `URL.createObjectURL`, lo clickea, revoca.
- `components/ExportCsvButton.tsx` (nuevo): props `{ tenant, resource,
  label? }`. Estado `loading`; on click → `apiBlob` → `saveBlob`; error →
  toast/alerta inline simple.
- `components/DataTable.tsx`: prop opcional `toolbar?: ReactNode`, renderizada
  en la barra superior (junto al contador). Cada `*Tab` del inventario pasa
  `toolbar={<ExportCsvButton tenant={tenant} resource="…" />}`.
- `pages/Dashboard.tsx`: botón "Imprimir / PDF" (`onClick={() =>
  window.print()}`) junto a "Actualizar"; ambos con clase `no-print`.
- `index.css`: bloque `@media print` (oculta `aside`, `.no-print`, topbar;
  ancho completo; `break-inside: avoid` en las tarjetas).
- `App.tsx`: clase `no-print` en la topbar / menú de usuario.

### Principios

- Read-only, `GET`. Aislamiento por tenant (cliente nuevo por request).
- **Todo export se audita** (`<recurso>.export`, ok/error) — es una lectura
  masiva de datos de un tenant, tiene que quedar registrada con la identidad
  real igual que `list`.
- El CSV lleva sólo lo que ya se ve en la app; nada de secretos.

## Tareas

- [x] Backend: `app/core/csv_export.py` (`to_csv`, `epoch_iso`, `dotted`,
      `flag`).
- [x] Backend: `app/api/exports.py` (router, `_COLUMNS`, `_FETCH`, `_ACTION`,
      auditado con el patrón ok/error del inventario), incluido en `main.py`.
- [x] Backend: `tests/unit/test_csv_export.py` (dotted, epoch→ISO, escape de
      comillas/comas, booleanos, y que `_COLUMNS`/`_FETCH` cubren los mismos
      5 recursos).
- [x] Backend: `ruff` + `mypy` + `pytest` (35) verdes.
- [x] Frontend: `apiBlob` (api.ts) + `saveBlob` (`lib/download.ts`) +
      `ExportCsvButton` + prop `toolbar` en `DataTable` + botón en los 5
      tabs.
- [x] Frontend: botón "Imprimir / PDF" en el dashboard
      (`window.print()`) + `@media print` en `index.css` + `print:hidden` en
      el shell (`aside`, `header`, controles del dashboard).
- [x] Frontend: `npm run lint` + `tsc` + `build` verdes.
- [x] `CLAUDE.md` + `docs/PLAN.md` (§7 Fase 1) actualizados.
- [x] Probado en el server (2026-09-10): CSV de los 5 recursos + "Imprimir /
      PDF" del dashboard.

## Notas de cierre

- El ocultado de la navegación al imprimir lo hacen las utilidades
  `print:hidden` de Tailwind v4 en el markup; `index.css` sólo aporta el
  reset de padding de `main` y `break-inside: avoid` en las tarjetas.
- `apiBlob` reusa `doFetch` + `tryRefresh` de `api.ts` (un reintento con
  refresh, igual que `api()`), y saca el filename del `Content-Disposition`.
- El endpoint trae el dataset completo con los `client.list_*` que ya
  existen; para Cuentas ignora el `search`/paginado del inventario (el CSV
  es "todo el tenant").

## Notas de implementación

- Cuentas de Globex: ~1900 filas, CSV chico; no hace falta streaming.
  Si algún tenant tuviera decenas de miles se evalúa `StreamingResponse`.
- El `Content-Disposition` con `filename=` ASCII alcanza (tenant/resource son
  slugs). No hace falta `filename*=UTF-8''`.
- `window.print()` no da control fino de paginado; el objetivo es "sale
  presentable", no "pixel-perfect". Un PDF con portada/gráficos es la
  migración del HealthCheck.
