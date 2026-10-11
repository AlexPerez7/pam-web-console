# inventario-completo

- **Estado:** hecha (probada en el server 2026-09-10)
- **Roadmap:** Fase 1, ítem "Inventario navegable en vivo completo: … Sesiones, Componentes … Safes (+ members)"
- **Fecha:** 2026-09-09

## Problema

El inventario cubre Cuentas / Safes / Plataformas / Usuarios. Faltan tres
recursos para que el read-only de Fase 1 quede completo:

- **Sesiones PSM activas** — quién está conectado ahora mismo a qué target.
- **Salud de componentes** por instancia — hoy solo hay el resumen agregado
  en el dashboard.
- **Miembros de un Safe** — quién tiene qué permisos sobre cada Safe.

## Alcance

**Entra:**
- Tab **Sesiones** — `GET /API/LiveSessions` (sesiones PSM en curso).
- Tab **Componentes** — una fila por instancia de PVWA / PSM / CPM / AIM.
- **Miembros de Safe** dentro del panel de detalle del Safe (no un tab
  aparte — no existe endpoint de "todos los miembros", es 1 request por
  Safe).
- Manejo de permiso faltante: si el Service User no puede leer alguno, el
  tab/sección muestra el motivo y el resto del inventario sigue.

**Fuera (otra spec):**
- Grabaciones de sesión (`/API/Recordings`) y reproducción — Fase 4.
- Terminar una sesión (escritura) — Fase 3.
- Editar miembros de Safe (escritura) — Fase 2/3.
- Grupos del Vault como tab propio (los usuarios ya listan; los grupos
  llegan con RBAC).

## Requisitos

### R1 — Sesiones PSM activas

`GET /tenants/{tenant_id}/sessions` → `GET /API/LiveSessions` (paginado).
Rol `viewer`. Auditado `session.list`.

Respuesta: `{"tenant", "count", "sessions": [ {raw} ]}`.

Tab "Sesiones": tabla con usuario, target (`RemoteMachine` / cuenta),
protocolo, inicio (epoch → fecha/hora), duración, estado.

**Escenario:**
Dado que hay 2 sesiones RDP activas en Globex,
Cuando el operador abre el tab Sesiones,
Entonces ve 2 filas con el usuario y el equipo destino de cada una.

**Escenario (sin permiso):**
Dado que el Service User no tiene permiso de Session Monitoring,
Cuando abre el tab,
Entonces ve "No se pudo leer las sesiones: … 403 …" y los demás tabs
funcionan.

### R2 — Componentes por instancia

`GET /tenants/{tenant_id}/components` → `GET
/API/ComponentsMonitoringDetails/{id}` para `PVWA`, `SessionManagement`,
`CPM`, `AIM` en paralelo (404 = "no aplica"). Rol `viewer`. Auditado
`component.list`.

Respuesta: `{"tenant", "components": [ {component, id, ip, version,
connected, last_connection, os} ]}` — una fila por instancia; los
componentes que no aplican no aportan filas.

Tab "Componentes": tabla con componente, IP, versión, conectado (badge),
última conexión.

### R3 — Miembros de un Safe (en el detalle)

`GET /tenants/{tenant_id}/safes/{safe_id}/members` → `GET
/API/Safes/{safeUrlId}/Members/` (paginado). Rol `viewer`. Auditado
`safe.members` con `target = safe_id`.

Respuesta: `{"tenant", "safe_id", "members": [ {memberName, memberType,
permissions, membershipExpirationDate, isPredefinedUser} ]}`.

El `DetailDrawer` de un Safe (al clickear una fila del tab Safes) suma una
sección "Miembros" con: nombre, tipo (User/Group), y los permisos clave
(list / use / retrieve / manageSafe / manageSafeMembers) como chips o
íconos, más el vencimiento si tiene.

**Escenario:**
Dado el Safe `SQL-PROD` con 3 miembros (2 grupos, 1 usuario),
Cuando el operador abre su detalle,
Entonces la sección "Miembros" lista los 3 con sus permisos.

## Diseño técnico

**Backend**
- `app/cyberark/client.py`:
  - `live_sessions()` → `get_all("/API/LiveSessions", key="LiveSessions")`.
  - `safe_members(safe_id)` → `get_all("/API/Safes/{safe_id}/Members/",
    key="value", page_size=25)`.
  - (`component_details` ya existe, de dashboard-v2.)
- `app/api/inventory.py`: 3 rutas nuevas con el patrón `_run(...)`
  (auditan ok/error; `safe.members` pasa `target=safe_id`). Componentes:
  `asyncio.gather` de los 4, mapeo a filas planas.
- Sin cambios de modelo ni migración.

**Frontend**
- `lib/types.ts`: `LiveSession`, `SessionsResponse`, `ComponentRow`,
  `ComponentsResponse`, `SafeMember`, `SafeMembersResponse`.
- `pages/Inventory.tsx`: tabs "Sesiones" y "Componentes" (mismo patrón
  `DataTable` que los demás; no son clickeables — sin detalle por ahora).
- `components/DetailDrawer.tsx`: prop opcional `safeId` → `useQuery` de
  miembros + sección "Miembros".
- `SafesTab` pasa `safeId={sel?.safeUrlId}` al `DetailDrawer`.

**Principios**
- Read-only, `GET` únicamente. Aislamiento por tenant. Todo auditado; los
  miembros de Safe con `target` para el rastro por objeto.

## Tareas

- [x] Backend: `live_sessions` + `safe_members` en `client.py`;
      `component_details` ahora tolera 403 (además de 404).
- [x] Backend: rutas `/sessions`, `/components`, `/safes/{id}/members` en
      `inventory.py`, auditadas (`safe.members` con `target`).
- [x] Backend: `py_compile`.
- [x] Frontend: tipos + tabs Sesiones/Componentes + sección "Miembros" en el
      `DetailDrawer` de Safes.
- [x] Frontend: `npx tsc --noEmit` + `npm run build` verdes.
- [x] `CLAUDE.md` + `docs/PLAN.md`.
- [x] Probar en el server (Globex / Acme), mover a `docs/specs/done/`.
      (2026-09-10)

## Notas de implementación

- `LiveSessions` usa params capitalizados (`Limit`) — un solo GET con
  `Limit=1000`, no `get_all` (rara vez hay tantas sesiones activas).
- Componentes: `asyncio.gather` de los 4; los que dan 404/403 no aportan
  filas. Tab vacío → mensaje ("puede no tener permiso de monitoreo").
- Sesiones vacío → "No hay sesiones PSM activas".
