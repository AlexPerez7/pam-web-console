# reportes — spec macro

- **Estado:** maquinaria + Batches 1, 2 y 3 probados en el server
  (2026-09-10). Batch 1 (safe-duplicates, safe-permissions,
  account-inventory) + export CSV contra Globex/Acme. Batch 2
  (`psm-balance`) y Batch 3 (`license-candidates`, `identity-user-activity` —
  Identity Redrock) funcionando; todos los tenants Privilege Cloud tienen
  Identity. Batch 4 (`target-inactivity`, `account-activity`) bloqueado hasta
  que PAM Web Console tenga jobs (ARQ) — no se implementa en esta spec. Decisiones: GET,
  caché Redis con TTL, los dos exports coexisten, `viewer` para todos.
  **Fusión (2026-09-22):** `identity-user-activity` se dio de baja —
  compartía el 90% de la lógica con `license-candidates` (mismo cruce por
  username, misma noción de "candidato" por inactividad) con un alcance
  apenas distinto (todos los usuarios de Identity vs. sólo los que
  consumen licencia). `license-candidates` absorbió el alcance completo:
  ya no filtra por `userType`, suma `displayName`/`email`, y el
  `candidato` ahora también considera el status de Identity (disabled/
  suspended/locked/deactivated), no sólo los días sin login. Ver el
  docstring de `app/reports/license_candidates.py`.
- **Roadmap:** nuevo — encaja en `docs/PLAN.md §2.1` ("reporting" como
  workflow MSP) y §8 ("Exportar"). No es una fase de escritura.
- **Fecha:** 2026-09-10

## Problema

Los clientes piden seguido reportes puntuales ("dame las cuentas sin uso",
"auditá los permisos de todas las safes", "candidatos a liberar licencia").
Hoy eso se resuelve con scripts sueltos de PowerShell en
el repositorio de scripts de PowerShell previo, que hay que
correr a mano, cliente por cliente, y devuelven un CSV/Excel. Cada script
reimplementa auth, paginación y reintentos.

PAM Web Console ya tiene todo lo que esos scripts necesitan: cliente CyberArk async con
retry/paginación, multi-tenant, RBAC, audit, export CSV, grillas. Falta una
**sección de Reportes** que los ofrezca desde la webapp: elegir tenant →
correr → ver en una grilla → descargar CSV/JSON, auditado.

## Alcance

**Entra (esta spec — la maquinaria):**
- Un **registro de reportes** (`app/reports/`): cada reporte declara nombre,
  etiqueta, descripción, parámetros y una función `run()` pura-ish (fetch →
  cómputo → filas + columnas).
- **Endpoint** `GET /tenants/{id}/reports/{name}` → `{columns, rows, meta}`,
  auditado `report.<name>` (es una lectura masiva de un tenant). Rol
  `viewer` sobre el tenant.
- **Descarga**: `GET /tenants/{id}/reports/{name}/export?format=csv|json`
  (reusa `app.core.csv_export`).
- **Catálogo**: `GET /reports` → lista de reportes disponibles con su schema
  de parámetros, para que la UI sea data-driven.
- **Frontend**: `pages/Reports.tsx` — elegir reporte + tenant + params →
  correr → `DataTable` (reuso) + "Exportar CSV".
- **Batch 1** de reportes concretos (los que sólo necesitan datos que el
  cliente ya sabe traer — ver §"Roadmap de reportes").

**Fuera (otras specs / futuro):**
- Reportes que necesitan **actividad por cuenta** (miles de llamadas
  `/Accounts/{id}/Activities`) — necesitan la infra de **jobs (ARQ)**.
  Batch 4.
- Reportes que necesitan la **API Redrock de Identity** (`POST
  /Redrock/query`) — hay que agregar un cliente de Identity. Batch 3.
- Reportes **cross-tenant** ("la misma cuenta de servicio duplicada en los 4
  clientes") — v1 es por tenant.
- Los scripts que **escriben** (`Account_Management`,
  `Safe_Password_Reset`, `Platform_PSM_Migration`) → Fase 3/4, no son
  reportes.
- `Account_Secret_Backup` (recupera secretos) — **fuera de alcance
  del proyecto** (los secretos se quedan en PVWA).
- Los scripts **locales de servidor** (`SystemInfo`, `LogCleanup`,
  `WebDriverUpdater`, `ReportCleanup`) — no hablan con la API, no van en PAM Web Console.
- Reportes **programados** (nocturnos, por mail) — Fase 5.

## Diseño técnico — la maquinaria

### `app/reports/`

```
app/reports/
  __init__.py       REGISTRY: dict[str, Report]
  base.py           Report (dataclass) + ReportResult + Column
  safe_duplicates.py
  safe_permissions.py
  account_inventory.py
  ...
```

- `Report`:
  ```python
  @dataclass
  class Report:
      name: str                 # "safe-duplicates"
      label: str                # "Safes con cuentas duplicadas"
      description: str
      params: list[Param]       # [{key, type, required, label, ...}]
      run: Callable[[CyberArkClient, dict], Awaitable[ReportResult]]
      heavy: bool = False       # marca los que necesitan jobs (no disponibles aún)
  ```
- `ReportResult`: `{columns: list[Column], rows: list[dict], meta: dict}`.
  `Column = {key, label, kind?}` (kind = "date" | "bool" | "text" para el
  render y el CSV).
- La lógica de cada reporte es **pura sobre los datos** (como
  `app/analysis.py`): la función `run` sólo orquesta `client.list_*()` /
  fan-out y llama a un `compute_*` puro que se puede testear sin red.

### `app/api/reports.py`

- `GET /reports` (rol `viewer`, cualquier operador) → `[{name, label,
  description, params, heavy}]` del `REGISTRY`.
- `GET /tenants/{tenant_id}/reports/{name}` → resuelve el `Report`, valida
  params, `async with tenant_client(...)`, `await report.run(client,
  params)`, audita `report.<name>` (con `detail = {params, row_count}`),
  responde `{columns, rows, meta}`. `heavy=True` → `501 not_implemented`
  ("este reporte necesita la infra de jobs").
- `GET /tenants/{tenant_id}/reports/{name}/export?format=csv|json` → lo
  mismo pero devuelve el archivo (columnas del `ReportResult` →
  `csv_export.to_csv`). Auditado `report.<name>` con `format`.
- Se incluye el router en `main.py`.

### Frontend

- `pages/Reports.tsx`:
  - `useQuery(["reports"])` → catálogo.
  - Selector de reporte (lista con label + descripción) + selector de tenant
    + form de params (según `params` del catálogo — text / select / número /
    fecha).
  - "Ejecutar" → `GET /tenants/{id}/reports/{name}?...` → `<DataTable>` con
    las `columns` del resultado (modo cliente: filtro, orden, virtualización
    — ya lo hace).
  - "Exportar CSV" → `apiBlob` del endpoint `/export` (reusa
    `<ExportCsvButton>`-style).
  - Reportes `heavy` aparecen en la lista pero deshabilitados con nota
    ("disponible cuando PAM Web Console tenga jobs").
- Nav: ítem "Reportes" (rol `viewer`).
- `lib/types.ts`: `ReportInfo`, `ReportResult`.

### Principios

- **Read-only.** Un reporte es una lectura masiva de un tenant → se audita
  `report.<name>` con la identidad real, igual que `<recurso>.list`.
- Aislamiento por tenant (cliente nuevo por request).
- La lógica de cómputo es pura y testeable; nada de reimplementar auth ni
  paginación (usa el cliente de PAM Web Console).
- Los reportes **no exponen secretos** (ninguno de los de alcance los toca).

## Roadmap de reportes (de los scripts existentes)

| Reporte PAM Web Console | Script origen | Datos | Peso | Batch |
|---|---|---|---|---|
| `safe-duplicates` — cuentas con address duplicado en una safe | `Safe_Duplicate_Report` | `list_accounts` (o por safe) | liviano (cómputo puro) | **1** |
| `safe-permissions` — permisos de cada miembro de cada safe | `Safe_Permissions_Audit` | `list_safes` + `safe_members(id)` por safe | medio (fan-out acotado, como el dashboard) | **1** |
| `account-inventory` — inventario detallado (gestión, últimas fechas) | `Account_Inventory_Export` | `list_accounts` (las fechas ya vienen) | liviano | **1** |
| `psm-balance` — reparto de cuentas por PSM | `PSM_Balance_Report` | `list_accounts` + `list_platforms` + `GET /API/PSM/Servers` (nuevo `list_psm_servers`) | medio | **2** |
| `license-candidates` — candidatos a liberar licencia Standard/External | `License_Candidates_Report` | Vault users + **Identity Redrock** | medio | **3** (necesita cliente Identity) |
| `identity-user-activity` — usuarios de Identity con último login / inactivos | `Identity_User_Activity_Report` | **Identity Redrock** | medio | **3** |
| `target-inactivity` — cuentas sin acceso (PSM Connect / Show / Copy) hace N días | `Target_Inactivity_Report` | `list_accounts` + **activities por cuenta** | pesado | **4** (jobs) |
| `account-activity` — sesiones PSM por cuenta, últimos 30 días | `Account_Activity_Report` | **activities por cuenta** | pesado | **4** (jobs) |

`license-candidates` e `identity-user-activity` se solapan con la "migración
del HealthCheck de ITR" (replican `analysis.py:analyze_users()` de ITR), pero
como reportes on-demand, que es más útil que un PDF mensual.

## Batch 1 — detalle (los 3 reportes de esta spec)

### `safe-duplicates`

- Params: `safe_name` (opcional; sin él, todas las safes).
- `run`: `client.list_accounts()` (o `accounts_page` con `filter=safeName eq`
  si hay `safe_name`) → `compute_safe_duplicates(accounts)` puro:
  agrupa por `(safeName, address.strip().lower())`, emite sólo los grupos con
  ≥ 2 cuentas.
- Columnas: `safeName`, `grupo` (nro), `cuentasEnGrupo`, `address`,
  `accountId`, `userName`, `platformId`, `createdTime` (date),
  `masAntiguaDelGrupo` (bool — sugerencia).

### `safe-permissions`

- Params: ninguno (o `safe_name` para acotar).
- `run`: `client.list_safes()` → `asyncio.gather` de
  `client.safe_members(safeUrlId)` (bounded por el semáforo) → filas planas.
- Columnas: `safeName`, `memberName`, `memberType`, y las flags de permiso
  como columnas bool: `useAccounts`, `retrieveAccounts`, `listAccounts`,
  `addAccounts`, `updateAccountProperties`, `initiateCPMAccountManagementOperations`,
  `deleteAccounts`, `manageSafe`, `manageSafeMembers`, `viewSafeMembers`,
  `accessWithoutConfirmation`, `requestsAuthorizationLevel1/2`,
  `membershipExpirationDate` (date).

### `account-inventory`

- Params: ninguno.
- `run`: `client.list_accounts()` → aplanar. Reusa las columnas del export
  de Cuentas de `export-csv-pdf` + `secretManagement.lastVerifiedTime`,
  `lastReconciledTime`, `manualManagementReason`, `remoteMachinesAccess`.
- Es "el CSV de Cuentas, más completo y como reporte navegable".
- **No es 1:1 con el reporte nativo "Privileged Accounts Inventory" del
  PVWA** (`InventoryReports.InventoryReportUI`, comparado el 2026-09-24
  contra un `.xls` real bajado por Alex). Al nativo le sobran, comparado
  con este reporte: `Group name`, `Last accessed date`/`Last accessed by`,
  `Last modified by` (acá sólo hay fecha, no quién), `Change failure`/
  `Verification failure` (booleanos) y `Failure reason` (motivo real de
  una falla de cambio/verificación — distinto de `manualManagementReason`,
  que es por qué la gestión automática está apagada). **Investigado y
  cerrado, no se puede traer vía REST:** Alex capturó con DevTools la
  llamada real que dispara ese reporte nativo — `POST .../PasswordVault/
  services/ReportsMgt.asmx/LaunchReport`, **sin header `Authorization`**,
  autenticado 100% por cookies de sesión del navegador
  (`ASP.NET_SessionId`/`idToken-*`/`XSRF-TOKEN-*`) — mismo servicio
  legacy `.asmx` sin Bearer token que ya se había concluido para "Move
  Account" (ver `account-change-platform.md`). El script original
  (`Account_Inventory_Export.ps1`) tampoco tiene estos campos: usa
  `GET /API/Accounts/{id}` igual que PAM Web Console, no el motor de reportes
  clásico. No se puede replicar el mecanismo NATIVO sin la sesión de
  navegador de un operador — descartado, igual que Move Account.
  **Pero la mayoría de los datos sí se pueden reconstruir por otro
  camino** (mismo día): `Last accessed`/`Change failure`/`Verification
  failure`/`Failure reason` se derivan del mismo fan-out de `Activities`
  por cuenta que usan `target-inactivity`/`account-activity` — ver el
  reporte nuevo `account-inventory-full` en
  `docs/specs/done/batch4-reports.md` "Extra el mismo día". Sólo
  `Group name` queda sin resolver.

## Tareas (maquinaria + Batch 1)

- [x] `app/reports/base.py` (`Report`, `ReportResult`, `Column`, `Param`) +
      `REGISTRY`.
- [x] `app/reports/{safe_duplicates,safe_permissions,account_inventory}.py`
      con sus `compute_*` puros.
- [x] `app/api/reports.py`: `GET /reports`, `GET /tenants/{id}/reports/{name}`
      (caché Redis TTL + `?refresh`), `.../export` (csv|json). Auditado
      `report.<name>`. Incluido en `main.py`. Config `PAM_REPORT_CACHE_TTL`.
- [x] Tests: `tests/unit/test_reports.py` (los 3 `compute_*` + catálogo +
      run + export + reporte desconocido). 77 tests verdes.
- [x] Frontend: `pages/Reports.tsx` (catálogo + selector de reporte/tenant +
      form de params + `DataTable` con columnas dinámicas + "Exportar CSV").
      Nav "Reportes" (rol `viewer`). Tipos `ReportInfo`/`ReportResult`.
- [x] `ruff` + `mypy` + `pytest` + `lint` + `tsc` + `build` verdes.
- [x] `CLAUDE.md` + `docs/PLAN.md`.
- [x] Probar contra Globex / Acme los 3 reportes + su export CSV. Mover
      a `docs/specs/done/`.

## Batch 2 — `psm-balance`

- Params: ninguno.
- `client.list_psm_servers()` (nuevo — `GET /API/PSM/Servers`, tolera 404/403
  del endpoint devolviendo `[]`) + `list_platforms` + `list_accounts`.
- `compute_psm_balance(platforms, accounts, psm_servers)` puro: mapea
  `platformId → PSM` leyendo `sessionManagement` /
  `privilegedSessionManagement` de cada plataforma, cuenta cuentas reales por
  plataforma, agrega por PSM. `meta` trae el total por PSM (cuentas, % y nº
  de plataformas).
- Columnas: `platformId`, `platformName`, `active` (bool), `psmServerId`,
  `psmServerName`, `accounts` (number).

### Tareas Batch 2

- [x] `app/cyberark/client.py`: `list_psm_servers()`.
- [x] `app/reports/psm_balance.py` (`compute_psm_balance` puro + `_run` +
      `report`), registrado en `REGISTRY`.
- [x] Tests: `test_compute_psm_balance`.
- [x] Probar en el server contra un tenant real (2026-09-10).

## Batch 3 — `license-candidates`, `identity-user-activity` (Identity Redrock)

- `client.redrock_query(script)` (nuevo — `POST {identity}/Redrock/query`
  con `{"Script": ...}`, sólo SELECT; usa `identity_tenant_url` del auth
  ISPSS; error claro si el tenant no tiene Identity).
- `app/reports/_identity.py`: `parse_redrock_epoch` (`/Date(ms)/` o ISO →
  epoch), `days_since`, `INACTIVE_STATUS`.
- `identity-user-activity`: `SELECT ... FROM User` (una sola query). Candidato
  = nunca inició sesión, o `≥ inactive_days` sin login, o status inactivo.
  Param `inactive_days` (default 90).
- `license-candidates`: cruza usuarios del Vault (`list_users`) con Identity
  (`SELECT ... FROM Users` paginado LIMIT/OFFSET) por username. Sólo
  `EPVUser` / `ExtUser` consumen licencia. Candidato = nunca inició sesión o
  `≥ inactive_days`. Param `inactive_days` (default 30). El enriquecimiento
  con Remote Access (Alero) de los `ExtUser` queda fuera de v1.

### Tareas Batch 3

- [x] `app/cyberark/client.py`: `redrock_query()`.
- [x] `app/reports/_identity.py` + `identity_user_activity.py` +
      `license_candidates.py`, registrados en `REGISTRY`.
- [x] Tests: `test_parse_redrock_epoch`, `test_compute_identity_user_activity`,
      `test_compute_license_candidates`. 81 tests verdes.
- [x] Probar en el server contra un tenant con Identity (2026-09-10 — todos
      los tenants Privilege Cloud tienen Identity).

## Batch 4 — `target-inactivity`, `account-activity` (BLOQUEADO en ARQ)

Necesitan una llamada `GET /API/Accounts/{id}/Activities` **por cuenta** —
así lo hacen los scripts PS (`Target_Inactivity_Report.ps1:166`,
`Account_Activity_Report.ps1:156`), con `ThrottleLimit 10-20` en
paralelo. No hay endpoint central que dé el último acceso de todas las
cuentas (los retiros sin PSM — "Show/Copy Password" — sólo viven en el
Activities de cada cuenta).

**Por qué no se puede síncrono:** todos los tenants del MSP tienen
**> 1000 cuentas (≈ 2000)**. Un barrido de ~2000 llamadas, aun a
concurrencia 8, son varios minutos → no entra en un request HTTP (timeout de
nginx/browser, tapa un worker) y no se puede meter esa carga en el proceso
de la API.

**Decisión (2026-09-10):** se hace **ARQ primero** (worker aparte, ya está
en el roadmap y lo necesitan también dashboard v3 y los reportes programados
de Fase 5) y recién después estos dos reportes, como `Report(heavy=True)`
que encolan un job. Hasta entonces el endpoint devuelve 501.

## Decisiones abiertas

1. **`GET` vs `POST` para correr un reporte.** Un reporte pesado con muchos
   params quedaría mejor como `POST {params}`, pero para Batch 1 (params
   simples, opcionales) `GET` con query params es más simple y cacheable. —
   Propongo `GET` para Batch 1; si un reporte futuro necesita un body, se
   agrega `POST` sin romper.
2. **Caché.** Los reportes medios (safe-permissions) tardan. ¿Cachear en
   Redis con TTL (como el dashboard) + `?refresh=1`? — Propongo **sí**, con
   TTL corto (5-10 min), reusando `app.core.cache`. El audit se registra
   igual en cada request (también en hit).
3. **`account-inventory` vs el export de Cuentas que ya existe.** ¿Se
   fusionan? — Propongo dejar los dos: el export de Cuentas es "rápido, las
   columnas de la grilla"; el reporte es "todo el detalle". El reporte puede
   incluso reemplazar al export a futuro.
4. **Nombre de la sección / permisos.** ¿"Reportes" para todos los `viewer`,
   o hay reportes sensibles (permisos de safe) que quieras limitar a
   `tenant_admin`? — Propongo `viewer` para todos por ahora; granular
   después si hace falta.
