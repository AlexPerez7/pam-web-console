import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Loader2, RefreshCw } from "lucide-react";
import { useRef } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import type {
  VendorComponentState,
  VendorStatusNotice,
  VendorStatusSummary,
} from "@/lib/types";

const STATE_LABEL: Record<string, string> = {
  operational: "Operativo",
  degraded_performance: "Rendimiento degradado",
  partial_outage: "Interrupción parcial",
  major_outage: "Interrupción mayor",
  under_maintenance: "En mantenimiento",
};

const STATE_VARIANT: Record<string, BadgeProps["variant"]> = {
  operational: "success",
  degraded_performance: "warning",
  partial_outage: "warning",
  major_outage: "danger",
  under_maintenance: "info",
};

function stateLabel(state: VendorComponentState): string {
  return STATE_LABEL[state] ?? state;
}

function StateBadge({ state }: { state: VendorComponentState }) {
  return <Badge variant={STATE_VARIANT[state] ?? "neutral"}>{stateLabel(state)}</Badge>;
}

function NoticeCard({ notice }: { notice: VendorStatusNotice }) {
  return (
    <a
      href={notice.url}
      target="_blank"
      rel="noreferrer"
      className="block rounded-md border border-border p-3 text-sm hover:bg-muted/50"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Badge variant={notice.type === "incident" ? "danger" : "info"}>
              {notice.type === "incident" ? "Incidente" : "Mantenimiento"}
            </Badge>
            <span className="truncate font-medium text-navy-900">{notice.subject}</span>
          </div>
          {notice.latest_update && (
            <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
              {notice.latest_update}
            </p>
          )}
          <p className="mt-1 text-xs text-muted-foreground">
            {notice.begins_at
              ? `Programado: ${fmtDateTime(notice.begins_at)} — ${fmtDateTime(notice.ends_at)}`
              : notice.began_at
                ? `Desde ${fmtDateTime(notice.began_at)}`
                : null}
          </p>
        </div>
        <ExternalLink className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
      </div>
    </a>
  );
}

/** Estado de la plataforma CyberArk (status.cyberark.com) — el FABRICANTE,
 *  no un tenant de PAM Web Console. Ayuda a distinguir "esto anda mal en la config del
 *  cliente" de "CyberArk tiene una incidencia real en su SaaS" antes de
 *  escalar un ticket. Sin selector de tenant (mismo criterio que
 *  KnowledgeBase.tsx/Audit.tsx): es un dato global. Ver
 *  backend/app/vendor_status.py para el detalle de la API real (SorryApp,
 *  pública, sin auth). */
export default function CyberArkStatus() {
  const wantRefresh = useRef(false);

  const q = useQuery({
    queryKey: ["vendor-status", "cyberark"],
    queryFn: () => {
      const suffix = wantRefresh.current ? "?refresh=1" : "";
      wantRefresh.current = false;
      return api<VendorStatusSummary>(`/vendor-status/cyberark${suffix}`);
    },
    staleTime: 60_000,
    refetchInterval: 5 * 60_000,
  });

  function doRefresh() {
    wantRefresh.current = true;
    q.refetch();
  }

  const data = q.data;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-[22px] font-bold text-navy-900">Estado de CyberArk</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Página de estado pública del fabricante (
            <a
              href="https://status.cyberark.com"
              target="_blank"
              rel="noreferrer"
              className="underline"
            >
              status.cyberark.com
            </a>
            ) — útil para descartar una incidencia del lado de CyberArk antes de escalar un
            ticket.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={doRefresh} disabled={q.isFetching}>
          <RefreshCw className={q.isFetching ? "size-3.5 animate-spin" : "size-3.5"} />
          Actualizar
        </Button>
      </div>

      {q.isLoading && (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          <span>Consultando status.cyberark.com…</span>
        </div>
      )}

      {q.isError && <Alert variant="destructive">{(q.error as Error).message}</Alert>}

      {data && (
        <>
          <Card>
            <CardContent className="flex items-center justify-between gap-4 py-4">
              <div>
                <div className="flex items-center gap-2">
                  <StateBadge state={data.page_state} />
                  <span className="font-medium text-navy-900">{data.page_state_text}</span>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  Actualizado {fmtDateTime(data.updated_at)}
                  {data.cached ? " (caché)" : ""}
                </p>
              </div>
            </CardContent>
          </Card>

          <div>
            <h2 className="mb-2 text-sm font-semibold text-navy-900">Productos</h2>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {data.components.map((c) => (
                <a
                  key={c.id}
                  href={c.url ?? "https://status.cyberark.com"}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center justify-between rounded-md border border-border px-3 py-2 text-sm hover:bg-muted/50"
                >
                  <span className="truncate text-navy-900">{c.name}</span>
                  <StateBadge state={c.state} />
                </a>
              ))}
            </div>
          </div>

          <div>
            <h2 className="mb-2 text-sm font-semibold text-navy-900">
              Incidentes y mantenimientos en curso
            </h2>
            {data.active_notices.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Sin incidencias ni mantenimientos activos.
              </p>
            ) : (
              <div className="flex flex-col gap-2">
                {data.active_notices.map((n) => (
                  <NoticeCard key={n.id} notice={n} />
                ))}
              </div>
            )}
          </div>

          {data.upcoming_notices.length > 0 && (
            <div>
              <h2 className="mb-2 text-sm font-semibold text-navy-900">
                Próximos mantenimientos programados
              </h2>
              <div className="flex flex-col gap-2">
                {data.upcoming_notices.map((n) => (
                  <NoticeCard key={n.id} notice={n} />
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
