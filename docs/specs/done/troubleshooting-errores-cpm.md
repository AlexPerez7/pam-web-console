# troubleshooting-errores-cpm — sección dedicada de errores de rotación

- **Estado:** hecha — probada contra Globex (2026-09-16). **Fix real
  2026-09-24:** `cpmFailures()` (`lib/cpmFailures.ts`) tomaba la
  actividad más reciente que dijera "fail" en el `Action`, sin exigir
  que fuera realmente una operación del CPM — una falla de SESIÓN PSM
  (`PSMSR280E`/`PSMSR605E`/etc., componentes que se cuelgan, timeouts,
  nada que ver con cambiar/verificar la contraseña) más reciente que la
  última falla real del CPM tapaba el motivo correcto en "Último error"
  y en el match contra la base de conocimiento. Encontrado armando
  `account-inventory-full` (mismo criterio replicado en Python,
  `app/reports/account_inventory.py`) — confirmado con datos reales de
  Globex que ~22% de las cuentas en error tenían este solapamiento.
  Fix: sólo cuenta como "falla del CPM" un `Action` que diga "fail" **y**
  clasifique como cambio/verificación/reconciliación (mismas palabras
  clave que `classify()` de `activityClassify.ts`). Ver
  `docs/specs/done/batch4-reports.md` "Extra el mismo día" para la
  investigación completa. Sin tests unit (el frontend no tiene runner de
  tests JS/TS todavía) — verificado con `lint`+`tsc`+`build`.
- **Roadmap:** `docs/PLAN.md §7` Fase 1 — extra a pedido del jefe de Alex
  (surgió de la demo interna del 2026-09-14, ver `docs/PLAN.md` "Demo
  interna a un colega")
- **Fecha:** 2026-09-14

## Problema

Hoy el Dashboard ya calcula cuántas cuentas están en error de CPM
(`analyze_cpm_errors`: cuántas siguen reintentando, cuántas el CPM ya dio
por perdidas, desglose por motivo) — pero sólo como **números agregados**.
No hay forma de ver **cuáles** cuentas son, ni de actuar sobre ellas sin
saltar al PVWA. El jefe de Alex pidió una sección dedicada de
"Troubleshooting" en el nav izquierdo para revisar y analizar estas
cuentas directamente en PAM Web Console.

Decisiones ya tomadas con Alex para acotar el alcance:

- **Sólo errores de rotación/verificación del CPM** por ahora. La otra
  idea que había quedado de la misma demo — una pestaña de
  Actividad/Audit por cuenta al estilo del audit nativo de CyberArk, pero
  del propio PAM Web Console — queda **fuera**, como un plan aparte a especificar
  después.
- **Navegación por tenant** (selector arriba, igual que Dashboard/
  Inventario/Reportes) — no un resumen cross-tenant tipo Búsqueda. Más
  consistente con el resto de PAM Web Console y menos trabajo.

## Alcance

**Entra:**
- Página nueva `/troubleshooting` (label "Troubleshooting" en el nav,
  agregado al array `NAV` — visible para cualquier `viewer+`, mismo techo
  que Dashboard/Reportes: es de sólo lectura).
- Selector de tenant + resumen (KPIs: total en error, reintentando, se
  rindió + desglose por motivo) + **tabla de las cuentas afectadas**
  (nombre, safe, plataforma, estado, motivo, última modificación).
- Click en una fila abre el mismo `AccountDetailPanel` que usa Inventario
  — el operador puede Verificar/Cambiar/Reconciliar ahí mismo, sin saltar
  de página. Esto es lo que distingue la sección de "un reporte más":
  ver el problema y actuar en el mismo lugar.
- Backend: endpoint nuevo, sin escritura ni feature-flag (mismo criterio
  que Dashboard/Inventario: leer siempre se puede, `viewer+`).

**Fuera:**
- Pestaña de Actividad/Audit (plan aparte, spec futura).
- Acciones en lote desde esta vista (ej. "reintentar todas") — se abre
  cuenta por cuenta con las acciones que ya existen.
- Resumen cross-tenant.
- Filtrar por rango de fechas del error / exportar CSV — se puede sumar
  después si hace falta, no lo pidieron todavía.

## Requisitos

1. **Ver el desglose.** Al elegir un tenant, se ven los mismos números que
   hoy sólo están en el Dashboard (total en error, reintentando, se
   rindió, motivos) pero en su propia sección.
2. **Ver el detalle por cuenta.** Debajo del resumen, una tabla lista cada
   cuenta en error: nombre, safe, plataforma, si el CPM sigue
   reintentando o ya se rindió, el motivo (si lo hay) y cuándo fue la
   última modificación conocida.
3. **Actuar desde ahí.** Click en una fila abre el detalle de esa cuenta
   (mismo panel que Inventario) con sus acciones habituales
   (Verificar/Cambiar/Reconciliar) ya disponibles según feature-flags y
   rol del operador — sin funcionalidad nueva de escritura.
4. **Sin cuentas en error.** Si el tenant no tiene ninguna, se muestra un
   estado vacío claro, no una tabla en blanco confusa.

## Diseño técnico

- `backend/app/analysis.py`: nueva función pura `failed_accounts_rows(accounts,
  failed_ids)` — arma la lista curada por cuenta (id, name, safeName,
  platformId, status, automaticManagementEnabled, manualManagementReason,
  reason_label vía `_CPM_REASON_LABELS`, lastModifiedTime). Vive al lado de
  `analyze_cpm_errors` porque comparte el mismo criterio de "qué es un
  error" (`failed_ids` de `analyze_accounts`) — una sola fuente de verdad,
  no reinventar la detección de fallas.
- `backend/app/api/troubleshooting.py` (router nuevo): `GET
  /tenants/{id}/troubleshooting/account-errors[?refresh=1]` — mismo patrón
  que `dashboards.py`: `client.list_accounts()` → `analyze_accounts` →
  `analyze_cpm_errors` + `failed_accounts_rows` → cachea en Redis
  (`PAM_TROUBLESHOOTING_CACHE_TTL`, sugerido 300s — el dato no cambia tan
  rápido como para necesitar el TTL corto de plataformas/safes) →
  `?refresh=1` fuerza recálculo. Audita `troubleshooting.account_errors.view`
  siempre (también en cache hit, igual que `dashboard.view`). RBAC
  `require_tenant_access(viewer)`.
- `frontend/src/pages/Troubleshooting.tsx` (nuevo): selector de tenant
  (mismo patrón que `Dashboard.tsx`) + `Kpi`/`Panel` locales (mismo estilo
  visual que Dashboard) + `BarList` para el desglose por motivo +
  `DataTable` en **modo cliente** (la lista de cuentas en error es un
  subconjunto chico del total, no necesita paginación server-side) con
  `onRowClick` que abre `AccountDetailPanel` (mismo wiring que
  `Inventory.tsx`: estado `sel` + `opened/onClose/object/tenant/accountId`).
- `frontend/src/App.tsx`: agregar `{ to: "/troubleshooting", label:
  "Troubleshooting" }` al array `NAV` + `<Route path="/troubleshooting"
  element={<Page><Troubleshooting /></Page>} />`.
- Sin cambios en `features.py`/`Tenants.tsx` — no hay escritura, no hay
  feature-flag que gatear.

## Extra — "Último error" por cuenta (2026-09-15, a pedido de Alex)

**Problema:** la tabla mostraba que una cuenta está en "gestión manual"
(el CPM agotó reintentos), pero no el motivo real de por qué falló — para
verlo había que abrir el detalle de la cuenta y buscar en la pestaña
Actividad. Alex: "para que esto pase tuvo que fallar por algo la rotación
y ese error sería bueno poder verlo".

**Diseño:** sin cambios de backend — reusa
`GET /tenants/{id}/accounts/{id}/activities` (ya existía, alimenta
`AccountDetailPanel`). `components/LastCpmErrorCell.tsx` (nuevo): columna
"Último error" en la tabla de Troubleshooting con un botón "Ver error"
por fila; **al click** (no de entrada) pide las Activities de esa cuenta
(mismo `queryKey` que `AccountDetailPanel` — comparte caché de
react-query si el operador después abre el detalle completo) y muestra el
`Reason` de la actividad fallida más reciente (`Action` conteniendo
"fail", mismo criterio que `classify()` de `ActivityFeed.tsx`). El botón
llama `e.stopPropagation()` para no disparar el click de la fila (que
abre `AccountDetailPanel`).

**Por qué al click y no de entrada:** traer las Activities de las N
cuentas en error de una sola pasada dispararía N llamadas a CyberArk sólo
por abrir la página — se prioriza la carga inicial rápida sobre ver todos
los errores sin ningún click.

## Extra — panel de detalle específico para Troubleshooting (2026-09-15)

**Problema:** click en una fila abría el mismo `AccountDetailPanel` que
Cuentas — pensado para gestión general (editar, revelar, vincular,
borrar), no para "¿qué pasó y qué hago ahora". Alex: "diseñar un nuevo
detalle específico para el troubleshooting", con la idea de tener "una
sección de troubleshooting perfecta".

**Diseño:** `components/TroubleshootingAccountPanel.tsx` (nuevo,
reemplaza `AccountDetailPanel` como panel por defecto de esta página):
- Header igual (heading, chips de plataforma/safe, `StatusPill`).
- **Alert destacado** con el motivo de la última falla (misma lógica que
  `LastCpmErrorCell`, pero acá se carga de entrada — un solo panel
  abierto a la vez, no hay problema de N llamadas).
- Acciones **acotadas** a lo que resuelve una falla de rotación:
  Verificar / Cambiar / Reconciliar. Sin Editar/Revelar/Vincular/Borrar —
  eso vive en `AccountDetailPanel`, a un botón "Ver cuenta completa" de
  distancia (`onOpenFull`, la página maneja el toggle con un estado
  `fullView`).
- En vez de la pestaña Actividad con todo mezclado (CyberArk + PAM Web Console +
  filtros), un **"Historial de fallas"**: sólo los eventos con `Action`
  conteniendo "fail", vía `AccountActivityFeed` (reusado tal cual).

**Refactor para reusar sin duplicar:** `StatusPill`/`Chip`/`Card` se
exportaron de `AccountDetailPanel.tsx`. `CPM_LABEL`/`isInProcess`/
`CpmAction` se movieron a `lib/cpmActions.ts` (estaban en
`AccountDetailPanel.tsx`, pero exportar una constante/función junto a
componentes rompe el fast-refresh de Vite — mismo motivo que
`components/QueryState.tsx` se separó de `lib/gridHelpers.tsx` en
`nav-spaces.md`). Mismo movimiento para `classify()` — salió de
`ActivityFeed.tsx` a `lib/activityClassify.ts`. `cpmFailures()` (ya
existía en `lib/cpmFailures.ts` para `LastCpmErrorCell`) se reusa tal
cual acá.

## Tareas

- [x] `analysis.py`: `failed_accounts_rows` + tests unit.
- [x] `api/troubleshooting.py`: endpoint + caché Redis + audit.
- [x] `main.py`: registrar el router nuevo.
- [x] `Troubleshooting.tsx` + nav + ruta en `App.tsx` (hoy vive en el
      espacio Operación, ver `docs/specs/done/nav-spaces.md`).
- [x] Tests unit backend (`tests/unit/test_troubleshooting.py`, 3 tests,
      verdes — filtrado/no-op/caché).
- [x] `pytest` verde (verificado 2026-09-15). Falta re-confirmar
      `ruff`/`mypy`/`lint`/`tsc`/`build` en un pase completo si se toca
      este código de nuevo.
- [x] `components/LastCpmErrorCell.tsx` — columna "Último error", carga
      lazy al click, reusa `/accounts/{id}/activities`. `lint`/`tsc`/
      `build` verdes (2026-09-15).
- [x] `components/TroubleshootingAccountPanel.tsx` — panel de detalle
      específico, con toggle a `AccountDetailPanel` ("Ver cuenta
      completa"). Refactor `StatusPill`/`Chip`/`Card` exportados de
      `AccountDetailPanel.tsx`, `CPM_LABEL`/`isInProcess`/`CpmAction` a
      `lib/cpmActions.ts`, `classify()` a `lib/activityClassify.ts`.
      `lint`/`tsc`/`build` verdes (2026-09-15).
- [x] Columnas de la tabla ajustadas (2026-09-15, a pedido de Alex): "Cuenta"
      → **"Nombre de cuenta"** (evita confundirla con la fila/cuenta
      entera) + columnas nuevas **Usuario**/**Address**
      (`failed_accounts_rows` en `analysis.py` suma `userName`/`address`
      al `dict` que ya arma). `pytest`/`ruff`/`mypy`/`lint`/`tsc`/`build`
      verdes.
- [x] Probado contra Globex (2026-09-16): 737 cuentas en error, KPIs
      y desglose de motivos correctos, filtros por columna funcionando,
      click en una fila abre `TroubleshootingAccountPanel` con el
      "Último error" real (`Reason` de CyberArk) + historial de fallas,
      y "Ver cuenta completa" abre el `AccountDetailPanel` genérico con
      su actividad real. Probado read-only (sin ejecutar Verificar/
      Cambiar/Reconciliar/Borrar).
