# tablas-mejoradas

- **Estado:** hecha (probada en el server 2026-09-10)
- **Roadmap:** Fase 1, ítem "grids grandes: virtualización, filtros, orden,
  selección múltiple" (`docs/PLAN.md §4.2`, y el "Falta: virtualización de
  grillas" de §7)
- **Fecha:** 2026-09-10

## Problema

El `DataTable` de hoy es mínimo: **un** cuadro de filtro de texto global,
orden por columna (asc/desc al clickear el header) y un corte a 300 filas en
modo cliente. Para trabajar el inventario en serio falta:

- **Filtrar por columna** (ej. "plataforma = WinDomain" y a la vez "safe
  contiene SQL"), no un solo término contra todo.
- **Redimensionar columnas** — hoy los anchos los decide el navegador y una
  descripción larga aplasta al resto.
- **Elegir qué columnas ver** y que la preferencia se recuerde.
- **Ver todas las filas** sin el tope de 300 (Usuarios/Grupos/Plataformas de
  algunos tenants lo superan) — hoy no hay forma de llegar a la fila 301.

## Alcance

**Entra:**
- Reescribir `DataTable` sobre **TanStack Table** (headless) + **TanStack
  Virtual** para las filas. La API pública (`Column<T>`, props) se mantiene
  compatible; se agregan capacidades.
- **Redimensionar columnas** arrastrando el borde del header. Ancho por
  columna persistido.
- **Filtro por columna** (modo cliente): una fila de inputs bajo los headers;
  `incluye` sobre el valor de orden de la columna. El buscador global actual
  se mantiene.
- **Mostrar/ocultar columnas** desde un menú "Columnas". Persistido.
- **Orden tri-estado** (asc → desc → sin orden) al clickear el header;
  shift-click para orden secundario (modo cliente).
- **Header sticky** al hacer scroll vertical dentro de la grilla.
- **Virtualización de filas** en modo cliente → se elimina el tope de 300.
- Persistencia por tabla (`localStorage`, key por `tableId`): anchos,
  visibilidad y orden. Botón "Restablecer".

**Fuera (explícito):**
- **Modo servidor** (tabs Cuentas y Safes): sigue con su búsqueda/orden/
  paginación server-side y su único cuadro de búsqueda. El **filtro por
  columna** en modo servidor necesita extender el backend (`?filter[campo]=`)
  — otra spec. Sí aplican: resize, visibilidad, header sticky, virtualización
  de la página actual.
- **Selección de filas / acciones en lote** — llega con Fase 2 (escritura);
  el modelo de TanStack ya lo soporta, se activa entonces.
- Reordenar columnas con drag, agrupar, pivotar, exportar la vista filtrada
  (el export ya es "todo el tenant", `export-csv-pdf`).
- Las tablas de `Search.tsx` y `Operators.tsx` (chicas, fijas) — se pueden
  migrar después, no en esta spec.

## Requisitos

### R1 — Redimensionar columnas

Cada header (salvo la última) tiene un tirador en el borde derecho; al
arrastrar cambia el ancho de esa columna. Doble-click en el tirador vuelve la
columna a su ancho por defecto. Los anchos se guardan y se recuperan al
volver a la pestaña.

**Escenario:**
Dado el tab Safes con la columna "Descripción" muy ancha,
Cuando el operador arrastra su borde para achicarla,
Entonces la tabla se re-maqueta al instante y, al cambiar de tab y volver, la
columna sigue angosta.

### R2 — Filtro por columna (modo cliente)

Bajo la fila de headers hay una fila de inputs (uno por columna filtrable).
Escribir en varios los combina con AND. El buscador global sigue existiendo y
se combina también con AND. Un contador muestra "N de M" cuando hay filtro
activo. Botón para limpiar todos los filtros de columna.

**Escenario:**
Dado el tab Usuarios,
Cuando el operador escribe "svc" en la columna Usuario y "EPVUser" en Tipo,
Entonces sólo quedan los usuarios cuyo nombre incluye "svc" **y** son
EPVUser, y el contador dice "12 de 512".

**Escenario (modo servidor):**
Dado el tab Cuentas,
Cuando el operador mira los headers,
Entonces **no** hay fila de filtros por columna (sigue el buscador único
server-side); sí puede redimensionar y ocultar columnas.

### R3 — Mostrar/ocultar columnas

Un botón "Columnas" abre un menú con un check por columna. Desmarcar la
oculta. La preferencia se persiste por tabla. Al menos una columna queda
siempre visible.

### R4 — Orden

Click en el header: sin orden → asc → desc → sin orden. La flecha indica el
estado. Shift-click agrega la columna como criterio secundario (modo
cliente; en modo servidor el orden sigue siendo de a una columna, como hoy).
El orden se persiste por tabla (modo cliente).

### R5 — Virtualización y header sticky

En modo cliente se renderatizan sólo las filas visibles (TanStack Virtual);
se elimina `maxRows`. El header queda fijo (`position: sticky`) dentro del
contenedor con scroll. El contenedor tiene alto máximo (ej. `70vh`) y scroll
vertical propio; el scroll horizontal se mantiene para tablas anchas.

**Escenario:**
Dado un tenant con 1.900 usuarios en modo cliente,
Cuando el operador scrollea la grilla,
Entonces el scroll es fluido, el header no se va, y puede llegar a la última
fila.

### R6 — Persistencia

Estado guardado en `localStorage` bajo `pam:table:{tableId}`:
`{ columnSizing, columnVisibility, sorting }`. Los **filtros** (global y por
columna) **no** se persisten (son de la sesión de trabajo). Botón
"Restablecer" que borra la key y vuelve a los defaults. Si el JSON guardado
está corrupto o una columna ya no existe, se ignora sin romper.

## Diseño técnico

### Dependencias

`frontend/package.json`: `@tanstack/react-table` y
`@tanstack/react-virtual` (mismos autores que `react-query`, ya en el
proyecto; headless, sin CSS).

### `components/DataTable.tsx` (reescritura)

- **API pública que se mantiene:** `rows`, `columns: Column<T>[]`,
  `getRowKey`, `filterOn`, `filterPlaceholder`, `onRowClick`, `toolbar`,
  `server?: ServerMode`, `minWidth`.
- **`Column<T>` se extiende** (todo opcional, retro-compatible):
  - `width?: number` — ancho inicial (px).
  - `minWidth?: number` — tope inferior al redimensionar (def 80).
  - `filterable?: boolean` — def `true` si la columna tiene `sortValue` o
    `key` mapeable; `false` para columnas calculadas (badges, etc.).
  - `sortable?: boolean` — ya existe.
- **Nueva prop `tableId: string`** (obligatoria) — key de persistencia
  (`"inv-accounts"`, `"inv-safes"`, …). Sin `tableId` no se persiste.
- Internamente: mapear `Column<T>` → `ColumnDef` de TanStack.
  - `accessorFn` = `col.sortValue ?? (row) => row[col.key]` (para
    orden/filtro).
  - `cell` = `col.render ?? (v) => String(v ?? "—")`.
  - `enableSorting` = `col.sortable !== false && !server-calculated`.
  - `enableColumnFilter` = `col.filterable !== false && !server`.
  - `enableResizing = true`, `size`/`minSize` de `width`/`minWidth`.
- `columnResizeMode: "onChange"`, `table-layout: fixed`, `<th>/<td>` con
  `style={{ width: header.getSize() }}`.
- Filtro de columna: `filterFn` propia tipo `includesString` sobre el valor
  del `accessorFn` (string, lower, `includes`).
- Filtro global: se mantiene `filterOn` → `globalFilterFn` que concatena esos
  campos.
- Virtualización: `useVirtualizer` sobre el contenedor scroll; filas con
  `transform: translateY`. Estimación de alto de fila fija (ej. 40px).
- Estado persistido: hook `useTableState(tableId)` que lee/escribe
  `localStorage` con `try/catch` (mismo criterio tolerante que el resto del
  front); `sorting`, `columnVisibility`, `columnSizing` controlados por ese
  hook. `columnFilters` y `globalFilter` en estado local (no persistidos).
- **Modo servidor**: cuando viene `server`, se desactivan `getSortedRowModel`
  / `getFilteredRowModel` (el backend ordena y filtra); el click de header y
  la barra de paginación siguen llamando `server.onChange` como hoy. Resize/
  visibilidad/sticky/virtualización de la página actual: activos.

### `pages/Inventory.tsx`

- Pasar `tableId` a cada `<DataTable>` (`"inv-accounts"`, `"inv-safes"`,
  `"inv-platforms"`, `"inv-users"`, `"inv-groups"`, `"inv-sessions"`,
  `"inv-components"`).
- Marcar `filterable: false` en las columnas de badge/calculadas que ya
  tienen `sortable: false` (Gestión, CPM, Estado con render de badge, etc.).
- Opcional: `width` inicial para las columnas que hoy quedan mal (Descripción,
  Address).

### Principios

- Sólo frontend; ni un endpoint nuevo. Los datos ya vienen del inventario
  auditado. El estado de la tabla vive en el navegador del operador
  (`localStorage`), nunca en el server — igual que las otras preferencias de
  UI.

## Tareas

- [x] Deps: `@tanstack/react-table` (8.21) + `@tanstack/react-virtual` (3.14).
- [x] `DataTable.tsx`: reescritura sobre TanStack Table (mapa `Column<T>` →
      `ColumnDef`, `manual*` en modo servidor).
- [x] Resize de columnas (`columnResizeMode: onChange`, doble-click resetea)
      + persistencia (`columnSizing`).
- [x] Fila de filtros por columna (modo cliente) + botón "Limpiar filtros" +
      contador "N de M".
- [x] Menú "Columnas" (visibilidad, no deja ocultar la última) + "Restablecer".
- [x] Orden tri-estado + shift-multi (cliente) + persistencia.
- [x] Virtualización de filas (`useVirtualizer`, filas de padding) + header
      sticky + contenedor `max-h-[70vh]`; sacado `maxRows`.
- [x] Persistencia inline en `DataTable` (`pam:table:<tableId>`), write con
      debounce 200 ms, `try/catch`, merge con defaults (tolera JSON corrupto
      / columnas viejas).
- [x] `Inventory.tsx`: `tableId` en los 7 tabs + `filterable:false` en las
      columnas de badge (Estado de plataformas/usuarios/componentes) + `width`
      inicial en Descripción (safes/grupos).
- [x] `npm run lint` + `tsc` + `build` verdes. Bundle +~24 kB gzip
      (react-table); `chunkSizeWarningLimit` subido a 700 en `vite.config.ts`.
- [x] `CLAUDE.md` + `docs/PLAN.md` (§7 Fase 1) actualizados.
- [x] Probado en el server (2026-09-10): los 7 tabs, resize/filtros/
      visibilidad/orden se recuerdan al cambiar de tab; ~1.900 filas fluidas;
      modo servidor (Cuentas/Safes) intacto.

## Notas de cierre

- No se hizo un hook `useTableState` aparte: la persistencia vive dentro de
  `DataTable` (estado `persisted` + `useEffect` con debounce). Más simple, un
  solo consumidor.
- `columnResizeMode: "onChange"` (resize en vivo); el `localStorage.setItem`
  va con debounce de 200 ms para no escribir en cada mousemove.
- Celdas con `truncate` (los anchos ahora los fija el usuario) → altura de
  fila uniforme, que es lo que la virtualización necesita. El contenido
  completo se ve en el `DetailDrawer` al clickear.
- El filtro por columna se desactiva solo en las columnas con `sortable:
  false` o `filterable: false`, y en todo el modo servidor.

## Notas de implementación

- El `print:` del dashboard no toca esto, pero al imprimir el inventario (si
  algún día se agrega) hay que expandir la virtualización — no es tema ahora.
- Mantener el contrato de `server` **idéntico** para no tocar `useServerGrid`
  ni los endpoints.
- Densidad (compacto/cómodo) y reordenar columnas quedan como posible
  iteración menor si sobra tiempo; no bloquean.
