import { useQuery } from "@tanstack/react-query";
import { Loader2, Printer } from "lucide-react";
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { BarList } from "@/components/BarList";
import { MiniBars } from "@/components/MiniBars";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { ComplianceReason, DashboardResponse, TenantSummary } from "@/lib/types";

const C = {
  teal: "var(--color-line-transformation)",
  red: "var(--color-line-cyberdefense)",
  gold: "var(--color-line-consulting)",
  magenta: "var(--color-line-data)",
  blue: "var(--color-line-cloud)",
  sky: "var(--color-sky-500)",
};

function Kpi({
  label,
  value,
  sub,
  color = "text-navy-900",
  onClick,
}: {
  label: string;
  value: string | number;
  sub?: string;
  color?: string;
  onClick?: () => void;
}) {
  return (
    <Card
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={
        onClick
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") onClick();
            }
          : undefined
      }
      className={cn(onClick && "cursor-pointer transition-colors hover:bg-muted/40")}
    >
      <CardContent className="p-4 pt-4">
        <div className="mb-2 text-xs text-muted-foreground">{label}</div>
        <div className={`text-2xl font-bold leading-tight ${color}`}>
          {typeof value === "number" ? value.toLocaleString("es") : value}
        </div>
        {sub && <div className="mt-1 text-[11px] text-muted-foreground">{sub}</div>}
      </CardContent>
    </Card>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function relTime(iso: string): string {
  const d = new Date(iso);
  const min = Math.round((Date.now() - d.getTime()) / 60000);
  if (min < 1) return "recién";
  if (min < 60) return `hace ${min} min`;
  if (min < 60 * 6) return `hace ${Math.round(min / 60)} h`;
  // hace mucho: fecha y hora absolutas
  return d.toLocaleString("es", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function Dashboard() {
  const navigate = useNavigate();
  const [tenant, setTenant] = useState<string | null>(null);
  const wantRefresh = useRef(false);

  function goToCompliance(reason?: ComplianceReason) {
    if (!tenant) return;
    const suffix = reason ? `&reason=${reason}` : "";
    navigate(`/compliance?tenant=${tenant}${suffix}`);
  }

  const tenants = useQuery({
    queryKey: ["tenants"],
    queryFn: () => api<TenantSummary[]>("/tenants"),
  });

  const q = useQuery({
    queryKey: ["dashboard", tenant],
    queryFn: () => {
      const suffix = wantRefresh.current ? "?refresh=1" : "";
      wantRefresh.current = false;
      return api<DashboardResponse>(`/tenants/${tenant}/dashboard${suffix}`);
    },
    enabled: !!tenant,
    staleTime: 5 * 60_000,
  });

  function doRefresh() {
    wantRefresh.current = true;
    q.refetch();
  }

  const k = q.data?.kpis;
  const ch = q.data?.charts;
  const tenantName = tenants.data?.find((t) => t.id === tenant)?.name ?? tenant;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-[22px] font-bold text-navy-900">
            Dashboard
            {tenantName && <span className="hidden print:inline"> · {tenantName}</span>}
          </h1>
          {q.data && (
            <p className="mt-0.5 text-xs text-muted-foreground">
              {q.data.cached ? "Del caché · " : ""}actualizado {relTime(q.data.generated_at)}
              {q.data.compliance_partial ? " · compliance parcial" : ""}
              <span className="hidden print:inline">
                {" "}
                ({new Date(q.data.generated_at).toLocaleString("es")})
              </span>
            </p>
          )}
        </div>
        <div className="flex items-center gap-3 print:hidden">
          <Select value={tenant ?? undefined} onValueChange={setTenant}>
            <SelectTrigger aria-label="Tenant" className="w-52">
              <SelectValue placeholder="Elija un tenant" />
            </SelectTrigger>
            <SelectContent>
              {(tenants.data ?? []).map((t) => (
                <SelectItem key={t.id} value={t.id}>
                  {t.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {tenant && (
            <>
              <Button variant="outline" onClick={() => window.print()} disabled={!q.data}>
                <Printer className="size-4" />
                Imprimir / PDF
              </Button>
              <Button onClick={doRefresh} disabled={q.isFetching}>
                {q.isFetching && <Loader2 className="animate-spin" />}
                Actualizar
              </Button>
            </>
          )}
        </div>
      </div>

      {tenants.data?.length === 0 && (
        <p className="text-muted-foreground">
          No tiene tenants asignados. Pida acceso a un <code>platform_admin</code>.
        </p>
      )}
      {!tenant && (tenants.data?.length ?? 0) > 0 && (
        <p className="text-muted-foreground">Seleccione un tenant para ver sus métricas.</p>
      )}

      {tenant && q.isLoading && (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          <span>
            Calculando métricas de {tenant} (cuentas, safes, plataformas, compliance,
            componentes)…
          </span>
        </div>
      )}

      {q.isError && (
        <Alert variant="destructive">{(q.error as Error).message}</Alert>
      )}

      {k && ch && (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-7">
            <Kpi label="Cuentas" value={k.accounts_total} />
            <Kpi
              label="Compliance"
              value={`${k.compliance_pct}%`}
              color={k.compliance_pct < 90 ? "text-warning-ink" : "text-line-transformation"}
              onClick={() => goToCompliance()}
            />
            <Kpi
              label="Cuentas en error"
              value={k.accounts_failed}
              sub="secretManagement = Failure"
              color={k.accounts_failed > 0 ? "text-danger-ink" : "text-navy-900"}
            />
            <Kpi
              label="Gestión automática"
              value={`${k.auto_managed_pct}%`}
              sub={`${k.accounts_never_changed} nunca rotadas`}
              color="text-sky-600"
            />
            <Kpi
              label="Safes sin CPM"
              value={k.safes_no_cpm}
              sub={`${k.safes_total} safes`}
              color={k.safes_no_cpm > 0 ? "text-warning-ink" : "text-navy-900"}
            />
            <Kpi
              label="Instancias caídas"
              value={k.components_disconnected}
              sub="componentes"
              color={k.components_disconnected > 0 ? "text-danger-ink" : "text-navy-900"}
            />
            <Kpi
              label="Sesiones activas"
              value={k.sessions_available ? k.sessions_active : "—"}
              sub="ahora mismo (PSM)"
              color="text-sky-600"
            />
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Panel title="Compliance (desglose)">
              <BarList
                data={ch.compliance_breakdown}
                color={C.teal}
                labelWidth={110}
                onItemClick={(label) => {
                  if (label === "Compliant") return;
                  goToCompliance(label as ComplianceReason);
                }}
              />
            </Panel>
            <Panel title="Errores de CPM">
              <BarList data={ch.cpm_errors} color={C.red} labelWidth={150} />
            </Panel>
          </div>

          {k.sessions_available && ch.sessions_by_protocol.length > 0 && (
            <Panel title="Sesiones activas por protocolo">
              <BarList data={ch.sessions_by_protocol} color={C.sky} labelWidth={110} />
            </Panel>
          )}

          <Panel title="Salud de componentes">
            <div className="flex flex-col">
              {ch.components.map((c, i) => (
                <div
                  key={c.label}
                  className={
                    "flex items-center justify-between py-3" +
                    (i < ch.components.length - 1 ? " border-b border-border" : "")
                  }
                >
                  <span className="text-[13px] text-foreground">{c.label}</span>
                  {!c.available ? (
                    <Badge variant="neutral">no aplica</Badge>
                  ) : (
                    <Badge variant={c.connected < c.total ? "danger" : "success"}>
                      {c.connected}/{c.total}
                    </Badge>
                  )}
                </div>
              ))}
            </div>
          </Panel>

          <Panel title="Cuentas por plataforma (top 12)">
            <BarList data={ch.accounts_by_platform} color={C.sky} labelWidth={170} />
          </Panel>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Panel title="Antigüedad del último cambio de contraseña">
              <BarList data={ch.password_age} color={C.magenta} labelWidth={90} />
            </Panel>
            <Panel title="Estado de gestión del secreto">
              <BarList data={ch.management_status} color={C.teal} labelWidth={100} />
            </Panel>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Panel title="Retención de safes (días)">
              <BarList data={ch.safe_retention} color={C.gold} labelWidth={90} maxItems={12} />
            </Panel>
            <Panel title="Altas de cuentas por mes">
              <MiniBars data={ch.accounts_by_month} color={C.blue} />
            </Panel>
          </div>
        </>
      )}
    </div>
  );
}
