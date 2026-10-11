# rotation-campaigns — Campañas de rotación/remediación con seguimiento

- **Estado:** aprobada — confirmado con Alex (2026-09-15): sólo desde
  Troubleshooting, avance en vivo sin historial, sin feature-flag nueva.
- **Roadmap:** `docs/PLAN.md §7` Fase 4 — "Campañas de rotación /
  remediación con seguimiento y reporte"
- **Fecha:** 2026-09-15
- **Base:** `docs/specs/troubleshooting-errores-cpm.md` (fuente típica de
  cuentas para armar una campaña — reusa `analysis.analyze_accounts`
  `failed_ids` para calcular avance) y
  `docs/specs/account-bulk-onboard.md` (mismo criterio "PAM Web Console-nativo, sin
  cola de jobs, todo síncrono").

## Problema

Troubleshooting muestra un snapshot de cuentas en error, pero no hay
forma de agrupar un lote (ej. "las 40 cuentas de la migración de
SolarWinds") y hacerle seguimiento de remediación en el tiempo — cuántas
siguen fallando, cuántas ya se arreglaron, quién lo pidió y por qué. Hoy
esa tabla de seguimiento vive (si existe) en un Excel aparte.

## Alcance

**Entra:**
- **Crear una campaña**: desde Troubleshooting, tildar cuentas (mismo
  mecanismo de selección que ya tiene `AccountsTab` para bulk-edit) →
  "Crear campaña" → nombre + descripción + razón. Guarda un snapshot de
  qué cuentas entran (id/nombre/safe/plataforma) — **no** un vínculo vivo
  a un `savedFilter` de CyberArk.
- **Página "Campañas"** (nueva, tenant-scoped): lista de campañas del
  tenant con progreso (`X de Y resueltas`), click abre el detalle.
- **Detalle de campaña**: tabla de cuentas miembro con su estado
  **calculado en vivo** (¿sigue en `Failure` o no?, mismo criterio que
  `analyze_accounts`) — no un estado guardado que hay que sincronizar.
  Click en una cuenta abre `TroubleshootingAccountPanel` (reusado tal
  cual) para remediar ahí mismo.
- **Borrar una campaña** (housekeeping, no borra ninguna cuenta real).
- Datos **propios de PAM Web Console** (tabla nueva en Postgres) — primera vez que
  PAM Web Console guarda algo que no es ni audit ni backup ni config de tenant/
  operador.

**Fuera (de este corte):**
- Notificación de avance (Slack/mail) — Fase 3/4 ya lo tenía pendiente en
  general (`docs/PLAN.md §12` Pendientes #3), no se resuelve acá.
- Historial de "cuándo se resolvió cada cuenta" — el estado se calcula en
  vivo cada vez que se abre la campaña, no se persiste un timeline. Si
  hace falta después (para un reporte "se resolvió en 3 días"), es una
  vuelta futura con un snapshot periódico (necesitaría ARQ, mismo límite
  que "job abortable" de onboarding masivo).
- Cerrar/archivar una campaña formalmente — v1 no distingue
  activa/cerrada, sólo existe/borrada. Si con el uso real hace falta un
  estado explícito, se agrega.
- Crear una campaña desde Cuentas (no sólo desde Troubleshooting) — se
  puede sumar rápido después (mismo mecanismo de selección), no bloquea
  este corte.

## Requisitos

1. **Crear.** Con 5 cuentas tildadas en Troubleshooting, "Crear campaña"
   pide nombre + descripción (opcional) + razón (obligatoria, ≥ 8
   caracteres, igual criterio que el resto de PAM Web Console) → queda creada con
   las 5 cuentas como snapshot.
2. **Listar.** La página "Campañas" muestra todas las del tenant, más
   recientes primero, con `X de Y resueltas`.
3. **Ver avance.** Al abrir una campaña, cada cuenta miembro se resuelve
   contra el estado actual del tenant (una sola `list_accounts()`,
   cruzada por id — no N cuentas = N requests) — "resuelta" si ya no está
   en `Failure`, "pendiente" si sigue.
4. **Cuenta borrada en CyberArk.** Si una cuenta miembro ya no existe
   (borrada después de armar la campaña), se muestra como "no encontrada"
   en vez de romper el cálculo de avance del resto.
5. **Remediar desde ahí.** Click en una fila pendiente abre
   `TroubleshootingAccountPanel` con las acciones de siempre
   (Verificar/Cambiar/Reconciliar).
6. **Borrar campaña.** No toca CyberArk, sólo borra el registro de PAM Web Console.

## Diseño técnico

### Modelo (Postgres, nuevo)

- `app/models/campaign.py`: `Campaign` (id, tenant_id FK, name,
  description nullable, reason, created_by, created_at, updated_at) +
  `CampaignAccount` (id, campaign_id FK `ondelete=CASCADE`, account_id,
  account_name, safe_name, platform_id, created_at) — snapshot puro, sin
  FK a nada de CyberArk (no existe esa tabla). Migración Alembic `0007`.
  Mismo patrón que `AccountBackup`/`PlatformBackup`.

### Backend

- `app/domain/campaigns.py`: `campaign_progress(members: list[dict],
  accounts_by_id: dict) -> list[dict]` — puro, cruza los miembros contra
  el `dict` de cuentas vivas (armado una vez por request desde
  `list_accounts()`) y devuelve el estado de cada uno
  (`resolved`/`pending`/`not_found`), reusando el mismo criterio de
  "Failure" que `analysis.analyze_accounts`.
- `app/api/campaigns.py` (router nuevo, `tenant_id`-scoped):
  - `GET /tenants/{id}/campaigns` — lista + progreso agregado por
    campaña (una sola `list_accounts()` por tenant, reusada para todas
    las campañas de la respuesta). `viewer+`.
  - `POST /tenants/{id}/campaigns` — `{name, description?, reason,
    account_ids: list[str]}` — valida que las cuentas existan (contra la
    misma `list_accounts()` ya traída), guarda el snapshot. `operator+`,
    auditado `campaign.create`. **Sin feature-flag** — no toca CyberArk,
    es bookkeeping de PAM Web Console.
  - `GET /tenants/{id}/campaigns/{campaign_id}` — detalle + progreso por
    cuenta. `viewer+`.
  - `DELETE /tenants/{id}/campaigns/{campaign_id}` — `operator+`,
    auditado `campaign.delete`.

### Frontend

- `pages/Campaigns.tsx` (nuevo, ruta `/campaigns`, ítem nuevo en el
  espacio Operación) — `TenantScope` + lista con progreso
  (`DataTable` simple) + botón "Nueva campaña" (deshabilitado, se crea
  sólo desde Troubleshooting — evita duplicar el flujo de selección).
- `pages/Troubleshooting.tsx`: la tabla gana selección (`selectedIds`/
  `onSelectedIdsChange`, mismo patrón que `AccountsTab`) + botón "Crear
  campaña (N)" en el toolbar cuando hay algo tildado →
  `CampaignCreateDialog.tsx` (nuevo: nombre/descripción/razón, sin
  dry-run — no hay nada que previsualizar contra CyberArk).
- `pages/CampaignDetail.tsx` (o una vista dentro de `Campaigns.tsx`) —
  tabla de cuentas miembro con badge resuelta/pendiente/no encontrada,
  click abre `TroubleshootingAccountPanel` (mismo componente de hoy,
  reusado tal cual).

## Tareas

- [x] `app/models/campaign.py` (`Campaign`/`CampaignAccount`,
      `lazy="selectin"` para no romper con SQLAlchemy async) + migración
      `0007_campaigns.py`.
- [x] `app/domain/campaigns.py`: `campaign_progress` + `campaign_summary`
      + tests unit.
- [x] `app/api/campaigns.py`: los 4 endpoints (list/create/get/delete) —
      una sola `list_accounts()` por request, reusada entre todas las
      campañas de la respuesta.
- [x] Tests (`tests/unit/test_campaigns.py`, 11 — dominio + crear con
      cuentas faltantes, tope de todas faltantes → 422, listar con
      progreso, detalle con cuenta borrada después de crear la campaña,
      borrar, RBAC).
- [x] `pages/Campaigns.tsx` + ruta `/campaigns`, ítem "Campañas" en
      Operación (después de Troubleshooting). `CampaignDetailPanel.tsx`
      (avance en vivo, click en pendiente abre
      `TroubleshootingAccountPanel` reusado, con su propio toggle a
      `AccountDetailPanel`).
- [x] `Troubleshooting.tsx`: selección de filas (mismo patrón que
      `AccountsTab`) + botón "Crear campaña (N)" + `CampaignCreateDialog.tsx`
      (sin dry-run).
- [x] `ruff` + `ruff format` + `mypy` + `pytest` (backend, suite completa)
      + `lint` + `tsc` + `build` (frontend) verdes (2026-09-15).
- [x] `CLAUDE.md` + `docs/PLAN.md §7` (Fase 4).
- [x] Migración `0007` corrida en el server.
- [x] Probado contra Globex (2026-09-18): campaña armada desde
      Troubleshooting, remediación y avance en vivo confirmados. Spec
      cerrada.

## Decisiones abiertas

1. ~~¿Vale la pena que "Nueva campaña" también arranque desde Cuentas?~~
   **Confirmado: no, sólo Troubleshooting.**
2. ~~¿Alguna feature-flag para `campaign.create`/`campaign.delete`?~~
   **Confirmado: no, RBAC `operator+` alcanza.**
3. **Nombre de la tabla/ruta:** `campaigns` puede confundirse con
   "campañas de marketing" fuera de contexto — a criterio de Alex si
   prefiere otro nombre (`remediation-campaigns`, `rotation-batches`).
