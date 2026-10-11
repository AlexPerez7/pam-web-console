# user-activate — Reactivar un usuario suspendido del Vault

- **Estado:** hecha. Probada contra Globex (2026-09-12): usuario de
  prueba suspendido y reactivado.
- **Roadmap:** `docs/PLAN.md §7` Fase 2 — "Activar usuario suspendido
  (`user.activate`)"
- **Fecha:** 2026-09-10
- **Base:** `docs/specs/done/fase-2-escritura.md` (maquinaria `writeop`),
  `docs/specs/done/account-change.md` (op de acción con `effect`).

## Problema

Un usuario del Vault que falla el login demasiadas veces queda **suspendido**
(bloqueado). Es una llamada diaria del equipo PAM: "no puedo entrar", hay que
reactivarlo. Hoy se hace en el PVWA de cada cliente. Es una operación de
riesgo mínimo (levanta un bloqueo, no cambia datos, se puede volver a
suspender solo) → buena candidata de Fase 2.

## Alcance

**Entra:**
- Reactivar **un** usuario del Vault que está **suspendido** por intentos
  fallidos de login — `POST /API/Users/{id}/activate`.
- Vía `writeop.run_write`: feature `user.activate`, `reason`, dry-run, audit.
- UI: acción "Reactivar usuario" en el detalle de un usuario (nuevo — hoy el
  usuario sólo abre un volcado de propiedades).

**Fuera:**
- **Habilitar / deshabilitar** un usuario (`enableUser`) — es editar el
  usuario, más impacto; sería `user.enable` en otra spec si se pide.
- Resetear la contraseña del usuario, cambiar TOTP, etc. — CRUD de usuarios
  del Vault es Fase 3.
- Usuarios de Identity (no del Vault) — se gestionan en la consola de
  Identity, no acá.
- Desbloquear una **cuenta** (`unlockAccounts`) — es otra cosa (permiso de
  safe), no un usuario.

## Diseño

### Feature-flag

- `user.activate` — ya está en `KNOWN_FEATURES`. Toggle en la página Tenants
  ("Reactivar usuario suspendido").

### Es una op de acción (sin diff)

Como `account.change`: `Plan.effect` + `warnings`, sin `patch` ni
`before/after`.

- `Plan.patch = []`, `Plan.diff = []`
- `Plan.effect = "Se reactiva al usuario **<username>**, suspendido por
  intentos fallidos de login. Vuelve a poder iniciar sesión."`
- `Plan.warnings` según el estado actual del usuario:
  - `suspended` es `false` → "este usuario no está suspendido; la operación
    no va a tener efecto."
  - `enableUser` es `false` → "además está deshabilitado; reactivarlo no lo
    habilita (eso es otra operación)."
- `Plan.object_count = 1`

### Backend

- `app/cyberark/client.py`:
  - `get_user(user_id)` → `GET /API/Users/{id}` (para el estado actual).
  - `activate_user(user_id)` → `POST /API/Users/{id}/activate`. **Idempotente**
    (el estado final es "activo"), así que `retry_safe=True`.
- `app/domain/users.py` (nuevo): `plan_user_activate(current: dict, _changes)
  -> Plan` — puro, arma `effect` + `warnings` mirando `current["suspended"]`
  / `current["enableUser"]`.
- `app/api/writes.py`:
  - `POST /tenants/{tenant_id}/users/{user_id}/activate` — body `WriteActionIn`
    (`dry_run`, `reason`), `run_write(feature="user.activate",
    target=user_id, fetch=get_user, build_plan=plan_user_activate,
    apply=lambda c,_: c.activate_user(user_id))`.
  - RBAC: `require_tenant_access(OperatorRole.operator)`.

### Frontend

- `DetailDrawer` gana un prop `userId?: string`. Cuando `tenant && userId`,
  hace `useQuery(["user-detail", tenant, userId])` →
  `GET /tenants/{t}/users/{id}` (endpoint de lectura nuevo, auditado
  `user.get`) para tener el estado fresco.
  - Muestra una línea de estado arriba (activo / suspendido / deshabilitado)
    — reusa la lógica de badges que ya está en la columna "Estado" de la
    grilla de Usuarios (extraer a `lib/userStatus.ts`).
- `components/UserActivateSection.tsx` (nuevo) — visible sólo si
  `enabledFeatures["user.activate"]` **y** `myRole >= operator`:
  - Si el usuario está `suspended`: botón "Reactivar usuario" (destacado).
  - Si no: el botón aparece deshabilitado con el texto "el usuario no está
    suspendido".
  - Click → `<DryRunDialog path="/tenants/{t}/users/{id}/activate"
    changes={{}} title="Reactivar usuario">` (muestra el `effect`).
  - `onDone` → invalida `["user-detail", t, id]`, `["inv", t, "users"]`,
    `["audit", t, id]`. (helper `invalidateUser` en `lib/invalidate.ts`.)
- `pages/Inventory.tsx` `UsersTab`: pasarle `userId={String(sel?.id)}` y
  `tenant` al `DetailDrawer`.
- `app/api/inventory.py`: `GET /tenants/{id}/users/{user_id}` — lectura
  puntual de un usuario, auditada `user.get`.

## Requisitos / escenarios

**R1 — preview de un usuario suspendido:**
Dado un usuario `jdoe` con `suspended:true` en `lab`, la feature
`user.activate` on, y un operador `operator+`,
Cuando abre el detalle de `jdoe` y previsualiza "Reactivar usuario",
Entonces ve el `effect`, **no se llama a CyberArk para escribir**, y queda un
`user.activate` `dry_run:true` en el audit.

**R2 — ejecución:**
Dado el mismo caso con `dry_run:false` y `reason:"usuario reporta bloqueo, ticket OPS-9"`,
Entonces PAM Web Console hace `POST /API/Users/{id}/activate`, responde `ok`, la grilla
de Usuarios y el detalle se refrescan (el badge pasa a "activo"), y el audit
tiene `user.activate` `dry_run:false` con `reason` y `effect`.

**R3 — usuario no suspendido:**
Dado `jdoe` con `suspended:false`,
Cuando previsualiza,
Entonces ve el warning "no está suspendido; la operación no va a tener
efecto". (El botón igual está deshabilitado en la UI para este caso.)

**R4 — usuario deshabilitado:**
Dado `jdoe` con `suspended:true` y `enableUser:false`,
Cuando previsualiza,
Entonces ve además el warning "también está deshabilitado; reactivarlo no lo
habilita".

**R5 — feature apagada / RBAC:** heredados de `writeop` — feature off → `403`
+ `denied` en el audit (dry-run incluido); `viewer` → `403` antes de
`run_write`.

**R6 — rate-limit:** heredado (un preview no cuenta).

## Tareas

- [x] `app/cyberark/client.py`: `get_user`, `activate_user` (`retry_safe=True`).
- [x] `app/domain/users.py`: `plan_user_activate`.
- [x] `app/api/inventory.py`: `GET /tenants/{id}/users/{user_id}` (auditado
      `user.get`).
- [x] `app/api/writes.py`: `POST /tenants/{id}/users/{user_id}/activate`.
- [x] Página Tenants: toggle `user.activate`.
- [x] Tests unit (`tests/unit/test_user_activate.py`, 7): `plan_user_activate`
      (suspendido / no suspendido / deshabilitado) + endpoint (dry-run,
      ejecución, feature off → denied, viewer → 403). 105 backend verdes.
- [x] Frontend: `DetailDrawer` prop `userId` + badge de estado del usuario,
      `UserActivateSection`, `lib/userStatus.ts` (reusado por la grilla de
      Usuarios), `lib/invalidate.ts` (`invalidateUser`), wiring en
      `Inventory` `UsersTab`.
- [x] `ruff` + `mypy` + `pytest` + `lint` + `tsc` + `build` verdes.
- [x] `CLAUDE.md` + `docs/PLAN.md §7`.
- [x] Probar contra un tenant con la feature habilitada: suspender un usuario
      de prueba a mano (fallar el login 3+ veces), reactivarlo desde PAM Web Console,
      verificar en el PVWA. Probado contra Globex (2026-09-12).

## Decisiones abiertas

1. **¿Vale la pena el endpoint de lectura `GET /users/{id}`?** El detalle de
   Usuarios hoy usa el objeto de la grilla. Para reflejar el estado tras
   reactivar sin recargar toda la lista, conviene un GET puntual. — Propongo
   agregarlo (chico, y sirve para el detalle en general).
2. **Nombre del estado en la UI.** "suspendido" (por login) vs "bloqueado".
   PVWA dice "Suspended". — Propongo "suspendido".
