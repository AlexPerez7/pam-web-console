# rbac-por-tenant

- **Estado:** implementada — pendiente de probar en el server
- **Roadmap:** Fase 1, ítem "RBAC scopeado por tenant (`role_assignments`), refresh token, `/audit` restringido a tenant_admin+ y scopeado"
- **Fecha:** 2026-09-09

## Problema

1. **Sin aislamiento por operador.** Hoy `Operator.role` es global: cualquier
   `viewer` ve y consulta los 4 tenants. Para un MSP, un operador asignado a
   Globex no debería ver Umbrella. `docs/PLAN.md §10` pide "RBAC con scope
   por tenant; deny-by-default".
2. **La sesión se corta a las 8 h.** El JWT expira y `api.ts` desloguea. No
   hay refresh.
3. **`/audit` lo ve cualquier `viewer`** y sin scope — debería ser
   `tenant_admin+` y cada uno ver solo el audit de sus tenants.

## Alcance

**Entra:**
- Tabla `role_assignments` (operador × tenant × rol) + migración `0004`.
- Enforcement de acceso por tenant en todos los endpoints `/tenants/{id}/*`,
  `GET /tenants`, y la búsqueda cross-tenant.
- CRUD de asignaciones (solo `platform_admin`).
- Refresh token: `POST /auth/refresh`, el frontend renueva el access token
  de forma transparente.
- `GET /audit`: `tenant_admin+` y scopeado por tenant.

**Fuera (otra spec / fase):**
- Revocación granular de refresh tokens (hoy: stateless; se invalidan
  cambiando `PAM_SECRET_KEY` o con `is_active=false`). Store de sesiones en
  Fase 2+.
- Roles más finos que la jerarquía actual
  (viewer<operator<approver<tenant_admin<platform_admin).
- Grupos / asignación por grupo — llega si se integra AD (Fase 1+).

## Modelo

`Operator.role` pasa a ser el **rol de plataforma**:
- `platform_admin` → superusuario: todos los tenants, gestiona operadores y
  asignaciones. **No necesita asignaciones.**
- Cualquier otro rol de plataforma (`viewer`…`tenant_admin`) **no da acceso
  a ningún tenant por sí solo**. El acceso viene de `role_assignments`.

`role_assignments`:
| col | |
|---|---|
| `id` | PK |
| `operator_id` | FK operators, on delete cascade |
| `tenant_id` | FK tenants **o** el literal `*` (todos los tenants) |
| `role` | `operatorrole` enum — el rol efectivo del operador en ese tenant |
| `created_at` | |

- Unique `(operator_id, tenant_id)`.
- El **rol efectivo** de un operador en el tenant T = el de su asignación a
  T, si no hay, el de su asignación a `*`, si no hay, sin acceso.
- `platform_admin` global ignora la tabla (siempre `platform_admin` en todos).

Migración `0004`: crea la tabla. No toca operadores existentes — el único
operador hoy es `platform_admin`, así que nada se rompe. Documentar: tras la
migración, los operadores no-`platform_admin` necesitan asignación explícita.

## Requisitos

### R1 — Acceso por tenant

Nuevo `app/core/rbac.py`:
- `async effective_role(op, tenant_id, session) -> OperatorRole | None`
- `async accessible_tenant_ids(op, session) -> set[str] | None` (None = todos)

Nuevo dep `require_tenant_access(min_role=viewer)` en `deps.py`: resuelve
`tenant_id` del path, calcula el rol efectivo, 403 si `< min_role` o sin
acceso. Devuelve el `Operator` (como `require_role`).

Aplicar:
- `inventory.py`, `dashboards.py` — reemplazan `_viewer` por
  `require_tenant_access(viewer)`.
- `tenants.py` — `GET /tenants` filtra a `accessible_tenant_ids`;
  `GET /tenants/{id}` usa `require_tenant_access(viewer)`.
- `search.py` — abanica solo los tenants accesibles (además de habilitados +
  privilege_cloud).

**Escenario:**
Dado el operador `jperez` (`viewer`) con asignación solo a `globex`,
Cuando hace `GET /tenants`,
Entonces la respuesta trae solo Globex;
y `GET /tenants/umbrella/accounts` responde `403`.

**Escenario (platform_admin):**
Dado `alex` (`platform_admin`),
Entonces ve y consulta los 4 tenants sin asignaciones.

### R2 — CRUD de asignaciones

Bajo `require_role(platform_admin)`:
- `GET /operators/{id}/assignments` → lista.
- `PUT /operators/{id}/assignments` → reemplaza el set completo:
  `[{tenant_id, role}]` (tenant_id puede ser `*`). Valida que los
  `tenant_id` existan (o sean `*`) y que los roles sean válidos. Auditado
  `operator.assignments` con `target=<username>`, detail con el diff.

El panel de Operadores muestra, por operador, sus tenants asignados y
permite editarlos.

### R3 — Refresh token

- `security.py`: `make_refresh_token(operator_id)` (JWT HS256, `typ:"refresh"`,
  TTL `PAM_REFRESH_TTL_DAYS`, default 30) / `read_refresh_token`.
- `POST /auth/login` (respuesta ok) y `/auth/refresh` devuelven
  `{access_token, refresh_token, ...}`. `/auth/refresh` valida el refresh,
  chequea que el operador siga `is_active`, emite un access nuevo (y rota el
  refresh).
- `deps.current_operator` sigue aceptando solo el access token (rechaza uno
  con `typ:"refresh"`).
- Frontend: `auth.tsx` guarda ambos; `api.ts` ante un `401` en un endpoint
  autenticado intenta `POST /auth/refresh` una vez y reintenta; si el
  refresh falla, desloguea.

**Escenario:**
Dado un access token vencido y un refresh válido,
Cuando el frontend hace una llamada,
Entonces `api.ts` renueva el token y la llamada original devuelve `200` sin
que el operador note nada.

### R4 — `/audit` restringido y scopeado

- `GET /audit` y `/audit/verify`: `require_role(tenant_admin)` (rol de
  plataforma) **o** `tenant_admin`/`platform_admin` efectivo en al menos un
  tenant.
- Scope: si no es `platform_admin` global, los resultados se limitan a
  `accessible_tenant_ids` (y a tenants donde su rol efectivo es
  `tenant_admin+`). Pedir `?tenant_id=X` fuera de ese set → 403.
- `platform_admin` global: sin cambios (ve todo).

## Diseño técnico

- `app/models/role_assignment.py` — `RoleAssignment(Base, TimestampMixin?)`.
  Agregar a `app/models/__init__.py`.
- `alembic/versions/0004_role_assignments.py`.
- `app/core/rbac.py` — helpers puros + los 2 async de R1.
- `app/core/deps.py` — `require_tenant_access(min_role)`; helper para el
  gating de `/audit`.
- `app/config.py` — `refresh_ttl_days: int = 30`.
- `app/api/`: `tenants.py`, `inventory.py`, `dashboards.py`, `search.py`,
  `audit.py`, `auth.py`, `operators.py`.
- Frontend: `lib/api.ts` (refresh interceptor), `lib/auth.tsx` (2 tokens),
  `lib/types.ts`, `pages/Operators.tsx` (editor de asignaciones).

## Entrega por fases

1. **role_assignments + enforcement de acceso** (R1) + migración. `/tenants`
   ya scopeado ⇒ el frontend queda scopeado sin tocarlo.
2. **CRUD de asignaciones** (R2) + editor en Operadores.
3. **Refresh token** (R3).
4. **`/audit` restringido** (R4).

## Tareas

- [x] F1: `RoleAssignment` + migración `0004` + `rbac.py` +
      `require_tenant_access` + aplicar a tenants/inventory/dashboards/search.
- [x] F2: `GET/PUT /operators/{id}/assignments` + editor "Accesos" en
      `Operators.tsx`.
- [x] F3: access + refresh token (`typ`), `POST /auth/refresh`; `api.ts`
      renueva de forma transparente ante 401.
- [x] F4: `/audit` con `require_audit_reader` + scope por `audit_tenant_ids`;
      `GET /tenants/{id}/audit` para el rastro por objeto (el `DetailDrawer`
      lo usa en vez de `/audit?tenant_id=`).
- [x] `tsc` + `build` verdes por fase; `py_compile`.
- [x] `CLAUDE.md` + `docs/PLAN.md` + `docs/security.md`.
- [ ] Probar en el server, mover a `docs/specs/done/` (ya movida).

## Notas de implementación

- `Operator.role` NO es superusuario salvo `platform_admin`. Un
  `tenant_admin` de plataforma tampoco ve todos los tenants sin asignación
  (se decidió deny-by-default estricto).
- Migración `0004` no toca operadores: el único hoy es `platform_admin`.
  Los operadores no-`platform_admin` necesitan asignación explícita
  (editor "Accesos" en el panel de Operadores).
- Refresh stateless (sin store). Rotación en cada `/auth/refresh`.
- Tokens viejos (sin `typ`) tras el deploy: `read_token` los acepta como
  access (no rompe sesiones en curso). Al primer refresh pasan al formato nuevo.
