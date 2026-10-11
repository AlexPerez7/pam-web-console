import { useQuery } from "@tanstack/react-query";
import {
  Check,
  ChevronRight,
  FileCog,
  KeyRound,
  LayoutPanelLeft,
  Search,
  SearchX,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { PlatformConfigNode, PlatformConfigResponse, PlatformConfigRow } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Una sección con su camino desde la raíz, para breadcrumbs y búsqueda. */
interface FlatNode {
  node: PlatformConfigNode;
  path: PlatformConfigNode[];
  depth: number;
}

function flatten(nodes: PlatformConfigNode[], path: PlatformConfigNode[] = []): FlatNode[] {
  return nodes.flatMap((n) => [
    { node: n, path, depth: path.length },
    ...flatten(n.children, [...path, n]),
  ]);
}

function countRows(n: PlatformConfigNode): number {
  return n.rows.length + n.children.reduce((acc, c) => acc + countRows(c), 0);
}

function rowMatches(r: PlatformConfigRow, q: string): boolean {
  return [r.name, r.value, r.hint ?? ""].some((v) => v.toLowerCase().includes(q));
}

function Highlight({ text, q }: { text: string; q: string }) {
  if (!q) return <>{text}</>;
  const i = text.toLowerCase().indexOf(q);
  if (i < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, i)}
      <mark className="rounded-sm bg-sky-100 px-0.5 text-navy-900">{text.slice(i, i + q.length)}</mark>
      {text.slice(i + q.length)}
    </>
  );
}

/** Yes/No (y true/false) con ícono; números en monoespaciado; vacío "—". */
function Value({ value, q }: { value: string; q: string }) {
  const v = value.trim();
  const low = v.toLowerCase();
  if (!v) return <span className="text-muted-foreground">—</span>;
  if (low === "yes" || low === "true") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-[#eaf4de] px-2 py-0.5 text-xs font-semibold text-success-ink">
        <Check className="size-3" />
        Sí
      </span>
    );
  }
  if (low === "no" || low === "false") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs font-semibold text-muted-foreground">
        <X className="size-3" />
        No
      </span>
    );
  }
  const numeric = /^-?\d+(\.\d+)?$/.test(v);
  return (
    <span className={cn("break-all", numeric ? "font-mono text-sm font-semibold text-navy-900" : "text-sm")}>
      <Highlight text={v} q={q} />
    </span>
  );
}

function RowList({ rows, q }: { rows: PlatformConfigRow[]; q: string }) {
  return (
    <dl className="divide-y divide-border rounded-lg border border-border bg-card">
      {rows.map((r, i) => (
        <div
          key={`${r.name}-${i}`}
          className="grid grid-cols-1 gap-1 px-4 py-3 sm:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] sm:items-center sm:gap-4"
        >
          <dt className="min-w-0">
            <span className="block break-words font-mono text-[13px] font-medium text-navy-900">
              <Highlight text={r.name} q={q} />
            </span>
            {r.hint && (
              <span className="mt-0.5 block text-xs text-muted-foreground">
                <Highlight text={r.hint} q={q} />
              </span>
            )}
          </dt>
          <dd className="min-w-0 sm:text-right">
            <Value value={r.value} q={q} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

function Breadcrumb({ path, node }: { path: PlatformConfigNode[]; node: PlatformConfigNode }) {
  return (
    <div className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
      {[...path, node].map((p, i) => (
        <span key={p.id} className="inline-flex items-center gap-1">
          {i > 0 && <ChevronRight className="size-3" />}
          <span className={i === path.length ? "font-semibold text-navy-900" : undefined}>{p.label}</span>
        </span>
      ))}
    </div>
  );
}

const ROOT_ICON: Record<string, typeof KeyRound> = {
  xml: LayoutPanelLeft,
  ini: KeyRound,
};

/** Explorador de la configuración COMPLETA de una plataforma — el mismo
 *  contenido que el editor clásico del PVWA (`editor.aspx`: "UI &
 *  Workflows" + "Automatic Password Management"), leído del paquete de
 *  "Export Platform" (`GET .../platforms/{id}/config`). Sólo lectura:
 *  CyberArk no expone una API para editar estos parámetros. Navegación a
 *  la izquierda, parámetros con descripción en español a la derecha y
 *  búsqueda sobre todas las secciones a la vez. Ver
 *  `docs/specs/platform-config-viewer.md`. */
export function PlatformConfigExplorer({ tenant, platformId }: { tenant: string; platformId: string }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const q = search.trim().toLowerCase();

  const cfg = useQuery({
    queryKey: ["platform-config", tenant, platformId],
    queryFn: () =>
      api<PlatformConfigResponse>(
        `/tenants/${tenant}/platforms/${encodeURIComponent(platformId)}/config`,
      ),
    staleTime: 5 * 60_000,
  });

  const flat = useMemo(() => flatten(cfg.data?.tree ?? []), [cfg.data]);
  const sections = flat.filter((f) => f.depth > 0);
  const total = useMemo(() => (cfg.data?.tree ?? []).reduce((a, n) => a + countRows(n), 0), [cfg.data]);

  const matchCount = useMemo(() => {
    const m = new Map<string, number>();
    if (!q) return m;
    for (const f of flat) m.set(f.node.id, f.node.rows.filter((r) => rowMatches(r, q)).length);
    return m;
  }, [flat, q]);

  if (cfg.isLoading) {
    return (
      <div className="flex gap-4">
        <div className="hidden w-60 shrink-0 flex-col gap-2 sm:flex">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-7 w-full" />
          ))}
        </div>
        <div className="flex flex-1 flex-col gap-2">
          <Skeleton className="h-8 w-1/2" />
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-12 w-full" />
          ))}
        </div>
      </div>
    );
  }
  if (cfg.isError) {
    return (
      <Alert variant="destructive">
        No se pudo leer la configuración: {(cfg.error as Error).message}. PAM Web Console la obtiene del paquete de
        "Export Platform"; el service account necesita poder exportar plataformas.
      </Alert>
    );
  }
  if (!cfg.data || cfg.data.tree.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        El paquete de exportación no trajo archivos de configuración reconocibles.
      </p>
    );
  }

  const selected = sections.find((f) => f.node.id === selectedId) ?? sections[0];
  const results = q
    ? flat
        .map((f) => ({ ...f, rows: f.node.rows.filter((r) => rowMatches(r, q)) }))
        .filter((f) => f.rows.length > 0)
    : [];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Buscar parámetro, valor o descripción en todas las secciones"
            aria-label="Buscar en la configuración"
            className="h-9 pl-8"
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch("")}
              aria-label="Limpiar búsqueda"
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded-sm p-0.5 text-muted-foreground hover:text-foreground"
            >
              <X className="size-3.5" />
            </button>
          )}
        </div>
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <FileCog className="size-3.5" />
          {total.toLocaleString("es")} parámetros · sólo lectura
        </span>
      </div>

      {cfg.data.errors.length > 0 && (
        <Alert variant="warning">
          Parte del paquete no se pudo leer: {cfg.data.errors.join("; ")}
        </Alert>
      )}

      <div className="flex flex-col gap-4 sm:flex-row">
        <nav
          aria-label="Secciones de la configuración"
          className="max-h-64 shrink-0 overflow-y-auto sm:sticky sm:top-0 sm:max-h-[calc(100vh-18rem)] sm:w-60"
        >
          {cfg.data.tree.map((root) => {
            const Icon = ROOT_ICON[root.id] ?? FileCog;
            const items = flat.filter((f) => f.path[0]?.id === root.id);
            return (
              <div key={root.id} className="mb-3">
                <p className="mb-1 flex items-center gap-1.5 px-2 text-[11px] font-bold uppercase tracking-wide text-muted-foreground">
                  <Icon className="size-3.5" />
                  {root.label}
                </p>
                <ul className="flex flex-col">
                  {items.map((f) => {
                    const active = !q && selected?.node.id === f.node.id;
                    const hits = matchCount.get(f.node.id) ?? 0;
                    const count = q ? hits : f.node.rows.length;
                    return (
                      <li key={f.node.id}>
                        <button
                          type="button"
                          onClick={() => {
                            setSelectedId(f.node.id);
                            setSearch("");
                          }}
                          style={{ paddingLeft: `${0.5 + (f.depth - 1) * 0.85}rem` }}
                          className={cn(
                            "relative flex w-full items-center gap-2 rounded-md py-1.5 pr-2 text-left text-sm transition-colors",
                            active
                              ? "bg-accent font-semibold text-navy-900 before:absolute before:inset-y-1 before:left-0 before:w-0.5 before:rounded-full before:bg-sky-500"
                              : "text-foreground hover:bg-accent/60",
                            q && hits === 0 && "opacity-40",
                          )}
                          title={f.node.source_name}
                        >
                          <span className="min-w-0 flex-1 truncate">{f.node.label}</span>
                          {count > 0 && (
                            <span
                              className={cn(
                                "shrink-0 rounded-full px-1.5 text-[11px] tabular-nums",
                                q ? "bg-sky-100 font-semibold text-navy-900" : "text-muted-foreground",
                              )}
                            >
                              {count}
                            </span>
                          )}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </nav>

        <div className="min-w-0 flex-1">
          {q ? (
            results.length === 0 ? (
              <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border py-10 text-center">
                <SearchX className="size-6 text-muted-foreground" />
                <p className="text-sm text-muted-foreground">
                  Ningún parámetro coincide con "{search.trim()}".
                </p>
              </div>
            ) : (
              <div className="flex flex-col gap-4">
                {results.map((f) => (
                  <section key={f.node.id} className="flex flex-col gap-2">
                    <button
                      type="button"
                      className="w-fit text-left hover:underline"
                      onClick={() => {
                        setSelectedId(f.node.id);
                        setSearch("");
                      }}
                    >
                      <Breadcrumb path={f.path} node={f.node} />
                    </button>
                    <RowList rows={f.rows} q={q} />
                  </section>
                ))}
              </div>
            )
          ) : (
            selected && (
              <section className="flex flex-col gap-3">
                <header className="flex flex-col gap-1">
                  <Breadcrumb path={selected.path} node={selected.node} />
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <h3 className="text-lg font-bold text-navy-900">{selected.node.label}</h3>
                    {selected.node.source_name !== selected.node.label && (
                      <span className="text-xs text-muted-foreground">
                        en el PVWA: <span className="font-medium">{selected.node.source_name}</span>
                      </span>
                    )}
                  </div>
                </header>

                {selected.node.rows.length > 0 ? (
                  <RowList rows={selected.node.rows} q="" />
                ) : (
                  <p className="text-sm text-muted-foreground">
                    Esta sección no tiene parámetros propios
                    {selected.node.children.length > 0 ? "; elija una subsección." : "."}
                  </p>
                )}

                {selected.node.children.length > 0 && (
                  <div className="flex flex-col gap-1.5">
                    <p className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">
                      Subsecciones
                    </p>
                    <div className="grid gap-2 sm:grid-cols-2">
                      {selected.node.children.map((c) => (
                        <button
                          key={c.id}
                          type="button"
                          onClick={() => setSelectedId(c.id)}
                          className="flex items-center justify-between gap-2 rounded-lg border border-border px-3 py-2.5 text-left text-sm transition-colors hover:border-sky-300 hover:bg-accent/50"
                        >
                          <span className="min-w-0 truncate font-medium text-navy-900">{c.label}</span>
                          <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
                            {countRows(c)}
                            <ChevronRight className="size-3.5" />
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </section>
            )
          )}
        </div>
      </div>
    </div>
  );
}
