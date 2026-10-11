# dashboard-v2

- **Estado:** hecha (probada en el server 2026-09-10)
- **Roadmap:** Fase 1, ítem "Dashboards en vivo … reusando `analyze_*` de ITR"
- **Fecha:** 2026-09-09

## Problema

El dashboard v1 (`GET /tenants/{id}/dashboard`) da KPIs y gráficos de
accounts/safes/platforms, pero:

1. No tiene el **compliance real** (el desglose PolicyFailures /
   DisableByUser / DisableByCPM / Locked que da el Vault Analysis Report), ni
   el desglose de **errores de CPM** por motivo.
2. No dice nada de la **salud de los componentes** (PVWA / PSM / CPM / AIM).
3. **Pega en vivo a CyberArk en cada carga** — para Globex son ~20-40 s de
   espera cada vez que alguien abre el dashboard.

## Alcance

**Entra:**
- Compliance real vía `savedFilter` (como ITR) + desglose de errores de CPM.
- Salud de componentes (`ComponentsMonitoringDetails`).
- **Caché server-side** del cálculo del dashboard (Redis, ya está en el
  stack), con TTL y opción de refrescar a demanda.

**Fuera (otra spec):**
- **Sesiones concurrentes / actividad PSM.** Requiere traer las activities
  de *cada* cuenta (una llamada por cuenta → miles para Globex); eso es
  trabajo de un job en background (ARQ, pendiente de Fase 1+), no de un
  request sincrónico. Se hace en su propia spec cuando exista esa infra.
- Licencias, Connector Management, SIA.

## Requisitos

### R1 — Compliance real (savedFilter)

En el cálculo del dashboard, además de accounts/safes/platforms, traer el
conteo de cuentas que matchean estos `savedFilter` de `GET /API/Accounts`
(en paralelo con el resto):

| savedFilter | categoría |
|---|---|
| `PolicyFailures` | PolicyFailures |
| `DisabledPasswordByUser` | DisableByUser |
| `DisabledPasswordByCPM` | DisableByCPM |
| `Locked` | Locked |

Con eso, `analysis.analyze_compliance_breakdown(total, counts)` (portada de
ITR) devuelve `{compliant, compliant_pct, categories: {...}}`.

**Escenario:**
Dado un tenant con 1000 cuentas y 40 en `PolicyFailures`,
Cuando se calcula el dashboard,
Entonces `kpis.compliance_pct == 96.0` y
`charts.compliance_breakdown` trae las 5 categorías con sus conteos.

Nota (heredada de ITR): los `savedFilter` `FailedChange/FailedVerify/
FailedReconcile` devuelven 0 en Privilege Cloud aunque haya cuentas en error
— **no usarlos**. El error real de CPM se saca de
`secretManagement.status == "Failure"` (R2).

### R2 — Desglose de errores de CPM

`analysis.analyze_cpm_errors(accounts, failed_ids)` (portada de ITR), sobre
los datos de accounts que ya se traen: cuántas cuentas en `Failure` el CPM
sigue reintentando (`automaticManagementEnabled=true`) vs. cuántas ya
desactivó, y el desglose por `manualManagementReason`.

**Escenario:**
Dado 12 cuentas en estado Failure, 8 con reintento activo y 4 desactivadas
por `(CPM)MaxRetries`,
Entonces `charts.cpm_errors` refleja 8 "en reintento" y 4 con el motivo
"Reintentos agotados (CPM)".

### R3 — Salud de componentes

Traer `GET /API/ComponentsMonitoringDetails/{id}` para `PVWA`,
`SessionManagement`, `CPM`, `AIM` (en paralelo; 404 = "no aplica", no error).
`analysis.analyze_components(raw)` devuelve por componente:
`{available, total_instances, connected, disconnected}`.

KPI: "Componentes con instancias caídas". Panel: una fila por componente con
conectados / total.

**Escenario:**
Dado que el CPM tiene 2 instancias y 1 desconectada,
Entonces `kpis.components_disconnected >= 1` y `charts.components` muestra
`CPM 1/2`.

### R4 — Caché server-side

- El resultado completo del dashboard se cachea en Redis, key
  `pam:dashboard:{tenant_id}`, TTL configurable
  (`PAM_DASHBOARD_CACHE_TTL`, default 900 s).
- `GET /tenants/{id}/dashboard` sirve del caché si hay; si no, calcula y
  guarda.
- `GET /tenants/{id}/dashboard?refresh=1` ignora el caché, recalcula y
  regraba.
- La respuesta incluye `generated_at` y `cached` (bool).
- **Se audita `dashboard.view` en cada request** (también en cache hit — el
  operador accedió a datos del tenant), con `detail` incluyendo `cached` y
  los conteos.
- Si Redis no está disponible, el endpoint sigue funcionando sin caché (log
  + `cached: false` siempre).

**Escenario:**
Dado que el dashboard de Globex se calculó hace 3 minutos,
Cuando otro operador lo abre,
Entonces responde en <1 s con `cached: true` y `generated_at` de hace 3 min,
y queda un `dashboard.view` en el audit igual.

### R5 — Frontend

- Panel "Compliance" con el desglose (BarList) + el % como KPI.
- Panel "Errores de CPM" (BarList: en reintento / por motivo).
- Panel "Componentes" (fila por componente: conectados / total, color si hay
  caídas).
- Encabezado del dashboard: "Actualizado hace X · [Actualizar]". El botón
  llama con `?refresh=1`.

## Diseño técnico

**Backend**
- `app/cyberark/client.py`:
  - `saved_filter_accounts(saved_filter)` → `get_all("/API/Accounts",
    params={"savedFilter": ...})`.
  - `component_details(component_id)` → `GET
    /API/ComponentsMonitoringDetails/{id}`; 404 → `None`.
  - `all_components()` → dict `{id: [...] | None}` para PVWA / SessionManagement
    / CPM / AIM.
- `app/analysis.py`: portar `analyze_compliance_breakdown`,
  `analyze_cpm_errors`, `analyze_components` (versión recortada: sin el
  detalle de instancias).
  - `analyze_accounts` ya expone lo necesario; agregar `failed_ids` (set de
    ids con status Failure) a su salida para `analyze_cpm_errors`.
- `app/core/cache.py` (nuevo): helper mínimo sobre `redis.asyncio`
  (`get_json` / `set_json` con TTL), tolerante a Redis caído.
- `app/config.py`: `dashboard_cache_ttl: int = 900`.
- `app/api/dashboards.py`:
  - `?refresh: bool = False`.
  - cache lookup → si hit y no refresh, audita y responde.
  - miss: `asyncio.gather` de accounts, safes, platforms, 4 savedFilters,
    4 componentes (bounded por el semáforo del cliente); calcula; guarda en
    cache; audita; responde.

**Frontend**
- `pages/Dashboard.tsx`: 3 paneles nuevos + control de "Actualizar".
- `lib/types.ts`: extender `DashboardResponse` (`kpis.compliance_pct`,
  `kpis.components_disconnected`, `charts.compliance_breakdown`,
  `charts.cpm_errors`, `charts.components`, `cached`).
- Reutiliza `BarList`.

**Principios**
- Read-only. Aislamiento por tenant (cliente nuevo por request). Auditado
  siempre, también en cache hit.
- El caché guarda **métricas agregadas**, no objetos de cuentas ni secretos.
  TTL corto. Key namespaced por tenant.

## Tareas

- [x] Backend: `saved_filter_accounts` + `component_details` en `client.py`.
- [x] Backend: `compliance_breakdown` / `analyze_cpm_errors` /
      `analyze_components` en `app/analysis.py`; `failed_ids` en
      `analyze_accounts`.
- [x] Backend: `app/core/cache.py` (Redis, tolerante a caído).
- [x] Backend: `dashboards.py` con caché + `?refresh` + los cálculos nuevos;
      compliance/componentes degradan a parcial si fallan (`compliance_partial`).
- [x] Backend: `dashboard_cache_ttl` en config + `.env.example`; `py_compile`.
- [x] Frontend: paneles Compliance / CPM / Componentes + botón Actualizar +
      indicador "del caché · actualizado hace X".
- [x] Frontend: tipos.
- [x] `CLAUDE.md` + `docs/PLAN.md`.
- [x] Probar en el server (Globex, Acme), mover a `docs/specs/done/`.
      (2026-09-10)

## Notas de implementación

- Las 3 lecturas núcleo (accounts/safes/platforms) que fallan abortan el
  dashboard; los 4 savedFilters y los 4 componentes degradan a vacío y la
  respuesta trae `compliance_partial: true`.
- El caché guarda el dict completo de la respuesta; en un hit se marca
  `cached: true` y se re-audita igual.
- Sesiones concurrentes quedó fuera (necesita jobs) — no se tocó.
