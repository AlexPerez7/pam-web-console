# batch4-reports — `target-inactivity`, `account-activity` y `account-inventory-full` como jobs ARQ

- **Estado:** `account-inventory-full` **probado y confirmado contra
  Globex** (2026-09-24, encontró y corrigió un bug real de datos, ver
  "Bug real encontrado" más abajo). `target-inactivity`/
  `account-activity` implementados pero aún sin confirmar contra un
  tenant real puntualmente.
- **Roadmap:** `docs/PLAN.md §7` "Reportes", Batch 4
- **Fecha:** 2026-09-24, ampliada el mismo día con `account-inventory-full`

## Problema

Batch 1-3 de Reportes (`docs/specs/done/reportes.md`) portaron los scripts
de `PowerShell-Scripts` que sólo necesitan `list_accounts`/
`list_safes`/`safe_members` o Redrock de Identity — lecturas baratas,
corren síncronas en un request HTTP. Quedaban afuera dos reportes que
necesitan un `GET /API/Accounts/{id}/Activities` **por cuenta**:

- `target-inactivity` (`Target_Inactivity_Report.ps1`): cuentas sin
  ningún acceso (PSM Connect, o retiro manual de credencial vía Show/Copy
  Password) hace N días.
- `account-activity` (`Account_Activity_Report.ps1`): cantidad de
  conexiones PSM por cuenta en los últimos 30 días.

Con tenants de ~2000 cuentas, ese barrido tarda varios minutos aun con
concurrencia — no entra en un request HTTP (timeout de nginx/browser).
Bloqueado desde el 2026-09-10 a que existiera ARQ (que sólo se instaló el
2026-09-22/23, ver `docs/specs/done/jobs-programados.md`).

## Diseño

**Report.run() se reusa tal cual, sólo cambia quién lo llama.** No hace
falta una abstracción nueva: los `compute_*`/`_run` de estos dos reportes
tienen la misma forma que cualquier otro (`app/reports/*.py`), sólo que
`_run` hace un fan-out de Activities antes de armar las filas (ver
`app/reports/_activities.py`, compartido por los dos — mismo patrón que
`app.api.itr._all_account_activities`, no reusado directamente para no
tocar un módulo ya cerrado/probado).

- **Job genérico, no uno por reporte.** `app.jobs.reports.run_heavy_report
  (ctx, tenant_id, name, params, operator_username, operator_role)` busca
  el reporte en `REGISTRY` y llama a `report.run(client, params)` — sirve
  para `target-inactivity`/`account-activity` hoy y para cualquier reporte
  `heavy=True` que se agregue después, sin tocar el worker de nuevo.
- **Atribuido al operador real, no "system".** A diferencia del cron de
  `sync_all_tenants_accounts` (nadie lo pide, corre solo de noche), este
  job lo dispara un operador real desde la UI — se le pasan
  `operator_username`/`operator_role` al encolar (el job corre en el
  worker, sin el `Depends(current_operator)` de un request HTTP) para que
  el audit quede atribuido correctamente (principio no negociable del
  proyecto).
- **Resultado servido vía la MISMA caché que los reportes síncronos.** El
  job, al terminar, escribe su resultado en `app.api.reports._cache_key
  (tenant_id, name, params)` (Redis, TTL `report_cache_ttl`, 600s) — así
  `GET /reports/{name}` (para ver el resultado) y `.../export` (CSV/JSON)
  funcionan sin cambios una vez que el job cacheó algo, sin necesitar un
  código de lectura aparte. Mientras no haya nada cacheado, `GET
  /reports/{name}` sigue devolviendo `501` para un reporte `heavy` (pedía
  usar `POST .../run-job` en su lugar).
- **Pool ARQ propio para encolar desde la API** (`app.core.arq.
  get_arq_pool`, lazy singleton igual que `app.core.cache`) — distinto del
  pool interno que arq arma solo para el propio worker.
- **Polling desde el frontend.** `POST .../run-job` devuelve `{job_id}`;
  `GET .../jobs/{job_id}` devuelve `{status: "queued"|"in_progress"}` (sin
  el resultado, para no mandar un body pesado en cada poll) o, si
  `status: "complete"`, el mismo shape que un reporte normal
  (`columns`/`rows`/`meta`). El catálogo (`GET /reports`) ya exponía
  `heavy: bool`; `Reports.tsx` ahora sí deja elegir un reporte `heavy`
  (antes estaba deshabilitado en el picker) y, al ejecutar, pollea cada 3s
  hasta que `status === "complete"`.
- **Timeout de job largo.** El default de arq (`job_timeout=300s`) no
  alcanza con ~2000 cuentas a concurrencia 8 — `func(run_heavy_report,
  timeout=900)` en `WorkerSettings.functions`, con margen bajo el
  `job_timeout` que ya usa `recording_transcode` (540s) para otra cosa
  pesada del proyecto.

## Fidelidad con los scripts originales

- `_CONNECT_RE = r"PSM Connect|^Connect$"` / `_RETRIEVAL_RE = r"Show
  Password|Copy Password"` — mismos patrones que
  `$ConnectActionPattern`/`$RetrievalActionPattern` del `.ps1`.
- Cuentas nunca accedidas (o con error al consultar Activities) se tratan
  como "las más críticas" y ordenan primero — mismo criterio que
  `_DaysSort = [int64]::MaxValue` del script.
- `account-activity` cuenta sólo `"PSM Connect"` (no `Connect` genérico)
  dentro de los últimos 30 días — igual que el script, que no incluye
  retiros manuales de credencial en este reporte (a diferencia de
  `target-inactivity`, que sí los considera "acceso").
- **Cambio deliberado respecto al script:** una cuenta sin Activities
  accesible (permiso faltante, error transitorio) no se excluye del
  reporte — entra igual como "sin actividad registrada"/0 sesiones, con
  el conteo de errores expuesto en `meta.cuentas_con_error` (el script
  sólo lo loguea a un archivo de log que nadie ve desde PAM Web Console).
- **Orden de `account-activity`:** el script original exporta en el orden
  en que llegan las respuestas (no determinístico); acá se ordena por
  sesiones descendente — mejora menor, no rompe nada del script.

## Extra el mismo día — `account-inventory-full`

Alex comparó `account-inventory` (Batch 1, liviano) contra el reporte
nativo "Privileged Accounts Inventory" del PVWA (`InventoryReports.
InventoryReportUI`, un `.xls` real bajado del portal) y notó que le
faltaban columnas reales: `Group name`, `Last accessed date`/`Last
accessed by`, `Last modified by`, `Change failure`/`Verification
failure` (booleanos) y `Failure reason`.

**Investigado con DevTools (Alex capturó la request real del PVWA al
generar ese reporte):** `POST .../PasswordVault/services/ReportsMgt.asmx/
LaunchReport` — sin header `Authorization`, autenticado 100% por cookies
de sesión del navegador. Mismo servicio legacy `.asmx` sin Bearer token
que ya se había concluido para "Move Account" (`account-change-
platform.md`) — PAM Web Console no puede llamarlo. El script original
(`Account_Inventory_Export.ps1`) tampoco tiene estos campos, usa
`GET /API/Accounts/{id}` igual que PAM Web Console.

**Pero no todo estaba perdido:** `Last accessed`/`Change failure`/
`Verification failure`/`Failure reason` SÍ son derivables vía REST — con
el mismo fan-out de `Activities` por cuenta que ya usan `target-
inactivity`/`account-activity`. La app ya resuelve exactamente esto en
otro lado: `frontend/src/lib/activityClassify.ts` (`classify()`) y
`frontend/src/lib/cpmFailures.ts` (`cpmFailures()`, usado por
`LastCpmErrorCell.tsx` en Troubleshooting, ya probado contra 737 cuentas
reales en error de Globex) ya distinguen "verif"/"reconcil"/"change" +
si el `Action` contiene "fail", y usan `Activity.Reason` como motivo
legible. `account_inventory.py` replica ese mismo criterio en Python
(`_classify_failure_kind`, intencionalmente idéntico a `classify()` del
frontend), con `Group name` como única columna sin resolver (no parece
salir de `Activities`, se descarta por ahora).

Nuevo reporte `account-inventory-full` (`heavy=True`, categoría
"Cuentas"), separado del `account-inventory` liviano de siempre (que no
se toca, sigue rápido para quien sólo quiere el inventario básico) —
reusa `compute_account_inventory` y le suma `lastAccess`/`lastAccessBy`/
`changeFailure`/`verificationFailure`/`failureReason` por cuenta.

**Bug real encontrado en la primera corrida contra Globex
(2026-09-24, mismo día):** de 1892 cuentas, 429 tenían `failureReason`
poblado pero `changeFailure`/`verificationFailure` ambos en `false` —
inconsistente. Causa: el primer criterio tomaba "la actividad más
reciente cuyo `Action` contiene 'fail'", sin exigir que fuera
clasificable como cambio/verificación/reconciliación — Globex tiene
muchas actividades de **fallas de sesión PSM** (`PSMSR280E`/`PSMSR605E`/
etc., componentes que se cuelgan, timeouts, nada que ver con el CPM) que
también dicen "fail" en el `Action`. Una falla de sesión PSM más
reciente que la última falla REAL del CPM tapaba el motivo correcto.
Fix: en vez de tomar la primera con "fail", sigue buscando hacia atrás
hasta encontrar una que **sí** clasifique como change/verify/reconcile;
si no hay ninguna, `failureReason` queda vacío (antes quedaba con el
motivo de la sesión PSM, que no tenía sentido en este reporte). Test
nuevo que reproduce el caso exacto. **Mismo fix aplicado también a
`cpmFailures()` del frontend** (`lib/cpmFailures.ts`, Troubleshooting —
compartía el mismo criterio original sin filtrar por tipo, código ya en
producción que nadie había notado que podía mostrar el motivo
equivocado; a pedido de Alex, corregido igual).

**Re-corrido contra Globex (2026-09-24, mismo día) confirma el fix:**
de las 1892 cuentas, las "misteriosas" (con `failureReason` pero ningún
booleano en `true`) bajaron de 429 a **15** — y esas 15 son exactamente
el caso esperado: fallas de **reconciliación**
(`CACPM406E Reconciling Master/Password...`), que no tienen un booleano
dedicado en este reporte (igual que el nativo del PVWA, que tampoco
distingue reconciliación de cambio/verificación). Sin más pendientes
sobre este dato.

## Tareas

- [x] `app/reports/_activities.py`: `all_account_activities` compartida.
- [x] `app/reports/target_inactivity.py` + `account_activity.py`:
      `compute_*` puros + `_run` + `Report(heavy=True)`.
- [x] `app/reports/account_inventory.py`: `compute_account_inventory_full`
      + `report_full` (`heavy=True`) — reusa `compute_account_inventory`,
      suma último acceso y falla de cambio/verificación con motivo real.
- [x] `app/reports/__init__.py`: registrados en `REGISTRY`.
- [x] `app/core/arq.py`: pool ARQ para encolar desde la API.
- [x] `app/jobs/reports.py`: `run_heavy_report` (genérico, cualquier
      reporte `heavy`).
- [x] `app/jobs/worker.py`: registrado en `functions` con `timeout=900`.
- [x] `app/api/reports.py`: `_run_report` sirve desde caché aunque sea
      `heavy` (sólo 501 si no hay nada cacheado); `POST .../run-job` +
      `GET .../jobs/{job_id}`.
- [x] Frontend: tipos `ReportJobQueued`/`ReportJobStatus`;
      `pages/Reports.tsx` deja elegir reportes `heavy`, encola +
      pollea cada 3s, reusa el mismo render de tabla/export una vez
      completo.
- [x] Tests unit: `compute_target_inactivity` (activo reciente excluido,
      nunca accedida ordena primero, tipos de acceso, error en Activities
      no rompe), `compute_account_activity` (ventana de 30 días, orden),
      `compute_account_inventory_full` (último acceso/por quién, la
      falla más reciente gana sobre una más vieja de otro tipo, sin
      Activities no rompe), endpoint (`run-job` 422 si no es heavy / 404
      si no existe, `GET` 501 sin caché / 200 con caché, `jobs/{id}` en
      progreso / no encontrado / completo / fallido) — 24 tests en
      `test_reports.py`.
- [x] `ruff` + `format` + `mypy` + `pytest` + `pip-audit` (backend) y
      `lint` + `tsc` + `build` (frontend) verdes.
- [ ] Probar contra un tenant real — confirmar que el worker levanta el
      job nuevo (`docker compose restart worker` o rebuild), que el
      polling en la UI se ve bien con miles de cuentas, y medir cuánto
      tarda realmente (para ajustar `timeout=900` si hiciera falta).
