# fase-2-escritura — spec macro

- **Estado:** hecha — maquinaria + `account.update` probados contra el
  tenant `lab` en el server 2026-09-10 (editar name/address/userName
  end-to-end: toggle de la feature → "Editar metadata" → dry-run/diff →
  confirmar con razón → reflejado en la grilla y en el audit).
  Las demás operaciones de Fase 2 son specs nuevas sobre esta base.
  Falta (menor): editor de `platformAccountProperties` en la UI (el backend
  ya lo soporta); habilitar `account.update` para un cliente real cuando se
  decida (runbook).
- **Roadmap:** `docs/PLAN.md §7` Fase 2 — "Escritura de bajo riesgo
  (reversible, sin aprobación)"
- **Fecha:** 2026-09-10

## Problema

Hasta acá PAM Web Console es 100% read-only. Fase 2 introduce **la primera escritura
contra CyberArk**. Lo peor que puede pasar hoy es un reporte incompleto; a
partir de Fase 2, un bug puede **modificar la configuración de una cuenta
privilegiada de un cliente en producción**.

Cada operación de escritura (editar metadata, change/verify, crear cuenta,
agregar miembro a safe, activar usuario…) comparte la **misma maquinaria**:
gating por tenant, `dry-run` + diff, razón obligatoria, auditoría con
before/after, RBAC de escritura, cliente CyberArk con métodos de escritura,
límites. Esta spec define esa maquinaria **una vez**; cada operación concreta
va en su propia spec y se apoya en ella.

## Alcance

**Entra (esta spec):**
- El **contrato de escritura**: cómo se ve un endpoint de escritura de Fase 2
  (dry-run, razón, auditoría, gating) — de forma que todas las operaciones
  siguientes lo copien.
- **Feature-flags por tenant** (`Tenant.enabled_features`): estructura,
  enforcement (`require_feature`), y cómo se gestionan.
- **RBAC de escritura**: rol `operator` sobre el tenant para writes de bajo
  riesgo.
- **Cliente CyberArk con escritura**: `post` / `patch` / `delete` + política
  de reintentos segura para no-idempotentes + mapeo de errores.
- **Auditoría de escritura**: `before` / `after` / `patch` / `reason` /
  `dry_run` en el `detail`; se audita también el `dry-run`.
- **Límites**: rate-limit de escritura por `(operador, tenant)` + "circuit
  breaker" (hook para las masivas de Fase 4).
- **1ª operación de referencia**: `PATCH .../accounts/{id}` — editar metadata
  de cuenta. Se implementa como prueba de la maquinaria; el detalle fino va
  en `docs/specs/account-editar-metadata.md`.
- Frontend: el **flujo genérico de escritura** — botón "Editar" gateado,
  formulario, "Previsualizar cambios" (dry-run) → diff → "Confirmar" con
  razón. Componente `<DryRunDialog>` / hook `useWriteOp` reutilizable.

**Fuera (otras specs / Fase 3+):**
- Las demás operaciones de Fase 2 (change/verify/reconcile, crear cuenta con
  plantilla, agregar miembro a safe, activar usuario suspendido, crear safe)
  — cada una su spec, en el orden de §"Roadmap de operaciones".
- **Workflow de aprobación de un segundo operador** — Fase 3.
- **Operaciones destructivas** (borrar cuenta/safe, quitar miembros, editar
  plataformas) y **snapshots de reversión completos** — Fase 3.
- **Onboarding masivo / bulk / CSV** — Fase 4 (el circuit breaker se diseña
  acá pero no se ejercita).
- Plantillas de configuración por cliente — spec propia dentro de Fase 2
  (la usa `account.create`).
- Migración a CCP / OpenBao del secret store — independiente.

## Principios (no negociables — de `CLAUDE.md`)

1. **Read-only por defecto.** Ningún endpoint de escritura ejecuta sin que
   `Tenant.enabled_features[<feature>]` sea `true` **y** el operador tenga
   rol `operator+` efectivo sobre el tenant.
2. **`dry-run` + diff primero.** Toda escritura acepta previsualización:
   arma el plan de cambios y **no ejecuta**. Ejecutar exige `reason`.
3. **Todo se audita**, con `before` → `after`, la razón, y `dry_run` sí/no.
   El `dry-run` también deja rastro (alguien miró el objeto con intención de
   cambiarlo).
4. **Reversibilidad.** Fase 2 sólo cubre cambios **reversibles editando de
   nuevo** (metadata, flags on-demand). El `before` del audit alcanza para
   revertir a mano. Sin snapshot store (eso es Fase 3).
5. **Aislamiento por tenant.** Cliente CyberArk nuevo por request; el
   service account del tenant sólo tiene los permisos de las features
   habilitadas para *ese* tenant.
6. **Lab obligatorio.** Una feature no se habilita para un cliente sin
   haberse probado contra el Privilege Cloud del equipo PAM (tenant
   `lab`). Procedimiento en §"Habilitar una feature".

## Diseño técnico — la maquinaria

### 1. Feature-flags por tenant

`Tenant.enabled_features` (ya existe, `JSON`, default `{}`). Estructura: un
dict plano `{"<feature>": true}`. Nombres de feature = `<recurso>.<verbo>`,
los mismos que las acciones del audit:

```json
{ "account.update": true, "account.change": true, "safe.member.add": false }
```

- Ausente o `false` → la feature está apagada para ese tenant.
- `app/core/features.py`: `feature_enabled(tenant, name) -> bool`.
- `app/core/deps.py`: `require_feature(name)` → dependency que resuelve el
  `Tenant` del path y devuelve `403 feature_disabled` si no está.
  Se compone con `require_tenant_access(OperatorRole.operator)`.

**Gestión (v1, simple):**
- `GET /tenants/{id}/features` (rol `tenant_admin` efectivo) → el dict.
- `PUT /tenants/{id}/features` (rol **`platform_admin` global**) → reemplaza
  el dict. Auditado `tenant.features.update` con before/after.
- `seed/tenants.json` puede traer `enabled_features` para el bootstrap.
- **UI**: una tarjeta "Features de escritura" en una página de Tenants
  (nueva, mínima) o dentro de `Operators.tsx`. Puede ir después de la 1ª
  operación; el endpoint es lo que bloquea.

### 2. RBAC de escritura

- Writes de **bajo riesgo** (Fase 2): `require_tenant_access(OperatorRole.
  operator)` sobre el tenant del path.
- `viewer` no puede escribir. `approver` / `tenant_admin` heredan de
  `operator`.
- El `PUT /tenants/{id}/features` es `platform_admin` global (gobierna qué se
  puede tocar, no opera).

### 3. Contrato de un endpoint de escritura

Forma canónica que **todas** las operaciones de Fase 2 copian:

```
POST   /tenants/{tenant_id}/accounts/{account_id}:update      (ejemplo)
Body:  { "changes": { ...campos... }, "dry_run": true|false, "reason": "..." }
```

- `dry_run: true` (default si se omite): valida, arma el diff, **no
  ejecuta**. `reason` opcional. Responde
  `{ "dry_run": true, "plan": {...}, "diff": [...], "warnings": [...] }`.
- `dry_run: false`: exige `reason` no vacía (≥ 8 chars). Ejecuta, responde
  `{ "dry_run": false, "result": "ok", "before": {...}, "after": {...},
  "activity_ids": [...] }`.
- Errores de CyberArk → HTTP tipado (mismo mapeo que las lecturas) +
  `audit result="error"`.

Helper `app/core/writeop.py`:
- `class WriteOp` / función `run_write(...)` que envuelve: check de límites →
  fetch del estado actual → cálculo del diff → (dry-run: return) →
  ejecución → re-fetch → audit → return. Cada operación le pasa 3 callables:
  `fetch_current`, `build_change (current, changes) -> plan`, `apply
  (client, plan)`.

### 4. Cliente CyberArk con escritura

`app/cyberark/client.py`:
- `_request` ya acepta `method` y `json`. **Nuevo:** parámetro
  `retry_safe: bool` — sólo reintenta ante 429/5xx si es `True`. Para GET =
  `True` (default). Para **POST (crear)** = `False` (un timeout podría haber
  creado el objeto; reintentar duplica). PATCH/DELETE idempotentes = `True`.
  El re-auth ante 401 (una vez) se mantiene siempre.
- `post(path, json)` / `patch(path, json)` / `delete(path)` wrappers.
- `update_account(account_id, patch: list[dict]) -> dict` → `PATCH
  /API/Accounts/{id}` con cuerpo **JSON Patch (RFC 6902)**; devuelve la
  cuenta actualizada.
- Mapeo de errores de escritura: `400` → `CyberArkError` con el texto de
  validación de CyberArk (se muestra tal cual al operador); `409` →
  `Conflict`; `403` → `Forbidden` ("el service account no tiene el permiso
  X" — típico si la feature se habilitó en PAM Web Console pero no se ampliaron los
  permisos en CyberArk).

### 5. Diff / plan

- Para metadata de cuenta: `build_change` compara el objeto actual con los
  `changes` y produce una **lista de operaciones JSON Patch**
  (`{"op": "replace"|"add"|"remove", "path": "/name", "value": ...}`), más un
  `diff` legible `[{ "field": "name", "from": "...", "to": "..." }]` para la
  UI, más `warnings` (ej. "cambiar `address` no re-descubre la cuenta").
- Campos editables en v1 (`account.update`): `name`, `address`, `userName`,
  y `platformAccountProperties` (props personalizadas, merge por clave).
  **No** `platformId` (cambia la gestión), **no** los toggles de
  `secretManagement` (eso es `account.change`).

### 6. Auditoría de escritura

`audit.record(...)` para cada write:
- `action` = la feature (`account.update`).
- `target` = el id del objeto.
- `result` = `ok` | `error` | `denied`.
- `dry_run` = el flag (columna ya existe).
- `detail` = `{ "reason": "...", "diff": [...], "patch": [...],
  "before": {campos que cambian}, "after": {campos que cambian},
  "cyberark_activity_ids": [...] }`.
  **Sólo los campos que cambian** — no volcar el objeto entero, y **nunca**
  secretos.
- El `request_id` ya se propaga (middleware de logging).

### 7. Límites

- **Rate-limit de escritura**: contador en Redis por
  `(operador, tenant)` — `pam:write:<user>:<tenant>`, máx
  `PAM_WRITE_MAX_PER_WINDOW` (def 30) en `PAM_WRITE_WINDOW_MINUTES`
  (def 5). Superado → `429`. Reusa el patrón de `cache.incr_with_ttl` del
  lockout de login. **Los `dry-run` no cuentan.**
  - Redis caído: se **loguea** y se **permite** (consistente con el resto);
    la respuesta trae `rate_limit_checked: false`.
- **Circuit breaker** (`app/core/writeop.py`): si un `plan` afectaría más de
  `PAM_WRITE_OBJECT_CAP` (def 25) objetos, `run_write` corta con
  `409 too_many_objects` y exige otra vía (Fase 4: job con confirmación
  reforzada). Para las operaciones de un objeto de Fase 2 es un no-op.

### 8. Habilitar una feature para un cliente (procedimiento)

1. Ampliar el permiso del **service account** en CyberArk para ese tenant
   (ej. `Update Account Properties` en los safes objetivo). Documentado en
   `docs/runbooks/`.
2. Probar la operación contra el tenant **`lab`** (lab) con
   `dry_run=false` real.
3. `PUT /tenants/{id}/features` con la feature en `true` (platform_admin).
4. Anotar en las notas del tenant qué features están activas y desde cuándo.

Sin el paso 1, la operación devuelve `403 Forbidden` de CyberArk aunque la
feature esté `true` en PAM Web Console — y queda auditado como `error`.

## Primera operación (referencia): editar metadata de cuenta

`POST /tenants/{tenant_id}/accounts/{account_id}:update`

- Feature: `account.update`. RBAC: `operator+` sobre el tenant.
- Body: `{ "changes": { "name"?, "address"?, "userName"?,
  "platformAccountProperties"? }, "dry_run", "reason" }`.
- dry-run → `{ diff, patch, warnings }`. Ejecuta → `PATCH /API/Accounts/{id}`
  con el JSON Patch, re-fetch, audit `account.update`.
- **Reversible**: volver a editar con los valores del `before` del audit.

**Escenario (dry-run):**
Dado la cuenta `svc-sql` con `address = old.db.local`,
Cuando el operador pide `changes.address = new.db.local`, `dry_run=true`,
Entonces responde `diff: [{field:"address", from:"old.db.local",
to:"new.db.local"}]` y `patch: [{op:"replace", path:"/address", ...}]`, no
toca CyberArk, y queda un `account.update` con `dry_run:true` en el audit.

**Escenario (ejecución):**
Dado el mismo cambio con `dry_run=false` y `reason="pain: la DB migró de host"`,
Entonces PAM Web Console manda el PATCH, la cuenta queda con el address nuevo, y el
audit tiene `dry_run:false`, `before.address`, `after.address`, `reason`.

**Escenario (feature apagada):**
Dado `enabled_features["account.update"]` ausente para el tenant,
Cuando el operador intenta el update (dry-run incluido),
Entonces `403 feature_disabled` y queda `account.update` con
`result:"denied"`.

**Escenario (permiso faltante en CyberArk):**
Dado la feature `true` en PAM Web Console pero el service account sin `Update Account
Properties`,
Cuando ejecuta,
Entonces `403` con el texto de CyberArk y `account.update` con
`result:"error"` — la cuenta no cambió.

### Frontend

- `DetailDrawer` de una cuenta: botón **"Editar"** visible sólo si
  `tenant.features["account.update"]` **y** rol efectivo ≥ `operator`.
  (Se agrega `features` a `TenantSummary` / a `GET /tenants/{id}`.)
- Form con los campos editables (prefill del objeto actual).
- **"Previsualizar cambios"** → llama con `dry_run=true` → `<DryRunDialog>`
  muestra el `diff` (from → to por campo) + `warnings`.
- **"Confirmar"** → pide `reason` (textarea, ≥ 8 chars) → llama con
  `dry_run=false` → toast de éxito + invalida la query de la cuenta.
- Hook `useWriteOp(path)` que encapsula el par dry-run/confirm y el estado.

## Roadmap de operaciones de Fase 2 (cada una su spec)

| # | Operación | Feature | Notas |
|---|---|---|---|
| 1 | **Editar metadata de cuenta** | `account.update` | esta spec (referencia) |
| 2 | Change / Verify / Reconcile on-demand | `account.change` | dispara el CPM; "reversible" en el sentido de que no rompe nada permanente |
| 3 | Vincular logon / reconcile account | `account.link` | PATCH de `secretManagement` refs |
| 4 | Agregar miembro a un safe | `safe.member.add` | da acceso — límite: sólo *agregar*, con permisos de una lista blanca |
| 5 | Activar usuario suspendido | `user.activate` | POST a `/API/Users/{id}/activate` |
| 6 | Crear cuenta (con plantilla por cliente) | `account.create` | necesita la spec de plantillas; POST no-idempotente |
| 7 | Crear safe (con plantilla) | `safe.create` | idem |

## Tareas (maquinaria + 1ª operación)

- [x] `app/core/features.py` (`feature_enabled` + `KNOWN_FEATURES`). El
      gating + el audit de la denegación viven en `writeop.run_write`, no en
      una dependency (así el dry-run denegado también queda auditado).
- [x] `GET /tenants/{id}/features` (tenant_admin efectivo) + `PUT
      /tenants/{id}/features` (solo platform_admin, auditado, rechaza
      features desconocidas) + `my_role` en `GET /tenants` y
      `GET /tenants/{id}`.
- [x] `app/cyberark/client.py`: `retry_safe` en `_request` (POST no
      reintenta); `post`/`patch`/`delete`; `update_account(id, patch)`.
- [x] `app/core/writeop.py`: `run_write(...)` (feature gate + audit denied +
      reason ≥ 8 + rate-limit `(op, tenant)` + circuit breaker + dry-run +
      ejecución + audit before/after) + config `PAM_WRITE_*` +
      `.env.example`. Dataclass `Plan`.
- [x] **1ª operación**: `POST /tenants/{id}/accounts/{id}/update`
      (`account.update`), `app/domain/accounts.py` (`build_account_plan`),
      `tests/unit/test_account_update.py` (dry-run, ejecución, sin reason,
      feature off, viewer, rate-limit, no-op).
- [x] Frontend: `<DryRunDialog>` (previsualizar → diff → razón → confirmar) +
      `<AccountEditSection>` (botón "Editar metadata" gateado por
      `enabled_features` + `my_role`) + `lib/roles.ts` +
      `pages/Tenants.tsx` (toggles de features por tenant, platform_admin).
- [x] `docs/runbooks/habilitar-feature-de-escritura.md`.
- [x] `CLAUDE.md` + `docs/security.md` + `docs/PLAN.md` + `README.md`.
- [x] Probado contra `lab` en el server (2026-09-10): toggle de la
      feature → "Editar metadata" → dry-run/diff → confirmar con razón →
      cambio reflejado en la grilla y en el audit. name/address/userName OK.
      (El service account del lab ya tenía permiso; no hizo falta ampliarlo.)
- [ ] Pendiente (menor, no bloquea): editor de `platformAccountProperties`
      en la UI; habilitar `account.update` para un cliente real (runbook).

## Notas de cierre (maquinaria)

- El verbo quedó `POST .../accounts/{id}/update` (sub-path, no `:update`):
  el converter `str` de FastAPI es greedy y `{account_id}` se comía el
  `:update`.
- `require_feature` como dependency **no** se implementó: el gate va dentro
  de `run_write` para poder auditar la denegación con el `action` correcto.
- `my_role` en la respuesta de tenants: la UI lo necesita para mostrar el
  botón "Editar" sólo a quien puede.

## Decisiones abiertas

1. **Verbo del endpoint**: `POST .../accounts/{id}:update` (estilo Google
   AIP, deja libre el `PATCH` REST puro para más adelante) vs `PATCH
   .../accounts/{id}` con `dry_run` en query. — Propongo el `:update` para
   que "escritura" sea siempre un POST explícito con body `{changes, dry_run,
   reason}`, homogéneo con las que vienen.
2. **Rate-limit de escritura ante Redis caído**: permitir + loguear (como el
   login) vs bloquear. — Propongo permitir + `rate_limit_checked:false` en la
   respuesta y un `warning` en el audit.
3. **Gestión de features**: endpoint + UI mínima ahora, o CLI/seed hasta que
   haya más de un puñado de cambios. — Propongo el endpoint ahora (lo
   necesita el gating y el front), UI en una iteración corta después.
4. **`reason` mínimo**: ¿8 chars alcanza, o pedir un formato (ticket / texto
   libre)? — Propongo texto libre ≥ 8 chars por ahora; formalizar si Legal
   lo pide.
