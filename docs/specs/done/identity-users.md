# identity-users — Sección Identity: ver estado y bloquear/desbloquear un usuario

- **Estado:** hecha. Probada contra un tenant real por Alex (2026-09-15) —
  confirmado que el bearer ISPSS que PAM Web Console ya usa para Redrock alcanza sin
  cambios para `UserMgmt/*` (mayor riesgo de la spec, resuelto).
- **Roadmap:** `docs/PLAN.md` § "Identity (administración de SSO/MFA)"
- **Fecha:** 2026-09-15
- **Base:** `docs/specs/done/user-activate.md` (op de acción con `effect`,
  mismo patrón sin diff), `docs/specs/done/reportes.md` (Redrock de Identity,
  ya usado por `identity-user-activity`).

## Problema

El PVWA clásico separa "Users and Groups" del Vault (recién CRUDeado en
`docs/specs/vault-users-groups-crud.md`) de "Identity Administration"
(usuarios/roles de SSO/MFA). PAM Web Console hoy sólo **lee** de Identity para el
buscador de miembros (`client.redrock_query`, sólo `SELECT`) y para el
reporte `identity-user-activity` — no hay forma de actuar sobre un usuario de
Identity bloqueado sin salir a la consola de Identity. Acordado con Alex
(2026-09-14): PAM Web Console suma una sección propia que espeja esa separación del
PVWA, empezando por lo de menor riesgo.

## Alcance

**Entra:**
- Sección de nav nueva **"Identity"** — **sólo** contenido que sale de la
  API de Identity/SSO, nada del Vault (corregido a mitad de sesión: Alex
  aclaró "ahí no vemos usuarios o grupos del vault", refiriéndose al PVWA
  — ver `nav-spaces.md`).
- Lista de usuarios de Identity del tenant (reusa el mismo
  `SELECT ... FROM User` por Redrock que ya usa `identity-user-activity`,
  agregando el campo `ID`).
- Ver el estado puntual de un usuario de Identity (bloqueado / no bloqueado,
  vía `IsUserCloudLocked`).
- **Bloquear / desbloquear** un usuario de Identity (`SetCloudLock`) — acción
  reversible, mismo criterio de riesgo que `user.activate` (Vault).
- **Lista de roles de Identity, sólo lectura** (agregado el mismo día,
  pedido explícito de Alex: "solo necesitamos los usuarios y roles de
  Identity") — nombre + descripción, vía Redrock (`SELECT ID, Name,
  Description FROM Role`). Sin detalle, sin miembros, sin permisos
  administrativos.

**Fuera (del primer corte):**
- `RemoveUsers` (borrar), `InviteUsers`/`SendLoginEmails`/`SendSmsInvite`
  (invitar), `ChangeUserPassword` (cambiar password) — son identidades
  reales de SSO de clientes, más consecuente que un usuario nativo del
  Vault; posible spec propia si se pide.
- CRUD de roles (`StoreRole`/`UpdateRole`/`AssignSuperRights`), `Get role
  members`, `scim-api`, `cdirectory-service-api` — sólo entró la lista de
  roles de sólo lectura; el resto de `roles-api` sigue sin caso de uso
  concreto.
- Herramientas de migración de directorio (`MigrateDirectoryServiceEntities`/
  `RollbackMigration`) — fuera de alcance, no es CRUD.
- `GetUsersRolesAndAdministrativeRights` (roles asignados a un usuario
  puntual, distinto de la lista general de roles) — se puede sumar al
  detalle de usuario más adelante, no bloquea este corte.

## Investigación (2026-09-15, portal `api-docs.cyberark.com/identity-docs-api`)

El portal de referencia **no exige login para leer la documentación**
(sólo lo exige el botón "Send API Request" para probar en vivo contra un
tenant) — se pudo revisar `user-mgmt-api`, `roles-api` y `scim-api`
completos sin credenciales.

- **Auth: Bearer JWT contra `{tenant_url}`, igual patrón en las tres APIs.**
  Es el mismo esquema que ya usa `client.redrock_query` contra
  `{identity_tenant_url}/Redrock/query` con el token ISPSS
  (`self._auth`, obtenido en `ISPSSAuth.login` vía
  `POST {identity_tenant_url}/oauth2/platformtoken`, client_credentials).
  **Muy probable que el mismo bearer sirva sin cambios** — pendiente
  confirmar contra un tenant real que el service account tenga los
  "administrative rights" de Identity que estas APIs piden (ver
  Requisitos, gap explícito).
- **Endpoints exactos usados en esta spec** (todos bajo
  `{identity_tenant_url}/UserMgmt/...`, `POST`, Bearer Auth):
  - `GetUserInfo?ID=<uuid>` (default = usuario actual si se omite) — devuelve
    el objeto completo del usuario. Nota de la doc: "high CPU intensive
    call... cache as needed" — se llama sólo al abrir el detalle de un
    usuario puntual, nunca en la lista.
  - `IsUserCloudLocked?user=<nombre-o-uuid>` — `{Result: "True"/"False"
    (string), success}`.
  - `SetCloudLock?lockUser=<true|false>&user=<uuid>` — **exige el ID**, no
    el nombre (a diferencia de `IsUserCloudLocked`, que acepta ambos).
    "Only system administrator, users with user management rights, or the
    user itself can invoke this API."
- **SCIM API (hallazgo, no se usa en este corte):** expone CRUD completo por
  SCIM 2.0 estándar de Users/Groups y, llamativamente,
  `Containers`(=Safes)/`ContainerPermissions`(=miembros de safe)/
  `PrivilegedData` — es decir, una superficie alternativa para buena parte
  de lo que PAM Web Console ya hace vía `/API/...` clásico. Se deja anotado como
  alternativa a evaluar a futuro (¿reemplaza algún endpoint frágil actual?),
  fuera de alcance de esta spec.
- **Roles API:** CRUD de roles (`StoreRole`/`UpdateRole`/`AssignSuperRights`/
  `GetRole`/`GetRoleMembers`) + scripts de rol dinámico. Sin caso de uso
  concreto para PAM Web Console todavía — no se explora más en este corte.

## Requisitos / escenarios

**R1 — listar usuarios de Identity:**
Dado un tenant con Identity configurado (todos los Privilege Cloud lo
tienen), cuando un operador `viewer+` abre "Identity",
Entonces ve la tabla de usuarios (Usuario/Nombre/Email/Estado/Último login),
reusando la misma consulta Redrock de `identity-user-activity` + columna
`id` (no visible, sólo para navegar al detalle).

**R2 — ver estado de un usuario puntual:**
Dado un usuario `jdoe` de Identity,
Cuando el operador hace click en su fila,
Entonces PAM Web Console llama `GetUserInfo` + `IsUserCloudLocked` en paralelo y
muestra el detalle con un badge "Bloqueado" / "Activo".

**R3 — preview de bloquear/desbloquear:**
Dado el tenant con la feature `identity_user.lock` habilitada y `jdoe` no
bloqueado,
Cuando el operador `operator+` previsualiza "Bloquear usuario",
Entonces ve el `effect` ("Se bloquea a **jdoe**; no va a poder iniciar
sesión hasta que se lo desbloquee"), **sin llamar a `SetCloudLock`**, y
queda `identity_user.lock` `dry_run:true` en el audit.

**R4 — ejecución:**
Dado el mismo caso con `dry_run:false` y `reason:"ticket OPS-12, dispositivo
comprometido"`,
Entonces PAM Web Console llama `SetCloudLock(lockUser=true, user=<id>)`, el detalle se
refresca (badge "Bloqueado"), y el audit tiene `identity_user.lock`
`dry_run:false` con `reason` y `effect`.

**R5 — desbloquear:** simétrico a R3/R4 con `lockUser=false` — mismo botón
cambia de texto según el estado actual (mismo patrón que
`platform.activate`, una sola feature cubre ambas direcciones).

**R6 — usuario ya en el estado pedido:** si `jdoe` ya está bloqueado y se
pide bloquear de nuevo, `Plan.warnings` avisa "ya está bloqueado; la
operación no va a tener efecto" (mismo criterio que R3 de
`user-activate.md`).

**R7 — feature apagada / RBAC / rate-limit:** heredados de `writeop.run_write`
(igual que el resto de las operaciones de acción).

**R8 — bearer sin permisos de Identity (gap a confirmar):** si el service
account no tiene "administrative rights" en Identity, `UserMgmt/*` responde
403/401 — PAM Web Console lo mapea a un `CyberArkError` claro ("el service account no
tiene permisos de administración en Identity") en vez de un 500 genérico.
Sólo se puede confirmar/cerrar probando contra un tenant real.

## Diseño técnico

### Cliente CyberArk (`app/cyberark/client.py`)

Nuevos métodos, mismo patrón que `redrock_query` (URL absoluta contra
`identity_tenant_url`, reutilizando `self._request`):

```python
async def identity_get_user_info(self, user_id: str) -> dict: ...
    # POST {identity}/UserMgmt/GetUserInfo?ID=<user_id>

async def identity_is_user_locked(self, user: str) -> bool: ...
    # POST {identity}/UserMgmt/IsUserCloudLocked?user=<user>
    # parsea "Result" == "True" (viene como string, no bool)

async def identity_set_cloud_lock(self, user_id: str, lock: bool) -> None: ...
    # POST {identity}/UserMgmt/SetCloudLock?lockUser=<lock>&user=<user_id>
    # retry_safe=True (idempotente: el estado final es fijo)
```

Todos levantan `CyberArkError` si `identity_tenant_url` no está configurado
(mismo guard que `redrock_query`) o si la respuesta trae `success: false`.

### Dominio (`app/domain/identity_users.py`, nuevo)

```python
def plan_identity_user_lock(current: dict, changes: dict) -> Plan:
    # current = {"locked": bool, "username": str} (ya resuelto por el fetch)
    # changes = {"lock": bool}
    # Plan.effect = "Se bloquea/desbloquea a **<username>**..."
    # Plan.warnings si current["locked"] == changes["lock"]
    # Plan.patch = [], Plan.diff = [], Plan.object_count = 1
```

Puro, mismo estilo que `plan_user_activate`.

### API

- `GET /tenants/{id}/identity-users` — lista (reusa
  `client.redrock_query` con el mismo SQL que `identity_user_activity`,
  agregando `ID`; cache Redis corta igual que `safes_rows_cached`, TTL a
  definir). Auditado `identity_user.list`. `viewer+`.
- `GET /tenants/{id}/identity-users/{user_id}` — `GetUserInfo` +
  `IsUserCloudLocked` en paralelo (`asyncio.gather`). Auditado
  `identity_user.get`. `viewer+`.
- `POST /tenants/{id}/identity-users/{user_id}/lock` y
  `POST /tenants/{id}/identity-users/{user_id}/unlock` — dos endpoints,
  mismo criterio que `platform.activate`/`deactivate` (no un body con
  `{lock: bool}`, para que `changes={}` y el `<DryRunDialog>` genérico
  funcione sin cambios). Comparten `plan_identity_user_lock(target_lock)`
  (factory, `target_lock` fijo por endpoint) y una función privada
  `_identity_user_set_lock`. RBAC `operator+` (reversible, sin
  confirmación reforzada — mismo criterio que
  `user.activate`/`session.terminate`).
- `GET /tenants/{id}/identity-roles` (agregado el mismo día, pedido de
  Alex) — lista de roles, vía Redrock (`SELECT ID, Name, Description FROM
  Role ORDER BY Name`). Auditado `identity_role.list`. `viewer+`. Sin
  detalle/miembros (fuera de alcance, ver "Decisiones abiertas").

### Feature-flag

- `identity_user.lock` nuevo en `KNOWN_FEATURES`
  (`app/core/features.py`) **y** en la lista `FEATURES` de
  `frontend/src/pages/Tenants.tsx` en el mismo commit (gotcha ya repetido
  dos veces, ver `docs/PLAN.md`/memoria del proyecto).

### Frontend

- **Estado final (2026-09-15, tras la 4ª corrección de `nav-spaces.md`):**
  el espacio Identity tiene **sólo** dos ítems, ambos vía la API de
  Identity/SSO — nada del Vault (Usuarios/Grupos del Vault viven en
  Operación, junto a Cuentas/Safes/Plataformas; ver `nav-spaces.md`).
  - **"Usuarios"** (`/sso-users`, `pages/SsoUsers.tsx`) — de esta spec.
    Sin sufijo en el label: dentro de este espacio no hay nada más
    llamado "Usuarios" que desambiguar.
  - **"Roles"** (`/identity-roles`, `pages/IdentityRoles.tsx`) — pedido
    por Alex el mismo día ("solo necesitamos los usuarios y roles de
    Identity"), fuera del alcance original de esta spec pero implementado
    junto con ella por ser del mismo dominio. Lista de sólo lectura
    (nombre/descripción, `GET /tenants/{id}/identity-roles`, vía Redrock
    `SELECT ID, Name, Description FROM Role` — mismo patrón que la lista
    de usuarios). **Sin miembros ni permisos administrativos** (eso sí
    sigue fuera de alcance, ver "Decisiones abiertas").
- `TenantScope` (`components/TenantScope.tsx`) + `QueryState`
  (`components/QueryState.tsx`) se reusan tal cual en ambas páginas.
- `pages/SsoUsers.tsx`: click en una fila abre `DetailDrawer` con el prop
  nuevo `identityUserId` — muestra:
  - Badge de estado (bloqueado/activo) desde `IsUserCloudLocked`.
  - `IdentityUserLockSection` (nuevo componente, calcado de
    `PlatformActivateSection`): botón "Bloquear usuario" /
    "Desbloquear usuario" según el estado actual → `<DryRunDialog
    path="/tenants/{t}/identity-users/{id}/lock" changes={{}}>` (dos
    endpoints, `/lock` y `/unlock` — no un body `{lock: bool}`, mismo
    criterio que `platform.activate`/`deactivate`).
  - Gateado por `identity_user.lock` + `operator+`.

## Tareas

- [x] `app/cyberark/client.py`: `identity_get_user_info`,
      `identity_is_user_locked`, `identity_set_cloud_lock`.
- [x] `app/domain/identity_users.py`: `plan_identity_user_lock(target_lock)`
      (factory, mismo patrón que `plan_platform_activation`) + tests unit.
- [x] `app/api/identity.py` (router nuevo): `GET /tenants/{id}/identity-users`
      (lista, vía Redrock) + `GET /tenants/{id}/identity-users/{user_id}`
      (detalle, `GetUserInfo` + `IsUserCloudLocked` en paralelo).
- [x] `app/api/writes.py`: `POST /tenants/{id}/identity-users/{user_id}/lock`
      + `.../unlock` — dos endpoints (no un body `{lock: bool}`), mismo
      criterio que `platform.activate`/`deactivate`, para que el
      `<DryRunDialog>` genérico (`changes={}`) funcione sin cambios.
- [x] `identity_user.lock` en `KNOWN_FEATURES` + `Tenants.tsx` `FEATURES`
      (mismo commit).
- [x] Frontend: `pages/SsoUsers.tsx` (lista, ruta `/sso-users`, ítem
      "Usuarios" del espacio Identity), `DetailDrawer` prop
      `identityUserId` (badge bloqueado/activo), `IdentityUserLockSection`
      (bloquear/desbloquear, mismo patrón que `PlatformActivateSection`).
- [x] `pages/IdentityRoles.tsx` (lista de roles, ruta `/identity-roles`,
      ítem "Roles" del espacio Identity) + `GET /identity-roles` en
      `app/api/identity.py` — agregado junto con el resto por pedido de
      Alex el mismo día, no estaba en el alcance original.
- [x] Vault Usuarios/Grupos sacados del espacio Identity, movidos a
      Operación (`pages/Identity.tsx` → renombrado `pages/Users.tsx`,
      ruta `/identity` → `/users`) — corrección de `nav-spaces.md`, no de
      esta spec, pero tocó los mismos archivos.
- [x] Tests backend: `tests/unit/test_identity_client.py` (cliente: URL,
      params, parseo de `Result` string→bool, error `success:false`, sin
      Identity configurado) + `tests/unit/test_identity_user_lock.py`
      (dominio: bloquear/desbloquear/ya-en-ese-estado; endpoint: dry-run,
      ejecución lock/unlock, feature off → denied, viewer → 403) +
      `tests/unit/test_identity_lists.py` (`GET /identity-users` y
      `GET /identity-roles`).
- [x] `ruff` + `ruff format` + `mypy` + `pytest` (backend) + `lint` + `tsc`
      + `build` (frontend) verdes.
- [ ] `CLAUDE.md` + `docs/PLAN.md` (mover de "sección nueva propuesta" a
      estado implementado) — pendiente hasta cerrar la spec.
- [ ] Probar contra un tenant real (preferentemente `lab`/lab si ya
      tiene URL nueva, si no Globex): confirmar que el bearer ISPSS sirve
      tal cual para `UserMgmt/*` (R8) y bloquear/desbloquear un usuario de
      prueba de punta a punta. **No verificado visualmente en el browser
      ninguna de las tres sesiones de hoy** (la extensión Claude en Chrome
      no conectó) — sumar a esta verificación.

## Decisiones abiertas

1. ~~¿El bearer ISPSS alcanza, o Identity exige un scope/rol de API
   distinto para escritura?~~ **Resuelto (2026-09-15):** alcanza sin
   cambios — Alex confirmó `UserMgmt/*` funcionando contra un tenant real
   con el mismo bearer que ya usa `redrock_query`.
2. **¿`identity-users` vive en un router nuevo (`app/api/identity.py`) o
   dentro de `inventory.py`?** Propongo router nuevo — es un dominio
   distinto (Identity, no Vault), evita que `inventory.py` siga creciendo.
3. **SCIM API como alternativa a futuro.** Anotado en la investigación,
   sin decisión — posible spec propia si se ve un caso de uso concreto
   (por ejemplo, si algún endpoint `/API/...` clásico resulta frágil).
