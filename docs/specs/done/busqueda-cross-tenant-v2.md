# busqueda-cross-tenant-v2

- **Estado:** implementada — pendiente de probar en el server
- **Roadmap:** Fase 1, ítem "Búsqueda y filtros potentes; búsqueda cross-tenant"
- **Fecha:** 2026-09-09

## Problema

La búsqueda cross-tenant (`GET /search/accounts`) solo cubre **cuentas**. El
equipo también necesita "¿en qué cliente está este safe / esta plataforma /
este usuario?" sin ir cliente por cliente.

## Alcance

**Entra:**
- Extender la búsqueda a **safes**, **plataformas** y **usuarios**, con el
  mismo patrón (abanico en paralelo sobre los tenants accesibles, un tenant
  que falla no rompe la búsqueda, auditado por tenant).
- La UI gana un selector de tipo de recurso (Cuentas / Safes / Plataformas /
  Usuarios).

**Fuera:**
- Índice / caché de búsqueda — sigue siendo en vivo.
- Búsqueda combinada "todos los tipos a la vez".
- Buscar dentro de miembros de safe.

## Requisitos

### R1 — Endpoints por recurso

`GET /search/{resource}?q=` con `resource ∈ {accounts, safes, platforms, users}`.
Rol `viewer`; abanica solo los tenants accesibles + habilitados + Privilege
Cloud. `q` mínimo 2 caracteres.

Filtro por recurso (client-side sobre lo que trae CyberArk, salvo cuentas
que ya tienen `search` nativo):
- **accounts**: `client.list_accounts(search=q)` (como hoy).
- **safes**: `list_safes()` y filtrar por `safeName` / `description`.
- **platforms**: `list_platforms()` y filtrar por `general.id` / `general.name`.
- **users**: `list_users()` y filtrar por `username` / `personalDetails`.

Auditado por tenant: `account.search` / `safe.search` / `platform.search` /
`user.search` con `detail = {"q", "count"}`.

Respuesta unificada:
```json
{
  "query": "svc-sql",
  "resource": "safes",
  "results": [ {"tenant", "tenant_name", "count", "returned", "items": [...]} ],
  "errors": [ {"tenant", "tenant_name", "error"} ]
}
```
(`items` en vez de `accounts`; cap de 100 por tenant.)

**Escenario:**
Dado un safe `SQL-PROD` en Globex y `SQL-DR` en Acme,
Cuando el operador busca `resource=safes`, `q=SQL`,
Entonces la respuesta agrupa por tenant los safes que matchean.

### R2 — UI

`pages/Search.tsx`: un grupo de botones / tabs para el tipo de recurso
(Cuentas por defecto). Al cambiar de tipo, se re-ejecuta la búsqueda con el
término actual. Cada grupo de resultado muestra una tabla con columnas
apropiadas al recurso:
- safes: nombre, descripción, CPM
- platforms: id, nombre, tipo de sistema
- users: usuario, nombre, tipo

El botón "Ver en inventario" lleva a `/?tenant=<id>` (ya existe).

## Diseño técnico

**Backend** — `app/api/search.py`:
- Generalizar `_search_accounts_one` a `_search_one(tenant, resource, q)`
  que arma el cliente, llama al método correspondiente, filtra y devuelve
  `{tenant, rows}` o `{tenant, error}`.
- Una sola ruta `GET /search/{resource}` (`resource` validado con `Literal`
  o enum). El `action` de auditoría se deriva del recurso.
- Filtros de texto reutilizan `dash`/lower-contains como en el frontend.

**Frontend** — `lib/types.ts`: `ResourceSearchResponse<T>` genérico o un
tipo por recurso; `pages/Search.tsx` con el selector + columnas por recurso.

**Principios**: read-only, aislamiento por tenant, auditado, scopeado por
RBAC (usa `accessible_tenant_ids`, ya está en el endpoint actual).

## Tareas

- [x] Backend: `_search_one` genérico + `_fetch` + `_matches`; ruta única
      `GET /search/{resource}` con `resource: Literal[...]`; auditoría por
      recurso. `/search/accounts` sigue funcionando (es `resource="accounts"`).
- [x] Backend: `py_compile`.
- [x] Frontend: tipos genéricos (`SearchGroup`, `ResourceSearchResponse`),
      selector de recurso + columnas por recurso en `Search.tsx`.
- [x] Frontend: `tsc` + `build` verdes.
- [x] `CLAUDE.md` + `docs/PLAN.md`.
- [ ] Probar en el server, mover a `docs/specs/done/` (ya movida).

## Notas de implementación

- Cuentas usa el `search` nativo de CyberArk; safes/plataformas/usuarios se
  traen enteros y se filtran client-side (contains, case-insensitive) sobre
  campos clave.
- El frontend cambió `results[].accounts` → `results[].items` (genérico).
