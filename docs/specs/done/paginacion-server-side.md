# paginacion-server-side

- **Estado:** implementada — pendiente de probar en el server
- **Roadmap:** Fase 1, ítem "grillas … + orden + paginación server-side"
- **Fecha:** 2026-09-09

## Problema

El inventario trae **todo** el recurso y filtra/ordena/recorta en el
navegador (tope de 300 filas visibles). Para Globex (~1900 cuentas) la
carga es lenta y la vista queda truncada — no se puede llegar a la fila 400.

## Alcance

**Entra:**
- Paginación / orden / búsqueda **server-side** para **Cuentas** y **Safes**
  (la API de CyberArk lo soporta nativo: `offset`, `limit`, `sort`, `search`,
  y devuelve `count` total).
- La grilla (`DataTable`) gana un modo "servidor": no filtra/ordena/recorta,
  pide páginas y muestra controles de paginación.

**Fuera:**
- Plataformas, Usuarios, Sesiones, Componentes — siguen client-side (son
  conjuntos acotados: ~200 plataformas, pocos cientos de usuarios).
- Virtualización de la grilla: con `limit=100` server-side no hace falta.
- Persistir el estado de la grilla en la URL.

## Requisitos

### R1 — Endpoints paginados

`GET /tenants/{id}/accounts` y `.../safes` aceptan:
`search` (str), `sort` (str: `userName` o `-userName` para desc), `offset`
(int ≥ 0), `limit` (int 1–1000, default 100).

Respuesta: `{"tenant", "count": <total real>, "offset", "limit",
"accounts"|"safes": [<página>]}`. Auditado como hoy (`account.list` /
`safe.list`) con `detail = {"count": total, "returned": len(pagina)}`.

**Escenario:**
Dado Globex con 1894 cuentas,
Cuando el operador pide `?offset=100&limit=100&sort=-userName`,
Entonces recibe 100 cuentas ordenadas por usuario desc y `count: 1894`.

### R2 — Grilla en modo servidor

`DataTable` con prop `server`:
- No aplica filtro/orden/slice local.
- El buscador escribe con **debounce 300 ms** → nueva página desde offset 0.
- Click en el encabezado → cambia `sort` (asc → desc → sin orden) y recarga.
- Barra inferior: `[‹ Anterior]  N–M de TOTAL  [Siguiente ›]`.
- Mientras carga la página siguiente, mantiene la anterior visible
  (`placeholderData: keepPreviousData`).

## Diseño técnico

**Backend**
- `client.py`: `accounts_page(*, search, sort, offset, limit) -> (rows, total)`
  y `safes_page(...)` — usan `GET` directo (no `get_all`) y leen `count`.
- `inventory.py`: `/accounts` y `/safes` toman los 4 query params; `_run`
  detecta un resultado `tuple(list, int)` y audita `count`/`returned`.
- Sin migración.

**Frontend**
- `lib/types.ts`: `AccountsResponse` / `SafesResponse` suman `offset`,
  `limit`.
- `components/DataTable.tsx`: prop opcional `server` (total, offset, limit,
  sort, search, onChange, loading). Cuando está, rama server; sin ella, la
  rama client actual intacta.
- `pages/Inventory.tsx`: `AccountsTab` y `SafesTab` manejan el estado
  (offset/sort/search) y arman la query; el resto de los tabs sin cambios.

## Tareas

- [x] Backend: `accounts_page` / `safes_page` (GET directo, leen `count`) +
      rutas con `?search&sort&offset&limit`; `_run` maneja `tuple(rows,total)`.
- [x] Backend: `py_compile`.
- [x] Frontend: tipos (+`offset`/`limit`), `DataTable` prop `server` (input
      con debounce 300 ms, orden por header, barra de paginación,
      `keepPreviousData`), `useServerGrid` hook, `AccountsTab`/`SafesTab`.
- [x] Frontend: `tsc` + `build` verdes.
- [x] `CLAUDE.md` + `docs/PLAN.md`.
- [ ] Probar en el server (Globex), mover a `docs/specs/done/` (ya movida).

## Notas de implementación

- Solo `name/userName/address/platformId/safeName` (cuentas) y `safeName`
  (safes) son ordenables server-side (campos que acepta el `sort` de
  CyberArk); el resto son `sortable: false`.
- Plataformas/Usuarios/Sesiones/Componentes siguen en modo client (conjuntos
  acotados) — `DataTable` mantiene esa rama intacta.
