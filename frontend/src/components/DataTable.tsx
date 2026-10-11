import {
  type ColumnDef,
  type ColumnFiltersState,
  type ColumnSizingState,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  type SortingState,
  type VisibilityState,
  useReactTable,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  ChevronUp,
  Filter,
  Loader2,
  RotateCcw,
  Search,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type SortVal = string | number | null | undefined;

export interface Column<T> {
  key: string;
  header: string;
  render?: (row: T) => ReactNode;
  sortValue?: (row: T) => SortVal;
  align?: "left" | "right";
  /** en modo servidor, false = columna no ordenable (campo calculado) */
  sortable?: boolean;
  /** false = sin filtro por columna (badges / campos calculados) */
  filterable?: boolean;
  /** en modo servidor: habilita el input de filtro de esta columna (viaja
   *  al backend vía `ServerMode.onChange({columnFilters})`) — a diferencia
   *  de `filterable`, que es sólo para modo cliente. Sin esto, modo
   *  servidor no tiene filtro por columna (TanStack sólo puede filtrar
   *  filas ya cargadas). */
  serverFilterable?: boolean;
  /** ancho inicial en px */
  width?: number;
  /** tope inferior al redimensionar (px, def 80) */
  minWidth?: number;
  /** oculta la columna por default (recién en el primer uso — el operador
   *  la puede mostrar con "Columnas", y esa elección queda persistida
   *  igual que cualquier otra) — para columnas secundarias que ensuciarían
   *  la grilla si estuvieran siempre visibles. */
  defaultHidden?: boolean;
}

export interface ServerMode {
  total: number;
  offset: number;
  limit: number;
  sort: { key: string; asc: boolean } | null;
  search: string;
  /** valor actual por columna server-filterable (key = `Column.key`) */
  columnFilters?: Record<string, string>;
  loading?: boolean;
  /** si está: selector de tamaño de página al pie */
  onLimitChange?: (limit: number) => void;
  pageSizes?: number[];
  onChange: (next: {
    offset?: number;
    sort?: { key: string; asc: boolean } | null;
    search?: string;
    columnFilters?: Record<string, string>;
  }) => void;
}

interface Props<T> {
  rows: T[];
  columns: Column<T>[];
  getRowKey: (row: T, i: number) => string | number;
  /** identifica la tabla para persistir anchos/visibilidad/orden en localStorage */
  tableId: string;
  filterOn?: (row: T) => SortVal[];
  filterPlaceholder?: string;
  total?: number;
  minWidth?: number;
  onRowClick?: (row: T) => void;
  toolbar?: ReactNode;
  /** cuando está presente: paginación/orden/búsqueda contra el backend */
  server?: ServerMode;
  /** checkbox por fila (controlado por el padre, sobrevive a la paginación
   *  server-side — se guarda por `getRowKey`, no por índice de fila) */
  selectedIds?: Set<string>;
  onSelectedIdsChange?: (next: Set<string>) => void;
  /** controles extra a la izquierda, junto al buscador (ej. filtros propios) */
  leadingToolbar?: ReactNode;
  /** sustantivo del total ("cuentas" -> "1.894 cuentas") */
  countNoun?: string;
  /** la fila de filtros por columna se esconde tras un botón "Filtros" y los
   *  filtros activos se muestran como chips que se pueden quitar */
  collapsibleColumnFilters?: boolean;
  /** botón "Columnas" sólo con ícono */
  compactColumnsButton?: boolean;
}

interface Persisted {
  sorting: SortingState;
  columnVisibility: VisibilityState;
  columnSizing: ColumnSizingState;
}

const EMPTY: Persisted = { sorting: [], columnVisibility: {}, columnSizing: {} };

function loadPersisted(key: string): Persisted {
  try {
    const raw = localStorage.getItem(key);
    if (raw) return { ...EMPTY, ...JSON.parse(raw) };
  } catch {
    /* storage no disponible / JSON corrupto */
  }
  return EMPTY;
}

const ROW_H = 41;

/** Texto del encabezado de una columna, para etiquetas accesibles. */
function columnLabel(c: { id: string; columnDef: { header?: unknown } }): string {
  return typeof c.columnDef.header === "string" ? c.columnDef.header : c.id;
}

export function DataTable<T>({
  rows,
  columns,
  getRowKey,
  tableId,
  filterOn,
  filterPlaceholder,
  total,
  minWidth = 800,
  onRowClick,
  toolbar,
  server,
  selectedIds,
  onSelectedIdsChange,
  leadingToolbar,
  countNoun,
  collapsibleColumnFilters = false,
  compactColumnsButton = false,
}: Props<T>) {
  const [showColFilters, setShowColFilters] = useState(false);
  const selectable = !!selectedIds && !!onSelectedIdsChange;
  const storageKey = `pam:table:${tableId}`;
  const [persisted, setPersisted] = useState<Persisted>(() => {
    const loaded = loadPersisted(storageKey);
    const defaultVisibility: VisibilityState = {};
    for (const c of columns) if (c.defaultHidden) defaultVisibility[c.key] = false;
    return { ...loaded, columnVisibility: { ...defaultVisibility, ...loaded.columnVisibility } };
  });
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);
  const [globalFilter, setGlobalFilter] = useState("");
  const [serverSearch, setServerSearch] = useState(server?.search ?? "");
  const [serverColFilters, setServerColFilters] = useState<Record<string, string>>(
    server?.columnFilters ?? {},
  );
  const scrollRef = useRef<HTMLDivElement>(null);

  // búsqueda server-side con debounce (igual que antes)
  useEffect(() => {
    if (!server) return;
    const id = setTimeout(() => {
      if (serverSearch !== server.search) server.onChange({ search: serverSearch, offset: 0 });
    }, 300);
    return () => clearTimeout(id);
  }, [serverSearch, server]);

  // filtro por columna server-side, mismo debounce que la búsqueda
  useEffect(() => {
    if (!server) return;
    const id = setTimeout(() => {
      const prev = server.columnFilters ?? {};
      const changed =
        Object.keys(serverColFilters).length !== Object.keys(prev).length ||
        Object.entries(serverColFilters).some(([k, v]) => prev[k] !== v);
      if (changed) server.onChange({ columnFilters: serverColFilters, offset: 0 });
    }, 300);
    return () => clearTimeout(id);
  }, [serverColFilters, server]);

  // persiste con un pequeño debounce (el resize dispara muchos cambios)
  useEffect(() => {
    const id = setTimeout(() => {
      try {
        localStorage.setItem(storageKey, JSON.stringify(persisted));
      } catch {
        /* storage no disponible */
      }
    }, 200);
    return () => clearTimeout(id);
  }, [storageKey, persisted]);

  type Updater<V> = V | ((old: V) => V);
  const setPart =
    <K extends keyof Persisted>(k: K) =>
    (u: Updater<Persisted[K]>) =>
      setPersisted((prev) => ({
        ...prev,
        [k]: typeof u === "function" ? (u as (o: Persisted[K]) => Persisted[K])(prev[k]) : u,
      }));

  function resetLayout() {
    try {
      localStorage.removeItem(storageKey);
    } catch {
      /* ignore */
    }
    setPersisted(EMPTY);
  }

  const alignById = useMemo(() => {
    const m: Record<string, "left" | "right" | undefined> = {};
    for (const c of columns) m[c.key] = c.align;
    return m;
  }, [columns]);

  const serverFilterableById = useMemo(() => {
    const m: Record<string, boolean> = {};
    for (const c of columns) m[c.key] = !!c.serverFilterable;
    return m;
  }, [columns]);

  const tsColumns = useMemo<ColumnDef<T>[]>(
    () =>
      columns.map((col) => ({
        id: col.key,
        header: col.header,
        accessorFn: (row: T) =>
          col.sortValue ? col.sortValue(row) : (row as Record<string, unknown>)[col.key],
        cell: col.render
          ? (ctx) => col.render!(ctx.row.original)
          : (ctx) => String(ctx.getValue() ?? "—"),
        enableSorting: col.sortable !== false,
        enableColumnFilter: !server && col.filterable !== false && col.sortable !== false,
        filterFn: (row, id, value) =>
          String(row.getValue(id) ?? "")
            .toLowerCase()
            .includes(String(value).toLowerCase()),
        size: col.width ?? 170,
        minSize: col.minWidth ?? 80,
      })),
    [columns, server],
  );

  const table = useReactTable({
    data: rows,
    columns: tsColumns,
    state: {
      sorting: persisted.sorting,
      columnVisibility: persisted.columnVisibility,
      columnSizing: persisted.columnSizing,
      columnFilters,
      globalFilter,
    },
    getRowId: (row, i) => String(getRowKey(row, i)),
    columnResizeMode: "onChange",
    enableSortingRemoval: true,
    enableMultiSort: !server,
    manualSorting: !!server,
    manualFiltering: !!server,
    manualPagination: !!server,
    onSortingChange: setPart("sorting"),
    onColumnVisibilityChange: setPart("columnVisibility"),
    onColumnSizingChange: setPart("columnSizing"),
    onColumnFiltersChange: setColumnFilters,
    onGlobalFilterChange: setGlobalFilter,
    globalFilterFn: (row, _id, value) => {
      if (!filterOn) return true;
      const needle = String(value).toLowerCase();
      return filterOn(row.original).some((f) =>
        String(f ?? "")
          .toLowerCase()
          .includes(needle),
      );
    },
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: server ? undefined : getSortedRowModel(),
    getFilteredRowModel: server ? undefined : getFilteredRowModel(),
  });

  const modelRows = table.getRowModel().rows;
  const virtualizer = useVirtualizer({
    count: modelRows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_H,
    overscan: 12,
  });
  const vItems = virtualizer.getVirtualItems();
  const padTop = vItems.length ? vItems[0].start : 0;
  const padBottom = vItems.length
    ? virtualizer.getTotalSize() - vItems[vItems.length - 1].end
    : 0;

  const selectColWidth = 36;
  const visibleIds = modelRows.map((r) => r.id);
  const allVisibleSelected =
    selectable && visibleIds.length > 0 && visibleIds.every((id) => selectedIds!.has(id));
  const someVisibleSelected = selectable && visibleIds.some((id) => selectedIds!.has(id));

  function toggleRow(id: string) {
    if (!selectable) return;
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onSelectedIdsChange!(next);
  }

  function toggleAllVisible() {
    if (!selectable) return;
    const next = new Set(selectedIds);
    if (allVisibleSelected) for (const id of visibleIds) next.delete(id);
    else for (const id of visibleIds) next.add(id);
    onSelectedIdsChange!(next);
  }

  const tableWidth = Math.max(minWidth, table.getTotalSize() + (selectable ? selectColWidth : 0));
  // +1: columna "relleno" sin ancho fijo al final de cada fila, para que la
  // tabla ocupe todo el ancho disponible en vez de dejar un hueco vacío a
  // la derecha cuando las columnas reales no llegan a cubrirlo (ver abajo).
  const colSpan = table.getVisibleLeafColumns().length + (selectable ? 1 : 0) + 1;
  const hasColFilters = server
    ? columns.some((c) => c.serverFilterable)
    : table.getAllLeafColumns().some((c) => c.getCanFilter());
  const filtersActive = columnFilters.length > 0 || globalFilter.length > 0;
  // filtros por columna server-side con valor (chips cuando son plegables)
  const activeServerColFilters = Object.entries(serverColFilters).filter(([, v]) => v);
  const colFilterRowVisible = hasColFilters && (!collapsibleColumnFilters || showColFilters);
  const headerLabelById = Object.fromEntries(columns.map((c) => [c.key, c.header]));

  // --- orden: click de header (server = onChange; cliente = TanStack) ---
  function onHeaderClick(colId: string, canSort: boolean, e: unknown) {
    if (!canSort) return;
    if (server) {
      const cur = server.sort;
      const next =
        !cur || cur.key !== colId
          ? { key: colId, asc: true }
          : cur.asc
            ? { key: colId, asc: false }
            : null;
      server.onChange({ sort: next, offset: 0 });
    } else {
      table.getColumn(colId)?.getToggleSortingHandler()?.(e);
    }
  }
  function sortDir(colId: string): "asc" | "desc" | false {
    if (server) {
      if (server.sort?.key !== colId) return false;
      return server.sort.asc ? "asc" : "desc";
    }
    return table.getColumn(colId)?.getIsSorted() ?? false;
  }

  const from = server ? server.offset + 1 : 1;
  const to = server ? server.offset + modelRows.length : modelRows.length;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        {server ? (
          <div className="relative w-[420px] max-w-full">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder={filterPlaceholder ?? "buscar"}
              value={serverSearch}
              onChange={(e) => setServerSearch(e.target.value)}
              className="pl-8"
            />
          </div>
        ) : filterOn ? (
          <div className="relative w-[420px] max-w-full">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder={filterPlaceholder ?? "filtrar"}
              value={globalFilter}
              onChange={(e) => setGlobalFilter(e.target.value)}
              className="pl-8"
            />
          </div>
        ) : null}

        {leadingToolbar}

        <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
          {server?.loading && <Loader2 className="size-3.5 animate-spin" />}
          {server
            ? `${server.total.toLocaleString("es-AR")}${countNoun ? ` ${countNoun}` : ""}`
            : `${modelRows.length.toLocaleString("es")}${
                typeof total === "number" && total !== rows.length ? ` / ${total}` : ""
              }${filtersActive ? ` de ${rows.length.toLocaleString("es")}` : ""}`}
        </span>

        <div className="ml-auto flex items-center gap-2">
          {toolbar}
          {!server && filtersActive && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setColumnFilters([]);
                setGlobalFilter("");
              }}
            >
              Limpiar filtros
            </Button>
          )}
          {collapsibleColumnFilters && hasColFilters && (
            <Button
              variant={showColFilters ? "secondary" : "outline"}
              size="sm"
              onClick={() => setShowColFilters((v) => !v)}
              aria-expanded={showColFilters}
            >
              <Filter className="size-3.5" />
              Filtros
              {activeServerColFilters.length > 0 && (
                <span className="rounded-full bg-sky-500 px-1.5 text-[11px] font-semibold text-white">
                  {activeServerColFilters.length}
                </span>
              )}
            </Button>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              {compactColumnsButton ? (
                <Button
                  variant="outline"
                  size="icon"
                  className="size-8"
                  aria-label="Columnas"
                  title="Columnas visibles"
                >
                  <SlidersHorizontal className="size-3.5" />
                </Button>
              ) : (
                <Button variant="outline" size="sm">
                  <SlidersHorizontal className="size-3.5" />
                  Columnas
                </Button>
              )}
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="max-h-80 overflow-auto">
              {table.getAllLeafColumns().map((c) => {
                const label = columns.find((x) => x.key === c.id)?.header ?? c.id;
                const last = table.getVisibleLeafColumns().length === 1 && c.getIsVisible();
                return (
                  <DropdownMenuCheckboxItem
                    key={c.id}
                    checked={c.getIsVisible()}
                    disabled={last}
                    onCheckedChange={(v) => c.toggleVisibility(!!v)}
                    onSelect={(e) => e.preventDefault()}
                  >
                    {label}
                  </DropdownMenuCheckboxItem>
                );
              })}
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={resetLayout}>
                <RotateCcw className="mr-2 size-3.5" />
                Restablecer
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {collapsibleColumnFilters && server && activeServerColFilters.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {activeServerColFilters.map(([k, v]) => (
            <span
              key={k}
              className="inline-flex items-center gap-1 rounded-full border border-sky-300 bg-sky-50 py-0.5 pl-2.5 pr-1 text-xs text-navy-900"
            >
              {headerLabelById[k] ?? k}: <b className="font-semibold">{v}</b>
              <button
                type="button"
                onClick={() => setServerColFilters((prev) => ({ ...prev, [k]: "" }))}
                aria-label={`Quitar filtro ${headerLabelById[k] ?? k}`}
                className="rounded-full p-0.5 hover:bg-sky-100"
              >
                <X className="size-3" />
              </button>
            </span>
          ))}
        </div>
      )}

      <div
        ref={scrollRef}
        className={cn(
          "max-h-[70vh] overflow-auto rounded-lg border border-border transition-opacity",
          server?.loading && "opacity-60",
        )}
        aria-busy={server?.loading || undefined}
      >
        <table
          className="text-sm"
          style={{ minWidth: tableWidth, width: "100%", tableLayout: "fixed" }}
        >
          <thead className="sticky top-0 z-10 bg-muted">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {selectable && (
                  <th
                    style={{ width: selectColWidth }}
                    className="border-b border-border px-2 py-2"
                  >
                    <input
                      type="checkbox"
                      className="size-3.5"
                      checked={allVisibleSelected}
                      ref={(el) => {
                        if (el) el.indeterminate = !allVisibleSelected && someVisibleSelected;
                      }}
                      onChange={toggleAllVisible}
                      aria-label="Seleccionar todas las filas visibles"
                    />
                  </th>
                )}
                {hg.headers.map((h) => {
                  const canSort =
                    (server ? h.column.columnDef.enableSorting !== false : h.column.getCanSort());
                  const dir = sortDir(h.column.id);
                  return (
                    <th
                      key={h.id}
                      style={{ width: h.getSize() }}
                      aria-sort={
                        dir === "asc" ? "ascending" : dir === "desc" ? "descending" : undefined
                      }
                      className={cn(
                        "relative select-none border-b border-border px-3 py-2 text-left font-medium",
                        alignById[h.column.id] === "right" && "text-right",
                      )}
                    >
                      {canSort ? (
                        // botón (no un span con onClick): ordenable también
                        // con teclado (Tab + Enter/Espacio)
                        <button
                          type="button"
                          onClick={(e) => onHeaderClick(h.column.id, canSort, e)}
                          className="inline-flex cursor-pointer items-center gap-1 rounded-sm text-left font-medium focus-visible:outline-2 focus-visible:outline-ring"
                        >
                          {flexRender(h.column.columnDef.header, h.getContext())}
                          {dir === "asc" && <ChevronUp className="size-3" aria-hidden="true" />}
                          {dir === "desc" && <ChevronDown className="size-3" aria-hidden="true" />}
                        </button>
                      ) : (
                        <span className="inline-flex items-center gap-1">
                          {flexRender(h.column.columnDef.header, h.getContext())}
                        </span>
                      )}
                      {h.column.getCanResize() && (
                        <span
                          onMouseDown={h.getResizeHandler()}
                          onTouchStart={h.getResizeHandler()}
                          onDoubleClick={() => h.column.resetSize()}
                          className={cn(
                            "absolute right-0 top-0 h-full w-1.5 cursor-col-resize touch-none select-none",
                            h.column.getIsResizing() ? "bg-primary" : "hover:bg-primary/40",
                          )}
                        />
                      )}
                    </th>
                  );
                })}
                <th className="border-b border-border" aria-hidden />
              </tr>
            ))}
            {colFilterRowVisible && (
              <tr>
                {selectable && (
                  <td style={{ width: selectColWidth }} className="border-b border-border p-1" />
                )}
                {table.getVisibleLeafColumns().map((c) => (
                  // celdas de filtro: <td>, no <th> (no son encabezados)
                  <td key={c.id} style={{ width: c.getSize() }} className="border-b border-border p-1">
                    {server ? (
                      serverFilterableById[c.id] ? (
                        <Input
                          value={serverColFilters[c.id] ?? ""}
                          onChange={(e) =>
                            setServerColFilters((prev) => ({ ...prev, [c.id]: e.target.value }))
                          }
                          placeholder="filtrar…"
                          aria-label={`Filtrar por ${columnLabel(c)}`}
                          className="h-7 text-xs"
                        />
                      ) : null
                    ) : c.getCanFilter() ? (
                      <Input
                        value={(c.getFilterValue() as string) ?? ""}
                        onChange={(e) => c.setFilterValue(e.target.value)}
                        placeholder="filtrar…"
                        aria-label={`Filtrar por ${columnLabel(c)}`}
                        className="h-7 text-xs"
                      />
                    ) : null}
                  </td>
                ))}
                <td className="border-b border-border p-1" aria-hidden />
              </tr>
            )}
          </thead>
          <tbody>
            {padTop > 0 && (
              <tr>
                <td style={{ height: padTop }} colSpan={colSpan} />
              </tr>
            )}
            {vItems.map((vi) => {
              const row = modelRows[vi.index];
              return (
                <tr
                  key={row.id}
                  onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                  // fila clickeable = también con teclado (Tab + Enter/Espacio)
                  tabIndex={onRowClick ? 0 : undefined}
                  onKeyDown={
                    onRowClick
                      ? (e) => {
                          if (e.target !== e.currentTarget) return;
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            onRowClick(row.original);
                          }
                        }
                      : undefined
                  }
                  className={cn(
                    "group border-b border-border/60",
                    onRowClick &&
                      "cursor-pointer hover:bg-accent/50 focus-visible:bg-accent/50 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring",
                  )}
                  style={{ height: ROW_H }}
                >
                  {selectable && (
                    <td
                      style={{ width: selectColWidth }}
                      className="px-2 py-2"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <input
                        type="checkbox"
                        className="size-3.5"
                        checked={selectedIds!.has(row.id)}
                        onChange={() => toggleRow(row.id)}
                        aria-label="Seleccionar fila"
                      />
                    </td>
                  )}
                  {row.getVisibleCells().map((cell) => (
                    <td
                      key={cell.id}
                      style={{ width: cell.column.getSize() }}
                      className={cn(
                        "truncate px-3 py-2",
                        alignById[cell.column.id] === "right" && "text-right",
                      )}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                  <td aria-hidden />
                </tr>
              );
            })}
            {padBottom > 0 && (
              <tr>
                <td style={{ height: padBottom }} colSpan={colSpan} />
              </tr>
            )}
            {modelRows.length === 0 && (
              <tr>
                <td colSpan={colSpan}>
                  <p className="py-6 text-center text-sm text-muted-foreground">Sin resultados.</p>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {server && server.total > 0 && <Pagination server={server} from={from} to={to} />}
    </div>
  );
}

/** Pie de una grilla server-side: rango, páginas (primera/anterior/
 *  siguiente/última + "Página X de Y") y, si el padre lo permite, tamaño de
 *  página. */
function Pagination({ server, from, to }: { server: ServerMode; from: number; to: number }) {
  const pages = Math.max(1, Math.ceil(server.total / server.limit));
  const page = Math.floor(server.offset / server.limit) + 1;
  const go = (p: number) => server.onChange({ offset: (p - 1) * server.limit });
  const btn =
    "inline-flex size-8 items-center justify-center rounded-md border border-input bg-card hover:bg-muted disabled:opacity-40 disabled:hover:bg-card";
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-muted-foreground">
      <span>
        {from.toLocaleString("es-AR")}–{to.toLocaleString("es-AR")} de{" "}
        {server.total.toLocaleString("es-AR")}
      </span>
      <div className="flex items-center gap-3">
        {server.onLimitChange && (
          <label className="flex items-center gap-1.5">
            Filas por página
            <select
              value={server.limit}
              onChange={(e) => server.onLimitChange!(Number(e.target.value))}
              className="h-8 rounded-md border border-input bg-card px-2 text-sm text-foreground"
            >
              {(server.pageSizes ?? [25, 50, 100]).map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="flex items-center gap-1">
          <button
            type="button"
            className={btn}
            disabled={page <= 1 || server.loading}
            onClick={() => go(1)}
            aria-label="Primera página"
          >
            <ChevronsLeft className="size-3.5" />
          </button>
          <button
            type="button"
            className={btn}
            disabled={page <= 1 || server.loading}
            onClick={() => go(page - 1)}
            aria-label="Página anterior"
          >
            <ChevronLeft className="size-3.5" />
          </button>
          <span className="px-2 tabular-nums text-foreground">
            Página {page.toLocaleString("es-AR")} de {pages.toLocaleString("es-AR")}
          </span>
          <button
            type="button"
            className={btn}
            disabled={page >= pages || server.loading}
            onClick={() => go(page + 1)}
            aria-label="Página siguiente"
          >
            <ChevronRight className="size-3.5" />
          </button>
          <button
            type="button"
            className={btn}
            disabled={page >= pages || server.loading}
            onClick={() => go(pages)}
            aria-label="Última página"
          >
            <ChevronsRight className="size-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}
