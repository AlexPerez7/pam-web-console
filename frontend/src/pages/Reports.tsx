import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Check,
  Clock,
  Download,
  FileSpreadsheet,
  FileText,
  Loader2,
  PanelLeftClose,
  PanelLeftOpen,
  Play,
  Search,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { CredentialsBackupSection } from "@/components/CredentialsBackupSection";
import { DataTable, type Column } from "@/components/DataTable";
import { ItrReportSection } from "@/components/ItrReportSection";
import { MultiSelectField } from "@/components/MultiSelectField";
import { UserSearchField } from "@/components/UserSearchField";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toastError } from "@/components/ui/toast";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api, apiBlob } from "@/lib/api";
import { saveBlob } from "@/lib/download";
import { fmtDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import type {
  ReportColumn,
  ReportInfo,
  ReportJobQueued,
  ReportJobStatus,
  ReportParam,
  ReportResult,
  TenantSummary,
} from "@/lib/types";

type Row = Record<string, unknown>;

function cell(kind: ReportColumn["kind"], v: unknown) {
  if (kind === "date") return fmtDate(v);
  if (kind === "bool")
    return v ? <Check className="size-3.5 text-success-ink" /> : <span className="text-muted-foreground">—</span>;
  return v == null || v === "" ? "—" : String(v);
}
function sortValue(kind: ReportColumn["kind"], v: unknown): string | number | null {
  if (v == null) return null;
  if (kind === "bool") return v ? 1 : 0;
  if (kind === "date" || kind === "number") return Number(v) || 0;
  return String(v);
}

// Orden fijo de categorías del catálogo (no alfabético -- de más frecuente a
// más nicho). "Otros" es el fallback de un reporte sin categoría asignada.
const CATEGORY_ORDER = ["Cuentas", "Safes", "Identity y Licencias", "Infraestructura", "Otros"];

function groupByCategory(reports: ReportInfo[]): [string, ReportInfo[]][] {
  const byCat = new Map<string, ReportInfo[]>();
  for (const r of reports) {
    const cat = r.category || "Otros";
    if (!byCat.has(cat)) byCat.set(cat, []);
    byCat.get(cat)!.push(r);
  }
  return CATEGORY_ORDER.filter((c) => byCat.has(c)).map((c) => [c, byCat.get(c)!]);
}

// Preferencia por navegador: catálogo oculto para dar todo el ancho a la tabla.
const CATALOG_KEY = "pam:reports-catalog-collapsed";

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(CATALOG_KEY) === "1";
  } catch {
    return false;
  }
}
function writeCollapsed(v: boolean) {
  try {
    localStorage.setItem(CATALOG_KEY, v ? "1" : "0");
  } catch {
    /* sin storage: la preferencia no se recuerda */
  }
}

function fmtMetaLabel(k: string) {
  const s = k.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}
function fmtMetaValue(v: unknown) {
  return typeof v === "number" ? v.toLocaleString("es") : String(v);
}

/**
 * Página Reportes. Diseño en dos columnas: el catálogo (agrupado por
 * categoría, con búsqueda) queda fijo a la izquierda y la configuración del
 * reporte elegido (parámetros + Ejecutar) y su resultado aparecen al lado,
 * sin tener que bajar. El tenant se elige una sola vez en el encabezado y
 * vale para las dos pestañas. El catálogo se puede ocultar para dar todo el
 * ancho a la tabla de resultados.
 */
export default function Reports() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [reportName, setReportName] = useState(searchParams.get("report") || "");
  const [tenant, setTenant] = useState(searchParams.get("tenant") || "");
  const [values, setValues] = useState<Record<string, string>>(() => {
    const init: Record<string, string> = {};
    for (const [k, v] of searchParams.entries()) {
      if (k !== "report" && k !== "tenant") init[k] = v;
    }
    return init;
  });
  const [catalogFilter, setCatalogFilter] = useState("");
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [exporting, setExporting] = useState<"csv" | "xlsx" | null>(null);

  function toggleCatalog() {
    setCollapsed((c) => {
      writeCollapsed(!c);
      return !c;
    });
  }

  const catalog = useQuery({ queryKey: ["reports"], queryFn: () => api<ReportInfo[]>("/reports") });
  const tenants = useQuery({
    queryKey: ["tenants"],
    queryFn: () => api<TenantSummary[]>("/tenants"),
  });

  const report = catalog.data?.find((r) => r.name === reportName);
  const tenantName = tenants.data?.find((t) => t.id === tenant)?.name ?? tenant;

  const visibleReports = useMemo(() => {
    const q = catalogFilter.trim().toLowerCase();
    const all = catalog.data ?? [];
    if (!q) return all;
    return all.filter(
      (r) => r.label.toLowerCase().includes(q) || r.description.toLowerCase().includes(q),
    );
  }, [catalog.data, catalogFilter]);

  const activeReport = searchParams.get("report");
  const activeTenant = searchParams.get("tenant");
  const activeQ = useMemo(() => {
    const p = new URLSearchParams();
    for (const [k, v] of searchParams.entries()) {
      if (k !== "report" && k !== "tenant") p.set(k, v);
    }
    return p.toString();
  }, [searchParams]);

  // Sólo se muestra el resultado del reporte elegido: al cambiar de reporte
  // en el catálogo, el resultado anterior deja de verse.
  const ran =
    activeReport && activeTenant && activeReport === reportName
      ? { name: activeReport, tenant: activeTenant, q: activeQ }
      : null;

  const run = useQuery({
    queryKey: ["report", ran?.tenant, ran?.name, ran?.q],
    queryFn: () =>
      api<ReportResult>(
        `/tenants/${ran!.tenant}/reports/${ran!.name}${ran!.q ? `?${ran!.q}` : ""}`,
      ),
    enabled: !!ran && !report?.heavy,
    staleTime: 60_000,
  });

  // Reportes "heavy" no corren síncrono en el request -- se encolan como job
  // ARQ y se pollea hasta que termina. No usan el flujo de arriba
  // (searchParams -> GET).
  const [jobId, setJobId] = useState<string | null>(null);
  // tenant + parámetros con que se encoló el último job, para rotular su
  // resultado aunque después se edite el formulario
  const [heavyRun, setHeavyRun] = useState<{ tenant: string; q: string } | null>(null);

  /** Sólo los parámetros del reporte elegido: un valor que quedó cargado de
   *  otro reporte no viaja en la query. */
  function paramsQuery(): string {
    const p = new URLSearchParams();
    for (const param of report?.params ?? []) {
      const v = values[param.key];
      if (v) p.set(param.key, v);
    }
    return p.toString();
  }

  const runJob = useMutation({
    mutationFn: () => {
      const q = paramsQuery();
      return api<ReportJobQueued>(
        `/tenants/${tenant}/reports/${reportName}/run-job${q ? `?${q}` : ""}`,
        { method: "POST" },
      );
    },
    onSuccess: (d) => setJobId(d.job_id),
  });

  const jobStatus = useQuery({
    queryKey: ["report-job", tenant, reportName, jobId],
    queryFn: () => api<ReportJobStatus>(`/tenants/${tenant}/reports/${reportName}/jobs/${jobId}`),
    enabled: !!jobId,
    refetchInterval: (q) => (q.state.data?.status === "complete" ? false : 3000),
  });

  const heavyResult =
    jobStatus.data?.status === "complete" ? (jobStatus.data as ReportResult) : undefined;
  const displayResult = report?.heavy ? heavyResult : ran ? run.data : undefined;
  const heavyPending = !!report?.heavy && (runJob.isPending || (!!jobId && !heavyResult));
  const running = report?.heavy ? heavyPending : run.isFetching;

  const columns: Column<Row>[] = useMemo(
    () =>
      (displayResult?.columns ?? []).map((c) => ({
        key: c.key,
        header: c.label,
        render: (r) => cell(c.kind, r[c.key]),
        sortValue: (r) => sortValue(c.kind, r[c.key]),
        filterable: c.kind === "text" || c.kind === "number",
      })),
    [displayResult],
  );

  function selectReport(name: string) {
    setReportName(name);
    setJobId(null);
    setHeavyRun(null);
    runJob.reset();
  }

  function execute() {
    if (!report || !tenant) return;
    if (report.heavy) {
      setJobId(null);
      setHeavyRun({ tenant, q: paramsQuery() });
      runJob.reset();
      runJob.mutate();
      return;
    }
    const nextParams = new URLSearchParams(paramsQuery());
    nextParams.set("report", reportName);
    nextParams.set("tenant", tenant);
    setSearchParams(nextParams);
  }

  async function exportFile(format: "csv" | "xlsx") {
    const t = report?.heavy ? tenant : ran?.tenant;
    const n = report?.heavy ? reportName : ran?.name;
    const q = report?.heavy ? paramsQuery() : ran?.q;
    if (!t || !n) return;
    setExporting(format);
    try {
      const { blob, filename } = await apiBlob(
        `/tenants/${t}/reports/${n}/export?format=${format}${q ? `&${q}` : ""}`,
      );
      saveBlob(blob, filename);
    } catch (e) {
      toastError("No se pudo exportar el reporte", e);
    } finally {
      setExporting(null);
    }
  }

  // Qué se consultó: tenant y parámetros con que se generó el resultado que
  // está a la vista (no los del formulario, que pueden haber cambiado).
  const shown = report?.heavy ? heavyRun : ran;
  const shownTenantName = tenants.data?.find((t) => t.id === shown?.tenant)?.name ?? shown?.tenant;
  const shownParams = useMemo(() => {
    const q = new URLSearchParams(shown?.q ?? "");
    return (report?.params ?? []).flatMap((p) => {
      const v = q.get(p.key);
      if (!v) return [];
      const text = v
        .split(",")
        .map((o) => p.option_labels?.[o] ?? o)
        .join(", ");
      return [{ key: p.key, label: p.label, text }];
    });
  }, [report, shown?.q]);

  const missing = [
    ...(tenant ? [] : ["tenant"]),
    ...(report?.params ?? []).filter((p) => p.required && !values[p.key]).map((p) => p.label),
  ];
  const disableExecute = !report || missing.length > 0 || running;

  const setValue = (key: string, v: string) => setValues((s) => ({ ...s, [key]: v }));

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[22px] font-bold text-navy-900">Reportes y Operaciones</h1>
          <p className="text-sm text-muted-foreground">
            Elija el tenant y el reporte; la configuración y el resultado aparecen al lado.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Tenant
          </span>
          <Select value={tenant} onValueChange={setTenant}>
            <SelectTrigger className="w-56" aria-label="Tenant">
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
        </div>
      </div>

      <Tabs defaultValue="reports" className="w-full">
        <TabsList className="mb-4">
          <TabsTrigger value="reports">Reportes Analíticos</TabsTrigger>
          <TabsTrigger value="advanced">Exportaciones y Backups</TabsTrigger>
        </TabsList>

        <TabsContent value="reports">
          <div
            className={cn(
              "grid grid-cols-1 items-start gap-4",
              !collapsed && "lg:grid-cols-[19rem_minmax(0,1fr)]",
            )}
          >
            {!collapsed && (
              <aside className="flex flex-col rounded-lg border border-border bg-card lg:sticky lg:top-4 lg:max-h-[calc(100vh-7rem)]">
                <div className="flex items-center gap-2 border-b border-border p-3">
                  <div className="relative flex-1">
                    <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                      value={catalogFilter}
                      onChange={(e) => setCatalogFilter(e.target.value)}
                      placeholder="Buscar reporte"
                      aria-label="Buscar reporte"
                      className="h-9 pl-8"
                    />
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={toggleCatalog}
                    aria-label="Ocultar catálogo"
                    title="Ocultar catálogo (más espacio para la tabla)"
                    className="hidden lg:inline-flex"
                  >
                    <PanelLeftClose className="size-4" />
                  </Button>
                </div>
                <nav
                  aria-label="Catálogo de reportes"
                  className="flex flex-col gap-4 overflow-y-auto p-3"
                >
                  {catalog.isLoading && (
                    <p className="text-sm text-muted-foreground">Cargando catálogo…</p>
                  )}
                  {catalog.isError && (
                    <p className="text-sm text-danger-ink">{(catalog.error as Error).message}</p>
                  )}
                  {catalog.data && visibleReports.length === 0 && (
                    <p className="text-sm text-muted-foreground">Ningún reporte coincide.</p>
                  )}
                  {groupByCategory(visibleReports).map(([cat, reports]) => (
                    <div key={cat} className="flex flex-col gap-1">
                      <h3 className="px-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        {cat}
                      </h3>
                      {reports.map((r) => {
                        const active = reportName === r.name;
                        return (
                          <button
                            key={r.name}
                            type="button"
                            onClick={() => selectReport(r.name)}
                            aria-current={active ? "true" : undefined}
                            className={cn(
                              "flex flex-col gap-0.5 rounded-md border-l-2 px-2.5 py-2 text-left transition-colors",
                              active
                                ? "border-sky-500 bg-accent"
                                : "border-transparent hover:bg-muted/50",
                            )}
                          >
                            <span className="flex items-center gap-1.5 text-sm font-medium text-navy-900">
                              {r.label}
                              {r.heavy && (
                                <Clock
                                  className="size-3.5 shrink-0 text-muted-foreground"
                                  aria-label="Corre en segundo plano"
                                />
                              )}
                            </span>
                            <span className="line-clamp-2 text-xs text-muted-foreground">
                              {r.description}
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  ))}
                </nav>
              </aside>
            )}

            <section className="flex min-w-0 flex-col gap-4">
              {!report ? (
                <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border bg-card px-6 py-16 text-center">
                  <FileText className="size-8 text-muted-foreground" />
                  <p className="text-sm text-muted-foreground">
                    Elija un reporte del catálogo para configurarlo y ejecutarlo.
                  </p>
                  {collapsed && (
                    <Button variant="outline" size="sm" onClick={toggleCatalog}>
                      <PanelLeftOpen className="size-4" />
                      Mostrar catálogo
                    </Button>
                  )}
                </div>
              ) : (
                <div className="rounded-lg border border-border bg-card">
                  <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border p-4">
                    <div className="min-w-0">
                      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        {report.category || "Otros"}
                      </p>
                      <h2 className="mt-0.5 flex flex-wrap items-center gap-2 text-lg font-semibold text-navy-900">
                        {report.label}
                        {report.heavy && (
                          <Badge variant="info" className="gap-1">
                            <Clock className="size-3" />
                            En segundo plano
                          </Badge>
                        )}
                      </h2>
                      <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
                        {report.description}
                      </p>
                    </div>
                    {collapsed && (
                      <Button variant="outline" size="sm" onClick={toggleCatalog}>
                        <PanelLeftOpen className="size-4" />
                        Cambiar reporte
                      </Button>
                    )}
                  </div>

                  {report.params.length > 0 && (
                    <div className="grid grid-cols-1 gap-x-4 gap-y-3 p-4 sm:grid-cols-2 xl:grid-cols-3">
                      {report.params.map((p) => (
                        <ParamField
                          key={p.key}
                          param={p}
                          tenant={tenant}
                          value={values[p.key] ?? ""}
                          onChange={(v) => setValue(p.key, v)}
                        />
                      ))}
                    </div>
                  )}

                  <div className="flex flex-wrap items-center gap-3 rounded-b-lg border-t border-border bg-muted/30 px-4 py-3">
                    <Button onClick={execute} disabled={disableExecute}>
                      {running ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <Play className="size-4" />
                      )}
                      Ejecutar
                    </Button>
                    <span className="text-xs text-muted-foreground">
                      {missing.length > 0
                        ? `Falta: ${missing.join(", ")}.`
                        : `Sobre ${tenantName}.${report.heavy ? " Puede tardar varios minutos." : ""}`}
                    </span>
                  </div>
                </div>
              )}

              {ran && run.isError && (
                <Alert variant="destructive">{(run.error as Error).message}</Alert>
              )}
              {runJob.isError && (
                <Alert variant="destructive">{(runJob.error as Error).message}</Alert>
              )}
              {jobStatus.isError && (
                <Alert variant="destructive">{(jobStatus.error as Error).message}</Alert>
              )}

              {heavyPending && (
                <Alert>
                  <div className="flex items-center gap-2">
                    <Loader2 className="size-4 animate-spin" />
                    Procesando en segundo plano (puede tardar varios minutos con miles de cuentas) —
                    {jobStatus.data?.status === "in_progress"
                      ? " en curso…"
                      : jobStatus.data?.status === "queued"
                        ? " en cola…"
                        : " encolando…"}
                  </div>
                </Alert>
              )}

              {displayResult && (
                <>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-lg border border-border bg-card px-4 py-2.5 text-sm">
                    <span className="text-muted-foreground">
                      Resultado de <span className="font-medium text-navy-900">{report?.label}</span>
                      {shownTenantName && (
                        <>
                          {" "}
                          en <span className="font-medium text-navy-900">{shownTenantName}</span>
                        </>
                      )}
                    </span>
                    {shownParams.map((p) => (
                      <Badge key={p.key} variant="info" className="max-w-full">
                        <span className="truncate">
                          {p.label}: <span className="font-semibold">{p.text}</span>
                        </span>
                      </Badge>
                    ))}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Stat label="Filas" value={displayResult.rows.length} />
                    {Object.entries(displayResult.meta)
                      .filter(([, v]) => v === null || typeof v !== "object")
                      .map(([k, v]) => (
                        <Stat key={k} label={fmtMetaLabel(k)} value={v} />
                      ))}
                  </div>
                  {Array.isArray(displayResult.meta.by_system) && (
                    <SystemSummary rows={displayResult.meta.by_system as SystemRow[]} />
                  )}
                  <DataTable
                    rows={displayResult.rows}
                    columns={columns}
                    tableId={`report-${reportName}`}
                    getRowKey={(_r, i) => i}
                    filterOn={(r) =>
                      (displayResult?.columns ?? []).map((c) => r[c.key] as string | number | null)
                    }
                    filterPlaceholder="filtrar el reporte"
                    minWidth={Math.max(700, columns.length * 130)}
                    toolbar={
                      <div className="flex flex-wrap items-center gap-2">
                        {report?.formats?.includes("xlsx") && (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => exportFile("xlsx")}
                            disabled={!!exporting}
                          >
                            {exporting === "xlsx" ? (
                              <Loader2 className="size-3.5 animate-spin" />
                            ) : (
                              <FileSpreadsheet className="size-3.5" />
                            )}
                            Exportar Excel
                          </Button>
                        )}
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => exportFile("csv")}
                          disabled={!!exporting}
                        >
                          {exporting === "csv" ? (
                            <Loader2 className="size-3.5 animate-spin" />
                          ) : (
                            <Download className="size-3.5" />
                          )}
                          Exportar CSV
                        </Button>
                      </div>
                    }
                  />
                </>
              )}
            </section>
          </div>
        </TabsContent>

        <TabsContent value="advanced" className="flex flex-col gap-4">
          <div className="rounded-lg border border-border bg-card p-4">
            <h2 className="text-lg font-semibold text-navy-900">Exportaciones y Backups</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Operaciones puntuales sobre todo el tenant a la vez: el reporte ITR mensual y el
              backup cifrado de credenciales. A diferencia de los reportes analíticos, aquí se
              procesa el inventario completo en una sola corrida.
            </p>
          </div>
          {tenant ? (
            <div className="flex flex-col gap-4">
              <ItrReportSection tenant={tenant} />
              <CredentialsBackupSection tenant={tenant} />
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              Elija un tenant arriba para ver las operaciones disponibles.
            </p>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}

interface SystemRow {
  system: string;
  total: number;
  cpm_errors: number;
  overdue: number;
  failing: number;
  pct: number;
}

/** Resumen por tipo de sistema (`meta.by_system`, hoy sólo de
 *  `rotation-failures-by-system`): lo mismo que la Portada del Excel. */
function SystemSummary({ rows }: { rows: SystemRow[] }) {
  const n = (v: number) => v.toLocaleString("es");
  return (
    <div className="overflow-x-auto rounded-md border border-border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-muted/40 text-left text-xs uppercase text-muted-foreground">
            <th className="px-3 py-1.5 font-medium">Sistema</th>
            <th className="px-3 py-1.5 text-right font-medium">Cuentas</th>
            <th className="px-3 py-1.5 text-right font-medium">Error del CPM</th>
            <th className="px-3 py-1.5 text-right font-medium">Vencidas</th>
            <th className="px-3 py-1.5 text-right font-medium">Con problemas</th>
            <th className="px-3 py-1.5 text-right font-medium">%</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.system} className="border-b border-border/60 last:border-0">
              <td className="px-3 py-1.5 font-medium">{r.system}</td>
              <td className="px-3 py-1.5 text-right">{n(r.total)}</td>
              <td className="px-3 py-1.5 text-right">{n(r.cpm_errors)}</td>
              <td className="px-3 py-1.5 text-right">{n(r.overdue)}</td>
              <td className="px-3 py-1.5 text-right">{n(r.failing)}</td>
              <td
                className={`px-3 py-1.5 text-right font-semibold ${r.failing ? "text-danger-ink" : "text-success-ink"}`}
              >
                {r.pct.toLocaleString("es", { minimumFractionDigits: 1 })} %
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="rounded-md border border-border bg-card px-3 py-1.5">
      <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="text-sm font-semibold text-navy-900">{fmtMetaValue(value)}</p>
    </div>
  );
}

/** Un parámetro del reporte, a todo el ancho de su celda de la grilla. */
function ParamField({
  param: p,
  tenant,
  value,
  onChange,
}: {
  param: ReportParam;
  tenant: string;
  value: string;
  onChange: (v: string) => void;
}) {
  let control;
  if (p.type === "user") {
    control = (
      <UserSearchField
        tenant={tenant}
        value={value}
        onChange={onChange}
        placeholder={p.help ?? "Buscar usuario…"}
        ariaLabel={p.label}
        className="w-full"
      />
    );
  } else if (p.type === "multiselect" && p.options) {
    control = (
      <MultiSelectField
        options={p.options}
        labels={p.option_labels}
        value={value}
        onChange={onChange}
        placeholder={p.help ?? "Todos"}
        ariaLabel={p.label}
        className="w-full"
      />
    );
  } else if (p.type === "select" && p.options) {
    control = (
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="mt-1 w-full" aria-label={p.label}>
          <SelectValue placeholder={p.help ?? "—"} />
        </SelectTrigger>
        <SelectContent>
          {p.options.map((o) => (
            <SelectItem key={o} value={o}>
              {p.option_labels?.[o] ?? o}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  } else {
    control = (
      <Input
        type={p.type === "number" ? "number" : "text"}
        className="mt-1 h-9 w-full"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={p.help ?? ""}
      />
    );
  }
  return (
    <label className="flex min-w-0 flex-col text-xs font-medium text-navy-900">
      <span>
        {p.label}
        {p.required && (
          <span className="ml-0.5 text-danger-ink" aria-hidden="true">
            *
          </span>
        )}
      </span>
      {control}
    </label>
  );
}
