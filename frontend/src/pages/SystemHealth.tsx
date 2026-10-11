import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  CircleHelp,
  Loader2,
  RefreshCw,
  XCircle,
} from "lucide-react";
import { useRef, useState, type ReactNode } from "react";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { dash, fmtDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  SystemHealthComponent,
  SystemHealthError,
  SystemHealthInstance,
  SystemHealthResponse,
  SystemHealthRow,
} from "@/lib/types";

/**
 * System Health cross-tenant. Una tarjeta por tenant con el estado de cada
 * componente (Vault, CPM, PSM) en verde / ámbar / rojo, una barra
 * segmentada con una marca por instancia y, al hacer clic en un componente,
 * el detalle de sus instancias (IP, versión, usuario, última conexión), como
 * el System Health del PVWA pero con todos los tenants en una sola pantalla.
 * Los tenants con problemas se ordenan primero.
 */

type Status = "ok" | "degraded" | "down" | "unknown";

const RANK: Record<Status, number> = { down: 0, degraded: 1, ok: 2, unknown: 3 };

const STATUS_STYLE: Record<
  Status,
  {
    label: string;
    ink: string;
    bar: string;
    soft: string;
    border: string;
    Icon: typeof CheckCircle2;
  }
> = {
  ok: {
    label: "Operativo",
    ink: "text-success-ink",
    bar: "bg-line-transformation",
    soft: "bg-[#eaf4de]",
    border: "border-l-line-transformation",
    Icon: CheckCircle2,
  },
  degraded: {
    label: "Degradado",
    ink: "text-warning-ink",
    bar: "bg-line-consulting",
    soft: "bg-[#fdf0d6]",
    border: "border-l-line-consulting",
    Icon: AlertTriangle,
  },
  down: {
    label: "Caído",
    ink: "text-danger-ink",
    bar: "bg-line-cyberdefense",
    soft: "bg-[#fbe2e2]",
    border: "border-l-line-cyberdefense",
    Icon: XCircle,
  },
  unknown: {
    label: "Sin datos",
    ink: "text-muted-foreground",
    bar: "bg-muted-foreground/30",
    soft: "bg-muted",
    border: "border-l-border",
    Icon: CircleHelp,
  },
};

function componentStatus(c: SystemHealthComponent | undefined): Status {
  if (!c || !c.available || c.total === 0) return "unknown";
  if (c.connected === c.total) return "ok";
  return c.connected === 0 ? "down" : "degraded";
}

function vaultStatus(row: SystemHealthRow): Status {
  if (!row.vault.available) return "unknown";
  return row.vault.connected ? "ok" : "down";
}

/** Componentes que se muestran: CPM y PSM, más AIM sólo si el tenant lo usa.
 *  El Web Portal (PVWA) no: en Privilege Cloud lo administra CyberArk y no
 *  hay nada que el equipo pueda hacer sobre él; tampoco cuenta para el
 *  estado del tenant. */
function visibleComponents(row: SystemHealthRow): SystemHealthComponent[] {
  const order = ["CPM", "SessionManagement", "AIM"];
  return order
    .map((id) => row.components.find((c) => c.id === id))
    .filter((c): c is SystemHealthComponent => !!c && (c.id !== "AIM" || c.total > 0));
}

/** El peor estado entre sus componentes: con uno entero caído (ej. ningún
 *  CPM conectado) el tenant ya es crítico aunque el resto ande. */
function tenantStatus(row: SystemHealthRow): Status {
  const all = [vaultStatus(row), ...visibleComponents(row).map(componentStatus)];
  const known = all.filter((s) => s !== "unknown");
  if (known.length === 0) return "unknown";
  if (known.includes("down")) return "down";
  return known.includes("degraded") ? "degraded" : "ok";
}

/** Etiqueta a nivel tenant: "Crítico" en vez de "Caído", porque puede tener
 *  componentes arriba. */
const TENANT_LABEL: Partial<Record<Status, string>> = { down: "Crítico" };

function fmtNum(v: number | null) {
  return v === null ? "—" : v.toLocaleString("es-AR");
}

export default function SystemHealth() {
  const wantRefresh = useRef(false);

  const q = useQuery({
    queryKey: ["system-health"],
    queryFn: () => {
      const suffix = wantRefresh.current ? "?refresh=1" : "";
      wantRefresh.current = false;
      return api<SystemHealthResponse>(`/system-health${suffix}`);
    },
    staleTime: 60_000,
  });

  function doRefresh() {
    wantRefresh.current = true;
    q.refetch();
  }

  const rows = [...(q.data?.tenants ?? [])].sort(
    (a, b) =>
      RANK[tenantStatus(a)] - RANK[tenantStatus(b)] || a.tenant_name.localeCompare(b.tenant_name),
  );
  const errors = q.data?.errors ?? [];
  const counts = { ok: 0, degraded: 0, down: errors.length, unknown: 0 };
  for (const r of rows) counts[tenantStatus(r)] += 1;
  const total = rows.length + errors.length;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-[22px] font-bold text-navy-900">System Health</h1>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Estado de los componentes de CyberArk en todos los tenants.
            {q.data && ` Actualizado ${fmtDateTime(q.data.generated_at)}.`}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={doRefresh} disabled={q.isFetching}>
          <RefreshCw className={cn("size-3.5", q.isFetching && "animate-spin")} />
          Actualizar
        </Button>
      </div>

      {q.isLoading && (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          <span>Consultando los tenants…</span>
        </div>
      )}
      {q.isError && <Alert variant="destructive">{(q.error as Error).message}</Alert>}

      {q.data && total > 0 && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <SummaryTile status="ok" value={counts.ok} label="Operativos" />
          <SummaryTile status="degraded" value={counts.degraded} label="Degradados" />
          <SummaryTile status="down" value={counts.down} label="Críticos o sin respuesta" />
          <div className="rounded-lg border border-border bg-card px-4 py-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Tenants
            </p>
            <p className="mt-1 text-2xl font-bold text-navy-900">{total}</p>
          </div>
        </div>
      )}

      {errors.map((e) => (
        <ErrorCard key={e.tenant} error={e} />
      ))}
      {rows.map((row) => (
        <TenantCard key={row.tenant} row={row} />
      ))}

      {q.data && total === 0 && (
        <p className="text-sm text-muted-foreground">Sin tenants accesibles.</p>
      )}
    </div>
  );
}

function SummaryTile({ status, value, label }: { status: Status; value: number; label: string }) {
  const st = STATUS_STYLE[status];
  const active = value > 0;
  return (
    <div
      className={cn(
        "flex items-center gap-3 rounded-lg border border-border px-4 py-3",
        active ? st.soft : "bg-card",
      )}
    >
      <st.Icon
        className={cn("size-6 shrink-0", active ? st.ink : "text-muted-foreground/50")}
        aria-hidden="true"
      />
      <div>
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {label}
        </p>
        <p className={cn("text-2xl font-bold", active ? st.ink : "text-navy-900")}>{value}</p>
      </div>
    </div>
  );
}

function StatusPill({ status, label }: { status: Status; label?: string }) {
  const st = STATUS_STYLE[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold",
        st.soft,
        st.ink,
      )}
    >
      <span className={cn("size-2 rounded-full", st.bar)} aria-hidden="true" />
      {label ?? st.label}
    </span>
  );
}

function ErrorCard({ error }: { error: SystemHealthError }) {
  return (
    <section className="rounded-lg border border-l-4 border-border border-l-line-cyberdefense bg-card p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-base font-bold text-navy-900">{error.tenant_name}</h2>
        <StatusPill status="down" label="Sin respuesta" />
      </div>
      <p className="mt-2 text-sm text-danger-ink">No se pudo consultar el tenant: {error.error}</p>
    </section>
  );
}

function TenantCard({ row }: { row: SystemHealthRow }) {
  const [open, setOpen] = useState<string | null>(null);
  const status = tenantStatus(row);
  const comps = visibleComponents(row);
  const selected = comps.find((c) => c.id === open);

  return (
    <section
      className={cn(
        "rounded-lg border border-l-4 border-border bg-card",
        STATUS_STYLE[status].border,
      )}
    >
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-base font-bold text-navy-900">{row.tenant_name}</h2>
          <StatusPill status={status} label={TENANT_LABEL[status]} />
        </div>
        <div className="flex items-center gap-6 text-sm">
          <Metric label="Cuentas gestionadas" value={fmtNum(row.accounts_total)} />
          <Metric label="Sesiones activas" value={fmtNum(row.sessions_active)} />
        </div>
      </header>

      <div className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3">
        <ComponentTile
          name="Vault"
          status={vaultStatus(row)}
          summary={
            !row.vault.available ? "Sin datos" : row.vault.connected ? "Conectado" : "Desconectado"
          }
          detail={row.vault.available && row.vault.ip ? `IP ${row.vault.ip}` : undefined}
          segments={row.vault.available ? [!!row.vault.connected] : []}
        />
        {comps.map((c) => (
          <ComponentTile
            key={c.id}
            name={c.name}
            status={componentStatus(c)}
            summary={
              !c.available || c.total === 0
                ? "Sin datos"
                : c.total === 1
                  ? `${c.connected} de 1 instancia conectada`
                  : `${c.connected} de ${c.total} instancias conectadas`
            }
            segments={
              c.instances && c.instances.length > 0
                ? c.instances.map((i) => i.connected)
                : Array.from({ length: c.total }, (_, i) => i < c.connected)
            }
            expanded={open === c.id}
            onToggle={
              c.instances && c.instances.length > 0
                ? () => setOpen((cur) => (cur === c.id ? null : c.id))
                : undefined
            }
          />
        ))}
      </div>

      {selected?.instances && selected.instances.length > 0 && (
        <InstancesTable name={selected.name} instances={selected.instances} />
      )}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-right">
      <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="text-lg font-bold leading-tight text-navy-900">{value}</p>
    </div>
  );
}

function ComponentTile({
  name,
  status,
  summary,
  detail,
  segments,
  expanded,
  onToggle,
}: {
  name: string;
  status: Status;
  summary: string;
  detail?: string;
  segments: boolean[];
  expanded?: boolean;
  onToggle?: () => void;
}) {
  const st = STATUS_STYLE[status];
  const body: ReactNode = (
    <>
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-semibold text-navy-900">{name}</span>
        <st.Icon className={cn("size-5 shrink-0", st.ink)} aria-label={st.label} />
      </div>
      <div className="mt-3 flex h-2 gap-1" aria-hidden="true">
        {segments.length === 0 ? (
          <span className="h-full flex-1 rounded-full bg-muted" />
        ) : (
          segments.map((ok, i) => (
            <span
              key={i}
              className={cn(
                "h-full flex-1 rounded-full",
                ok ? STATUS_STYLE.ok.bar : STATUS_STYLE.down.bar,
              )}
            />
          ))
        )}
      </div>
      <div className="mt-2 flex items-center justify-between gap-2">
        <span className={cn("text-sm font-medium", st.ink)}>
          {summary}
          {detail && <span className="font-normal text-muted-foreground"> · {detail}</span>}
        </span>
        {onToggle && (
          <ChevronDown
            className={cn(
              "size-4 shrink-0 text-muted-foreground transition-transform",
              expanded && "rotate-180",
            )}
            aria-hidden="true"
          />
        )}
      </div>
    </>
  );
  const cls = cn(
    "rounded-md border px-3.5 py-3 text-left",
    status === "down" || status === "degraded"
      ? cn(st.soft, "border-transparent")
      : "border-border",
  );
  if (!onToggle) return <div className={cls}>{body}</div>;
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={expanded}
      aria-label={`${name}: ${summary}. ${expanded ? "Ocultar" : "Ver"} instancias`}
      className={cn(
        cls,
        "transition-colors hover:border-sky-300",
        expanded && "ring-2 ring-sky-300",
      )}
    >
      {body}
    </button>
  );
}

function InstancesTable({ name, instances }: { name: string; instances: SystemHealthInstance[] }) {
  const sorted = [...instances].sort((a, b) => Number(a.connected) - Number(b.connected));
  return (
    <div className="border-t border-border px-4 pb-4 pt-3">
      <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {name} · {instances.length} {instances.length === 1 ? "instancia" : "instancias"}
      </p>
      <div className="overflow-x-auto rounded-md border border-border">
        <table className="w-full min-w-[640px] text-sm">
          <thead className="bg-muted/40 text-left text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-3 py-2 font-semibold">Estado</th>
              <th className="px-3 py-2 font-semibold">IP</th>
              <th className="px-3 py-2 font-semibold">Versión</th>
              <th className="px-3 py-2 font-semibold">Usuario del componente</th>
              <th className="px-3 py-2 font-semibold">Última conexión</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((i, idx) => {
              const st = STATUS_STYLE[i.connected ? "ok" : "down"];
              return (
                <tr key={idx} className="border-t border-border">
                  <td className="px-3 py-2">
                    <span className={cn("inline-flex items-center gap-1.5 font-medium", st.ink)}>
                      <st.Icon className="size-4" aria-hidden="true" />
                      {i.connected ? "Conectado" : "Desconectado"}
                    </span>
                  </td>
                  <td className="px-3 py-2 tabular-nums">{dash(i.ip)}</td>
                  <td className="px-3 py-2 tabular-nums">{dash(i.version)}</td>
                  <td className="px-3 py-2">{dash(i.user)}</td>
                  <td className="px-3 py-2 tabular-nums">{fmtDateTime(i.last_logon)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
