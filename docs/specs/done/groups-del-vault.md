# groups-del-vault

- **Estado:** hecha (probada en el server 2026-09-10)
- **Roadmap:** Fase 1, ítem "Inventario … Falta: Groups del Vault"
- **Fecha:** 2026-09-10

## Problema

El inventario lista usuarios del Vault pero no los **grupos**. Los grupos son
la unidad real de asignación de permisos en CyberArk (los miembros de un Safe
suelen ser grupos, no usuarios sueltos), así que hoy, cuando en el detalle de
un Safe aparece un grupo, no hay forma de ver quién está adentro sin entrar a
PVWA. Es el último recurso de solo lectura que le falta a la Fase 1.

## Alcance

**Entra:**
- Tab **Grupos** en el inventario por tenant — grupos del Vault (`Vault`) y
  de directorio (`Directory`), con tipo, descripción y directorio de origen.
- **Miembros de un grupo** en el panel de detalle (mismo patrón que los
  miembros de Safe: un request por grupo, no hay endpoint de "todos").
- **Búsqueda cross-tenant** de grupos (`GET /search/groups`).
- Manejo de permiso faltante: si el Service User no tiene "Audit users",
  el tab muestra el motivo y el resto del inventario sigue.

**Fuera (otra spec / otra fase):**
- CRUD de grupos y de miembros (escritura) — Fase 3.
- Grupos de Identity / roles de ISPSS — sólo grupos que expone la API Gen2
  de PVWA (`/API/UserGroups`).
- Enriquecer el detalle de Safe resolviendo cada grupo a sus miembros
  automáticamente — por ahora el operador abre el grupo desde su tab.

## Requisitos

### R1 — Listar grupos del Vault

`GET /tenants/{tenant_id}/groups` → `GET /API/UserGroups`. Rol `viewer`.
Auditado `group.list`.

Respuesta: `{"tenant", "count", "groups": [ {raw} ]}`. Campos crudos de la
API: `id`, `groupName`, `groupType` (`Vault` | `Directory`), `description`,
`directory`, `location`.

Tab "Grupos": tabla con nombre, tipo (badge Vault/Directory), directorio (si
aplica), descripción. Fila clickeable → detalle.

**Escenario:**
Dado que Globex tiene 40 grupos del Vault,
Cuando el operador abre el tab Grupos,
Entonces ve 40 filas con el nombre y el tipo de cada uno.

**Escenario (sin permiso):**
Dado que el Service User no tiene "Audit users",
Cuando abre el tab,
Entonces ve "No se pudo leer los grupos: … 403 …" y los demás tabs
funcionan.

### R2 — Miembros de un grupo (en el detalle)

`GET /tenants/{tenant_id}/groups/{group_id}/members` → `GET
/API/UserGroups/{group_id}` (trae el grupo con `members`). Rol `viewer`.
Auditado `group.members` con `target = group_id`.

Respuesta: `{"tenant", "group_id", "members": [ {memberId, memberName,
memberType} ]}` — normalizado desde el array `members` que devuelve la API.

El `DetailDrawer` de un grupo suma una sección "Miembros" con nombre y tipo
(User / Group). Si el grupo no tiene miembros → "Sin miembros".

**Escenario:**
Dado el grupo `PAM-Admins` con 3 usuarios,
Cuando el operador abre su detalle,
Entonces la sección "Miembros" lista los 3.

### R3 — Búsqueda cross-tenant de grupos

`GET /search/groups?q=` — abanica los tenants accesibles (mismo patrón que
`accounts`/`safes`/`platforms`/`users`), filtra por `groupName` /
`description` / `directory`. Auditado `group.search` por tenant.

**Escenario:**
Dado que `PAM-Admins` existe en Globex y en Acme,
Cuando el operador busca "pam-admins" en Grupos,
Entonces ve una Card por tenant con el grupo.

## Diseño técnico

**Backend**
- `app/cyberark/client.py`:
  - `list_user_groups()` → `get("/API/UserGroups")`, devuelve
    `data.get("value", [])` (la API responde sin paginar, como `/API/Users`;
    si algún tenant paginara, `get_all(..., key="value")`).
  - `user_group_members(group_id)` → `get(f"/API/UserGroups/{group_id}")`,
    devuelve el array `members` normalizado a
    `{memberId, memberName, memberType}` (los nombres de campo reales de la
    API varían: `id`/`memberId`, `userName`/`name`; normalizar acá).
- `app/api/inventory.py`: 2 rutas nuevas con el patrón `_run(...)`
  (`group.list`; `group.members` con `target=group_id`). Sin cambios de
  modelo ni migración.
- `app/api/search.py`: `Resource` suma `"groups"`; `_fetch` →
  `client.list_user_groups()`; `_matches` para `groups`
  (`groupName`/`description`/`directory`); `_ACTION["groups"] =
  "group.search"`.

**Frontend**
- `lib/types.ts`: `VaultGroup`, `GroupsResponse`, `GroupMember`,
  `GroupMembersResponse`; `SearchResource` suma `"groups"`.
- `pages/Inventory.tsx`: `<TabsTrigger value="groups">Grupos</TabsTrigger>` +
  `GroupsTab` (patrón `DataTable` + `DetailDrawer`, como `UsersTab`).
- `components/DetailDrawer.tsx`: prop opcional `groupId` → `useQuery` de
  miembros + sección "Miembros" (reutiliza el componente `Section` que ya
  usa `safeId`).
- `pages/Search.tsx`: opción "Grupos" en el selector de recurso + columnas.

**Principios**
- Read-only, `GET` únicamente. Aislamiento por tenant (cliente nuevo por
  request / por tenant en la búsqueda). Todo auditado; los miembros con
  `target` para el rastro por objeto.

## Tareas

- [x] Backend: `list_user_groups` + `user_group_members` en `client.py`
      (normaliza `members` a `{memberId, memberName, memberType}`).
- [x] Backend: rutas `/groups` y `/groups/{id}/members` en `inventory.py`,
      auditadas (`group.list`; `group.members` con `target=group_id`).
- [x] Backend: `groups` en `search.py` (`Resource`, `_fetch`, `_matches`,
      `_ACTION`).
- [x] Backend: `tests/unit/test_search.py` (`_matches` para groups);
      `mypy` + `ruff` + `pytest` (29 pass + 3 contract skip) verdes.
- [x] Frontend: tipos (`VaultGroup`, `GroupsResponse`, `GroupMember`,
      `GroupMembersResponse`) + tab "Grupos" (`GroupsTab`) + sección
      "Miembros" en el `DetailDrawer` (prop `groupId`) + opción "Grupos" en
      `Search.tsx`.
- [x] Frontend: `npm run lint` + `npx tsc --noEmit` + `npm run build`
      verdes.
- [x] `CLAUDE.md` (árbol + estado) + `docs/PLAN.md` (§7 Fase 1).
- [x] Probar en el server (Globex / Acme), mover a `docs/specs/done/`.
      (2026-09-10)

## Notas de implementación

- Permiso: `/API/UserGroups` usa el mismo "Audit users" que `/API/Users`; si
  la pestaña Usuarios funciona, Grupos también.
- `groupType`: `Vault` (grupo interno del Vault) vs `Directory` (grupo LDAP
  mapeado). Mostrar ambos; el badge distingue.
- El array `members` de `GET /API/UserGroups/{id}`: **confirmar los nombres
  de campo reales contra Globex** antes de dar por buena la normalización
  (la doc y el JSON real difieren, como pasó con `enableUser` en usuarios).
  `user_group_members()` prueba varios alias (`id`/`memberId`/`userId`,
  `username`/`userName`/`name`/`memberName`).
- El detalle sólo pide miembros si el grupo trae `id` numérico
  (`/API/UserGroups/{id}` lo necesita); si la lista no lo trae, la sección
  "Miembros" no aparece.
