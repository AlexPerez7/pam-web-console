# vault-users-groups-crud — CRUD de usuarios y grupos del Vault

- **Estado:** implementada (backend), **UI removida el 2026-09-24** — ver
  "Estado 2026-09-24" más abajo
- **Roadmap:** `docs/PLAN.md §7` Fase 3, ítem "CRUD de usuarios y grupos
  del Vault"
- **Fecha:** 2026-09-14

## Estado 2026-09-24

Alex aclaró que "Users and Groups" del Vault es un concepto de **PAM
Self-Hosted**, no de Privilege Cloud — el único deployment que soporta
PAM Web Console v1 (`Tenant.deployment` sólo admite `privilege_cloud` hoy). En
Privilege Cloud la administración de usuarios es 100% vía Identity
(`/sso-users`, `app/api/identity.py`); no hay ningún caso de uso real
para este CRUD contra Acme/Initech/Globex/Umbrella/el MSP.

Se sacó **todo el frontend** de esta feature (páginas `Users.tsx`/
`Groups.tsx`, sus rutas, el link "Ver en inventario" de esos dos
recursos en `Search.tsx`, las ramas de usuario/grupo Vault en
`DetailDrawer.tsx`, los 9 componentes de escritura y los tipos/util que
quedaron huérfanos — ver `CLAUDE.md` "Sacado del frontend (2026-09-24)"
para el detalle completo). **El backend queda intacto** (los 8
endpoints en `app/api/writes.py` + `app/domain/users.py` + sus tests) —
a pedido de Alex, por si se retoma PAM Self-Hosted más adelante. Nunca
se probó contra un tenant real; con la UI removida, no hay urgencia de
hacerlo salvo que Self-Hosted se desparquee.

Sigue sin tocarse la búsqueda de miembros para agregar a una safe
(`app/domain/member_search.py`, `SafeMemberAddSection.tsx`) — es una
feature distinta (agregar un principal EXISTENTE como miembro de una
safe, no administrar altas/bajas de usuarios) que sigue en uso y
probada.

## Problema

PAM Web Console hoy puede leer usuarios y grupos del Vault (inventario + miembros de
grupo, Fase 1) y reactivar un usuario suspendido por login fallido
(`user.activate`, Fase 2), pero no puede crear, editar ni borrar usuarios
o grupos, ni gestionar la membresía de un grupo — todo eso sigue siendo
manual en el PVWA clásico.

## Alcance

**Entra**, ocho operaciones nuevas:

| Operación | Feature | RBAC | Confirmación reforzada |
|---|---|---|---|
| Crear usuario | `user.create` | `operator+` | No |
| Editar usuario | `user.update` | `operator+` | No |
| Borrar usuario | `user.delete` | `approver+` | Sí (`confirm` = `username`) |
| Crear grupo | `group.create` | `operator+` | No |
| Renombrar grupo | `group.update` | `operator+` | No |
| Borrar grupo | `group.delete` | `approver+` | Sí (`confirm` = `groupName`) |
| Agregar miembro a grupo | `group.member.add` | `operator+` | No |
| Quitar miembro de grupo | `group.member.remove` | `approver+` | Sí (`confirm` = nombre del miembro) |

Mismo criterio de riesgo que ya usa PAM Web Console: crear es aditivo/reversible
(`operator+`, sin confirmación — igual que `account.create`/
`safe.create`/`platform.duplicate`); **borrar** un usuario o grupo, y
**quitar** un miembro, tocan acceso de terceros — mismo techo que
`safe.member.remove`/`account.delete`/`platform.delete` (`approver+` +
confirmación reforzada). Editar/renombrar/agregar quedan en `operator+`
sin confirmación, igual que el resto de Fase 2.

**Alcance de campos v1 (curado, no todo lo que expone la API):**

- **Usuario** — crear/editar: `userName` (sólo alta), `initialPassword`
  (sólo alta, nunca en diff/audit — mismo criterio que el secreto de
  `account.create`), `email`, `firstName`, `lastName`,
  `changePasswordOnTheNextLogon` (default `true` al crear). **Fuera de
  v1:** `vaultAuthorization` (roles de administración de Vault — alto
  riesgo, requiere su propia revisión), datos de contacto extendidos
  (dirección, teléfonos), `userType`, `expiryDate`, deshabilitar un
  usuario a mano (distinto de reactivar uno suspendido, que ya existe).
- **Grupo** — crear: `groupName`, `description`, `location`. Editar:
  **sólo el nombre** — es lo único que expone `Update Group` según la API
  verificada (ver Investigación).
- **Miembro de grupo** — `memberId` (nombre de usuario o grupo),
  `memberType` (`vault` | `domain`, default `vault`), `domainName`
  (sólo si `memberType=domain`). Reusa el buscador `/member-search` que ya
  existe para `safe.member.add` (Vault + Identity, dedup).

**Fuera:**
- CRUD en lote.
- `vaultAuthorization` (roles de administración) — ver arriba.
- Borrar un grupo o usuario no valida que no tenga membresías activas en
  otras safes/grupos (ver "Decisiones abiertas" — gap conocido, documentado
  a propósito en vez de hacer un escaneo caro).

## Investigación: endpoints

`docs.cyberark.com` bloqueó el fetch automatizado también para estas
páginas (404 incluso con user-agent de navegador). Verificado contra el
código fuente del módulo open-source `psPAS`
(`psPAS/Functions/User/*.ps1` — la carpeta se llama `User` pero incluye
también los grupos):

```
POST   /api/Users                              New-PASUser (Gen2)
PUT    /api/Users/{id}                         Set-PASUser (Gen2, requiere id + username)
DELETE /api/Users/{id}                         Remove-PASUser (Gen2)

POST   /API/UserGroups                         New-PASGroup — {groupName, description, location}
PUT    /API/UserGroups/{id}                    Set-PASGroup — SÓLO {GroupName}
DELETE /API/UserGroups/{id}                    Remove-PASGroup
POST   /API/UserGroups/{id}/Members            Add-PASGroupMember — {memberId, memberType, domainName}
DELETE /API/UserGroups/{id}/members/{member}   Remove-PASGroupMember
```

- Todos requieren el **ID numérico interno** del usuario/grupo (no el
  username/groupName) salvo `Add`/`Remove Group Member`, que usan el
  `groupId` numérico en la URL pero el `memberId` como **nombre**.
- `Update User` (`Set-PASUser`) es un archivo enorme en `psPAS` (más de
  40 parámetros posibles: dirección, teléfonos, roles de Vault, tipo de
  usuario, etc.) — v1 se queda con el subconjunto de arriba, igual
  criterio que "Additional Properties" en `account.create` (no se
  implementa todo lo que la API permite, sólo lo que se necesita hoy).
- **No confirmado si `Update User`/`Update Group` aceptan un PUT parcial
  o exigen el objeto completo** (a diferencia de `Update Account`, que sí
  está documentado como patch parcial vía JSON Patch, y `Update Safe`,
  confirmado que exige el objeto completo). Por las dudas, el diseño
  hace **GET del usuario/grupo actual → mergea sólo los campos
  editables → manda el objeto completo** en el `PUT` — es el camino
  seguro sea cual sea el comportamiento real de la API. Se verifica en
  la prueba contra Globex.
- Fuente ya usada por Alex antes de esta sesión para verificar endpoints
  bloqueados en `docs.cyberark.com`: el propio código de `psPAS`
  (mismo criterio que `account-password-manual.md` y
  `platform-lifecycle.md`).

## Requisitos

1. **Crear usuario.** Con `userName` + `initialPassword` (mínimo), se
   crea el usuario; el resto de los campos es opcional. El
   `initialPassword` nunca aparece en el diff del dry-run ni en el audit.
2. **Editar usuario.** Cambiar `email`/`firstName`/`lastName`/
   `changePasswordOnTheNextLogon` de un usuario existente, sin tocar
   `userName` ni la contraseña.
3. **Borrar usuario.** Requiere `confirm` = el `userName` exacto.
4. **Crear grupo.** Con `groupName` (mínimo).
5. **Renombrar grupo.** Sólo cambia `groupName`; cualquier otro campo
   pedido se ignora (la API no lo soporta).
6. **Borrar grupo.** Requiere `confirm` = el `groupName` exacto.
7. **Agregar miembro a un grupo.** Buscador reusado de
   `safe.member.add`; agrega sin confirmación reforzada.
8. **Quitar miembro de un grupo.** Requiere `confirm` = el nombre del
   miembro.

## Diseño técnico

- `backend/app/cyberark/client.py`: `create_user(body)`, `update_user(id,
  body)`, `delete_user(id)`, `create_group(body)`, `update_group(id,
  body)`, `delete_group(id)`, `add_group_member(group_id, body)`,
  `remove_group_member(group_id, member)`. `retry_safe=False` en los
  `POST` de creación (no idempotentes), igual criterio que el resto.
- `backend/app/domain/users.py`: agrega `build_user_create_body`/
  `plan_user_create`, `build_user_update_plan` (edición: diff de los
  campos curados, el `initialPassword` nunca viaja en `changes` de
  update — sólo existe en create), `build_user_delete_plan`
  (`confirm_phrase = userName`).
- `backend/app/domain/groups.py` (nuevo): `build_group_create_body`/
  `plan_group_create`, `build_group_update_plan` (sólo `groupName`),
  `build_group_delete_plan` (`confirm_phrase = groupName`),
  `group_member_body` (memberId/memberType/domainName),
  `build_group_member_remove_plan` (`confirm_phrase` = el nombre del
  miembro, resuelto por el `fetch` del endpoint — mismo patrón que
  `find_member` de `safes.py`).
- `backend/app/api/writes.py`: 8 endpoints nuevos bajo
  `/tenants/{id}/users` y `/tenants/{id}/groups`. `user.update`/
  `group.update`: el `fetch` trae el objeto actual completo
  (`client.get_user`/nuevo `client.get_group`) y el `apply` manda ese
  objeto mergeado con los campos editados (ver nota de "PUT completo").
- `backend/app/core/features.py` + `frontend/src/pages/Tenants.tsx`: las
  8 features nuevas en ambas listas (gotcha ya conocido).
- Frontend: `UserCreateDialog.tsx`/`UserEditSection.tsx`/
  `UserDeleteSection.tsx` en el detalle de usuario (`DetailDrawer.tsx`
  gana las secciones, igual patrón que Platforms); `GroupCreateDialog.tsx`/
  `GroupEditSection.tsx`/`GroupDeleteSection.tsx`/`GroupMemberAddSection.tsx`
  (reusa el buscador de `SafeMemberAddSection`)/`GroupMemberRow.tsx` (botón
  "Quitar", mismo patrón que `SafeMemberRow`) en el detalle de grupo.

## Decisiones abiertas (documentadas, no bloquean)

1. **Borrar usuario/grupo no valida membresías activas.** A diferencia de
   `safe.delete` (bloquea si tiene cuentas) o `platform.delete` (bloquea
   si tiene cuentas), acá no hay un endpoint barato para listar "todas
   las safes/grupos donde este usuario es miembro" — escanearlo
   significaría recorrer todas las safes del tenant. Se documenta como
   gap conocido; CyberArk igual puede rechazar el borrado del lado del
   servidor si el usuario tiene dependencias.
2. **`vaultAuthorization` (roles de administración de Vault) queda fuera
   de v1** — es lo más sensible de "Update User" (puede otorgar
   `AddSafes`, `ManageUsers`, etc.) y merece su propia spec con más
   cuidado de RBAC/confirmación, no colarse como un campo más del CRUD
   básico.

## Tareas

- [x] `client.py`: 8 métodos nuevos (+ `get_group` si no existe ya un
      equivalente reusable de `user_group_members`).
- [x] `domain/users.py`: create/update/delete de usuario.
- [x] `domain/groups.py` (nuevo): create/update/delete de grupo +
      add/remove miembro.
- [x] `writes.py`: 8 endpoints.
- [x] `features.py` + `Tenants.tsx`: 8 features en ambas listas.
- [x] Frontend: diálogos/secciones de usuario y grupo.
- [x] Tests unit: las 8 operaciones (dry-run, confirm, RBAC, feature-flag,
      el `initialPassword` nunca en diff/audit).
- [x] `ruff` + `mypy` + `pytest` + `lint` + `tsc` + `build` verdes.
- [ ] Probar contra Globex antes de cerrar — en particular confirmar si
      `Update User`/`Update Group` aceptan PUT parcial o exigen el
      objeto completo (ver Investigación).
