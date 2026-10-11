# filtros-server-cuentas

- **Estado:** hecha (probada en el server 2026-09-10; incl. Safe con espacios)
- **Roadmap:** Fase 1, ítem "filtro por columna en modo servidor
  (Cuentas/Safes)"
- **Fecha:** 2026-09-10

## Problema

El tab Cuentas está en modo servidor (paginado de a 100 contra CyberArk).
Su único control es el buscador libre (`search`), que en la API Gen2 busca
en `userName` / `address` / `name` — **no** por Safe ni por antigüedad. Una
pregunta muy común de operación ("las cuentas del Safe X", "las modificadas
en la última semana") hoy no se puede responder sin traer todo.

## Alcance

**Entra** — sólo lo que la API Gen2 de CyberArk soporta server-side en
`GET /API/Accounts` vía el parámetro `filter`:

- **Filtro por Safe** (exacto): `filter=safeName eq <safe>`.
- **Filtro por fecha de modificación**: `filter=modificationTime gt <epoch>`
  (y opcionalmente `lt`).
- Se combinan entre sí (`AND`) y conviven con `search` y `sort`.
- UI en el tab Cuentas: un **select de Safes del tenant** + un input de
  fecha "modificadas desde". Ambos opcionales; limpiarlos vuelve a "todas".

**Fuera (explícito):**
- Filtro por `platformId`, `userName`, `address`, `status`, etc. **La API no
  lo permite.** Para eso hace falta el *sync de inventario a Postgres* (otra
  spec, más grande) — queda anotado ahí.
- **Safes**: `GET /API/Safes` sólo acepta `search` (nombre) y `sort`, sin
  `filter` útil. El tab Safes **no cambia** en esta spec.
- Filtro por columna genérico en la grilla en modo servidor (la fila de
  inputs bajo los headers sigue siendo sólo de modo cliente).

## Requisitos

### R1 — Filtro por Safe en Cuentas

`GET /tenants/{tenant_id}/accounts?safe_name=<safe>` agrega
`filter=safeName eq <safe>` a la llamada a CyberArk. Rol `viewer`, auditado
igual que hoy (`account.list`), el `detail` incluye los filtros aplicados.

**Escenario:**
Dado el tenant Globex con 1894 cuentas y 60 en el Safe `SQL-PROD`,
Cuando el operador elige `SQL-PROD` en el select de Safe,
Entonces la grilla muestra 60 (el `count` del backend = 60) y la paginación
se recalcula; el audit registra `account.list` con
`detail.filters = {"safe_name": "SQL-PROD"}`.

### R2 — Filtro por fecha de modificación

`GET /tenants/{tenant_id}/accounts?modified_after=<epoch_seg>` agrega
`filter=modificationTime gt <epoch>`. Si además hay `safe_name`, se combinan
con `AND`.

**Escenario:**
Dado que el operador pone "modificadas desde" = hace 7 días,
Cuando aplica,
Entonces sólo quedan las cuentas con `secretManagement.lastModifiedTime`
posterior a ese epoch.

### R3 — Lista de Safes para el select

`GET /tenants/{tenant_id}/safe-names` → `{"tenant", "names": [...]}`
(ordenado). Trae la lista completa de Safes del tenant (`client.list_safes()`,
ya se usa en export/dashboard). Rol `viewer`, auditado `safe.list`.

El select del tab Cuentas se puebla con esto. Si el tenant tiene demasiados
Safes para un select cómodo (> ~200) el componente permite tipear para
filtrar las opciones (combobox), no un `<select>` plano.

### R4 — Combinación y reset

- Los filtros (`safe_name`, `modified_after`), `search` y `sort` son
  independientes y se combinan.
- Cambiar cualquier filtro **resetea `offset` a 0**.
- La barra de estado muestra el `count` real que devuelve CyberArk para la
  combinación aplicada.
- "Limpiar" (o dejar el select en "Todos los Safes" y borrar la fecha)
  vuelve al listado completo.

## Diseño técnico

### Backend

- `app/cyberark/client.py` — `accounts_page(...)` suma kwargs
  `safe_name: str | None`, `modified_after: int | None`:
  ```python
  parts = []
  if safe_name:
      parts.append(f"safeName eq {safe_name}")
  if modified_after:
      parts.append(f"modificationTime gt {modified_after}")
  if parts:
      params["filter"] = " AND ".join(parts)
  ```
- `app/api/inventory.py` — `/accounts` suma `safe_name: str | None = None` y
  `modified_after: int | None = Query(None, ge=0)`; los pasa a
  `accounts_page` y los mete en el `detail` del audit
  (`{"filters": {...}}` cuando hay alguno).
- `app/api/inventory.py` — ruta nueva `GET /{tenant_id}/safe-names`:
  `client.list_safes()` → `sorted({s["safeName"] for s in rows if s.get("safeName")})`.
  Auditada con `_run(... "safe.list" ...)`.

### Frontend

- `lib/types.ts` — `SafeNamesResponse { tenant; names: string[] }`.
- `pages/Inventory.tsx`:
  - `useServerGrid` acepta un arg `extra?: Record<string, string | undefined>`;
    lo serializa dentro de `params` y, con un `useEffect` sobre su versión
    serializada, resetea `offset` a 0.
  - `AccountsTab`:
    - `useQuery` de `/tenants/{id}/safe-names`.
    - estado `safeName` y `modifiedAfter` (Date → epoch al vuelo).
    - `const grid = useServerGrid({ safe_name: safeName || undefined,
      modified_after: modifiedAfter ? String(epoch) : undefined });`
    - render de los controles (select de Safe + input `type="date"` +
      botón "Limpiar" si hay alguno activo) en el `toolbar` del `DataTable`,
      junto al `ExportCsvButton`.
- Componente `SafeSelect` (o reutilizar el `Select` de shadcn con búsqueda si
  hace falta): opción "Todos los Safes" + una por nombre.
- **El `DataTable` y su `ServerMode` no cambian** — los filtros viven en
  `AccountsTab` + `useServerGrid`.

### Principios

- Read-only, `GET`. Aislamiento por tenant. Todo auditado; el audit de
  `account.list` ahora dice qué filtros se aplicaron.
- El `export` de Cuentas sigue siendo "todo el tenant" — no toma estos
  filtros (es deliberado; ver `export-csv-pdf`).

## Tareas

- [x] Backend: `accounts_filter(safe_name, modified_after)` (helper puro) +
      `accounts_page` lo usa para el parámetro `filter`.
- [x] Backend: `/accounts` acepta `safe_name` / `modified_after` y los mete
      en `detail.filters` del audit (vía `_run(..., extra=...)`); ruta
      `GET /{tenant_id}/safe-names` (auditada `safe.list`).
- [x] Backend: `tests/unit/test_cyberark_client.py` (armado del `filter`).
- [x] Backend: `ruff` + `mypy` + `pytest` (39) verdes.
- [x] Frontend: `useServerGrid(extra?)` + reset de offset al cambiar `extra`;
      `AccountsTab` con `<Select>` de Safe + input `type="date"` "modificadas
      desde" + "Limpiar", en el `toolbar` del `DataTable`. `SafeNamesResponse`
      en types.
- [x] Frontend: `lint` + `tsc` + `build` verdes.
- [x] `CLAUDE.md` + `docs/PLAN.md` actualizados.
- [x] Probado en el server (Globex, 2026-09-10): Safe (incl. con espacios
      en el nombre), combinado con `search` y `sort`, y `modified_after`.

## Notas de cierre

- El select de Safe usa el sentinel `"__all"` (Radix Select no admite
  `value=""`).
- `modified_after` se manda en segundos (epoch). El input `type="date"` da
  medianoche local del día elegido.
- **Safe con espacios: OK.** `filter=safeName eq Mi Safe` sin comillas
  funcionó contra Globex (httpx URL-encodea el valor y CyberArk lo
  interpreta bien). No hizo falta tocar `accounts_filter`.

## Notas de implementación

- **Safes con espacios en el nombre**: la sintaxis de `filter` de CyberArk
  toma el valor "hasta el próximo operador", sin comillas. Un Safe llamado
  `SQL PROD` puede romper el parseo. Verificar contra Globex; si falla,
  probar con comillas (`safeName eq "SQL PROD"`) o URL-encode explícito, y
  documentar el resultado acá.
- `modificationTime` en el `filter` es sobre el timestamp de modificación de
  la cuenta (no el de la contraseña). Coincide con lo que el operador espera
  como "modificadas".
- Si `GET /API/Accounts` devuelve 400 por un `filter` inválido, el handler
  ya lo mapea a 502 con el texto del error (patrón `_run`); en el front se
  muestra como error del tab. No hace falta manejo especial.
