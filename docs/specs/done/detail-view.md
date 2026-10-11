# detail-view

- **Estado:** hecha (probada en el server 2026-09-09)
- **Roadmap:** Fase 1, ítem "Vista de detalle de cada objeto con su historial/actividad"
- **Fecha:** 2026-09-09

## Problema

El inventario muestra las filas con 5-6 columnas, pero un objeto de CyberArk
(cuenta, safe, plataforma, usuario) tiene decenas de propiedades. Hoy no hay
forma de ver el objeto completo ni su historia sin salir a la API a mano.
Para operar (y para el paso a Fase 2, donde habrá que confirmar cambios) se
necesita ver el detalle y qué pasó con ese objeto.

## Alcance

**Entra:**
- Click en cualquier fila del inventario → panel lateral (Drawer) con el
  objeto completo, formateado.
- Para **cuentas**, además: actividad del objeto en CyberArk
  (`/API/Accounts/{id}/Activities`) y su rastro en el audit log de PAM Web Console.
- Filtros nuevos en `GET /audit` para poder traer "lo que pasó con este
  objeto".

**Fuera (otra spec / fase):**
- Actividad de safes / plataformas / usuarios en CyberArk (esos objetos no
  tienen un endpoint de "activities" equivalente; su detalle v1 es solo el
  objeto crudo).
- Edición desde el detalle (Fase 2).
- Links a sesiones PSM del objeto (depende del tab de Sesiones, otra spec).

## Requisitos

### R1 — Drawer de detalle genérico

Al hacer click en una fila de cualquier tab del inventario se abre un
`Drawer` (lateral derecho) que muestra **todas** las propiedades del objeto
ya cargado en la grilla, agrupadas: valores primitivos como `clave: valor`;
los sub-objetos (ej. `secretManagement`, `general`) como una sección con sus
campos; los arrays como lista o conteo. Sin fetch extra para esto (la grilla
ya tiene el objeto).

**Escenario:**
Dado el tab "Cuentas" de Globex,
Cuando el operador hace click en la fila `svc-sql`,
Entonces se abre un panel con el nombre arriba y todas las propiedades de esa
cuenta (id, address, platformId, safeName, `secretManagement.*`,
`platformAccountProperties.*`, createdTime, etc.).

### R2 — Endpoint de detalle de cuenta (fresco)

`GET /tenants/{tenant_id}/accounts/{account_id}` — trae la cuenta desde
CyberArk (`GET /API/Accounts/{id}`), por si cambió desde que se cargó la
grilla. Rol `viewer`. Auditado: `action = "account.get"`, `target =
account_id`, `result` ok/error.

El Drawer de una cuenta usa esta respuesta (más completa y actual) en lugar
del objeto de la lista.

### R3 — Actividad de la cuenta en CyberArk

`GET /tenants/{tenant_id}/accounts/{account_id}/activities` — proxy a
`GET /API/Accounts/{id}/Activities`. Rol `viewer`. Auditado:
`action = "account.activities"`, `target = account_id`.

Respuesta: `{"account_id": "...", "activities": [ {date, action, reason, user, client_type, ...} ]}`
(parseo defensivo: la API devuelve `Activities` o `value`).

**Escenario:**
Dado que la cuenta `svc-sql` tuvo un CPM change ayer,
Cuando el operador abre su detalle,
Entonces en la sección "Actividad en CyberArk" ve esa entrada con fecha,
acción y usuario.

### R4 — Rastro del objeto en el audit log de PAM Web Console

`GET /audit` acepta filtros opcionales nuevos: `tenant_id`, `target`,
`action` (además del `limit` actual). Se combinan con AND.

El Drawer de una cuenta muestra la sección "Actividad en PAM Web Console" con
`GET /audit?tenant_id={t}&target={account_id}` — quién de PAM Web Console miró o tocó
esa cuenta, y cuándo.

**Escenario:**
Dado que el operador `jperez` abrió el detalle de `svc-sql` dos veces hoy,
Cuando cualquier operador con rol `viewer` abre ese detalle,
Entonces la sección "Actividad en PAM Web Console" lista esos dos accesos
(`account.get`) atribuidos a `jperez`.

Nota: hoy las lecturas de lista (`account.list`, etc.) **no** setean
`target`, así que el rastro por objeto arranca con las acciones de detalle de
esta spec y crecerá con las de Fase 2. No se cambia el set de campos de
`audit.record` — solo se empieza a pasar `target` donde aplica.

### R5 — Detalle de safe / plataforma / usuario

Mismo Drawer de R1 (objeto crudo de la grilla), **sin** las secciones de
actividad. Sin endpoints nuevos en v1.

## Diseño técnico

**Backend**
- `app/cyberark/client.py`: agregar `get_account(account_id)` y
  `account_activities(account_id)`.
- `app/api/inventory.py`: agregar las dos rutas de R2 y R3, con el mismo
  patrón `_run(...)` (audita ok/error) pero pasando `target=account_id`.
- `app/api/audit.py`: `recent()` acepta `tenant_id`, `target`, `action`
  opcionales y los aplica como `where` sobre el `select(AuditLog)`.

**Frontend**
- `components/DataTable.tsx`: prop opcional `onRowClick?: (row: T) => void`;
  si está, las filas toman `cursor: pointer` y disparan el callback.
- `components/DetailDrawer.tsx`: `Drawer` que recibe `{ title, object }` y lo
  renderiza recursivo/agrupado. Si recibe además `{ tenant, accountId }`,
  monta dos `useQuery` (activities y audit) y sus secciones.
- `pages/Inventory.tsx`: cada tab tiene `const [sel, setSel] = useState`; pasa
  `onRowClick={setSel}` al `DataTable` y renderiza `<DetailDrawer>`.
  Para el tab de Cuentas, el Drawer recibe `accountId` y `tenant`.
- Tipos en `types.ts`: `AccountActivity`, `AccountActivitiesResponse`.

**Principios**
- Read-only, `GET` únicamente.
- Aislamiento por tenant (usa `tenant_client`, cliente nuevo por request).
- Todo auditado, con `target` por primera vez para poder reconstruir la
  historia por objeto.

## Tareas

- [x] Backend: `get_account` + `account_activities` en `client.py`.
- [x] Backend: rutas `GET /tenants/{id}/accounts/{account_id}` y `/activities`
      en `inventory.py`, auditadas con `target` (`_run` acepta `target=`).
- [x] Backend: filtros `tenant_id` / `target` / `action` en `GET /audit`.
- [x] Backend: `py_compile`.
- [x] Frontend: `onRowClick` en `DataTable`.
- [x] Frontend: `components/DetailDrawer.tsx`.
- [x] Frontend: wiring en `pages/Inventory.tsx` (los 4 tabs) + tipos.
- [x] `CLAUDE.md` + `docs/PLAN.md` actualizados.
- [ ] Probar en el server, mover esta spec a `docs/specs/done/`.

## Notas de implementación

- El Drawer muestra el objeto de la grilla de inmediato y, para cuentas, lo
  reemplaza por el `GET /accounts/{id}` fresco cuando llega.
- Objetos anidados se muestran como sub-secciones; los arrays, como JSON en
  un bloque de código.
- Las claves que terminan en `time`/`date` con valor numérico se muestran
  con la fecha formateada al lado.
