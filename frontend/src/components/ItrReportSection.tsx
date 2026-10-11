import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Loader2, Server, X } from "lucide-react";
import { useRef, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { api, apiBlob, apiForm } from "@/lib/api";
import { saveBlob } from "@/lib/download";
import { roleAtLeast } from "@/lib/roles";
import type { TenantSummary } from "@/lib/types";
import { toast, toastError } from "@/components/ui/toast";

interface InfraFile {
  id: number;
  filename: string;
  size_bytes: number;
  uploaded_by: string;
  uploaded_at: string;
}

/** Mismo `TAB_ORDER` que `app/itr/report_builder.py` (sin "sessions" —
 *  esa la controla el checkbox "Incluir Sesiones", no esta lista) — sólo
 *  cambia qué botones aparecen en la nav del reporte, los datos de una
 *  pestaña excluida se siguen calculando igual (mismo criterio que ITR). */
const OPTIONAL_TABS: { id: string; label: string }[] = [
  { id: "overview", label: "Resumen" },
  { id: "components", label: "Componentes" },
  { id: "users", label: "Users" },
  { id: "safes", label: "Safes" },
  { id: "platforms", label: "Platforms" },
  { id: "accounts", label: "Accounts" },
  { id: "kpis", label: "KPI's" },
  { id: "findings", label: "Hallazgos" },
  { id: "infra", label: "Infraestructura" },
];

/** Archivos `HealthCheck_<host>_<fecha>.json` (el `.ps1` corrido a mano en
 *  cada servidor del cliente) que alimentan la pestaña Infraestructura del
 *  reporte ITR — sin esto, esa pestaña sale con "sin datos". Mismo RBAC
 *  que generar el reporte (`tenant_admin+`). Ver docs/specs/done/itr-report.md. */
function InfraFilesSection({ tenant }: { tenant: string }) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);

  const filesQuery = useQuery({
    queryKey: ["itr-infra-files", tenant],
    queryFn: () => api<{ files: InfraFile[] }>(`/tenants/${tenant}/itr/infra-files`),
  });

  function invalidate() {
    qc.invalidateQueries({ queryKey: ["itr-infra-files", tenant] });
  }

  const upload = useMutation({
    mutationFn: (picked: File[]) => {
      const form = new FormData();
      picked.forEach((f) => form.append("files", f));
      return apiForm(`/tenants/${tenant}/itr/infra-files`, form);
    },
    onSuccess: () => {
      setError(null);
      invalidate();
      toast({ title: "Archivos de infraestructura subidos" });
    },
    onError: (e) => setError((e as Error).message),
  });

  const remove = useMutation({
    mutationFn: (id: number) =>
      api(`/tenants/${tenant}/itr/infra-files/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      invalidate();
      toast({ title: "Archivo borrado" });
    },
    onError: (e) => toastError("No se pudo borrar el archivo", e),
  });

  const files = filesQuery.data?.files ?? [];

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-card p-3">
      <div className="flex items-start gap-2">
        <Server className="mt-0.5 size-4 shrink-0 text-primary" />
        <div>
          <p className="text-sm font-semibold">Infraestructura (opcional)</p>
          <p className="text-xs text-muted-foreground">
            Suba los <code>HealthCheck_&lt;host&gt;_&lt;fecha&gt;.json</code> que genera el
            script <code>.ps1</code> corrido en cada servidor del cliente. Sin archivos, la
            pestaña Infraestructura del reporte sale con "sin datos".
          </p>
        </div>
      </div>

      {files.length > 0 && (
        <ul className="flex flex-col gap-1 text-sm">
          {files.map((f) => (
            <li key={f.id} className="flex items-center justify-between gap-2">
              <span className="truncate">{f.filename}</span>
              <Button
                size="sm"
                variant="ghost"
                disabled={remove.isPending}
                onClick={() => remove.mutate(f.id)}
                aria-label={`Borrar ${f.filename}`}
              >
                <X className="size-3.5" />
              </Button>
            </li>
          ))}
        </ul>
      )}

      <input
        ref={fileRef}
        type="file"
        accept=".json,application/json"
        multiple
        className="hidden"
        onChange={(e) => {
          // `files` es una lista viva: se copia a un arreglo ANTES de limpiar el
          // input, o `value = ""` la vacía y la subida sale sin archivos.
          const picked = Array.from(e.target.files ?? []);
          if (picked.length) upload.mutate(picked);
          e.target.value = "";
        }}
      />
      <div>
        <Button
          size="sm"
          variant="outline"
          disabled={upload.isPending}
          onClick={() => fileRef.current?.click()}
        >
          {upload.isPending && <Loader2 className="size-3.5 animate-spin" />}
          Subir archivo(s)
        </Button>
      </div>
      {error && <Alert variant="destructive">{error}</Alert>}
    </div>
  );
}

/** Reporte ITR completo del tenant (el mismo documento HTML autocontenido
 *  que antes sólo generaba Telemetry-Reporter por CLI) — Accounts,
 *  Compliance, Errores de CPM, Componentes, Safes, Platforms, Users,
 *  KPI's, Licencias, Connector Management y SIA Connectors, todo en un
 *  único archivo listo para compartir con el cliente. Sesiones es opt-in
 *  (1 request extra por cuenta) e Infraestructura depende de los archivos
 *  subidos en `InfraFilesSection` (ver docs/specs/done/itr-report.md).
 *  Sólo visible para `tenant_admin+` — no revela secretos, pero agrega
 *  TODO el estado del tenant en un solo documento. Sin feature-flag (es
 *  lectura, no toca CyberArk). Pestañas a incluir y caché de datos crudos
 *  son opt-in, calcados de `generate_report.py`/`cache_utils.py` de ITR. */
export function ItrReportSection({ tenant }: { tenant: string }) {
  const [reason, setReason] = useState("");
  const [includeSessions, setIncludeSessions] = useState(false);
  const [excludedTabs, setExcludedTabs] = useState<Set<string>>(new Set());
  const [useCache, setUseCache] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  function toggleTab(id: string, included: boolean) {
    setExcludedTabs((prev) => {
      const next = new Set(prev);
      if (included) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
    enabled: !!tenant,
  });

  if (!tenant || !roleAtLeast(t.data?.my_role, "tenant_admin")) return null;

  async function run() {
    setDownloading(true);
    setError(null);
    setDone(false);
    try {
      const { blob, filename } = await apiBlob(`/tenants/${tenant}/reports/itr/export`, {
        method: "POST",
        body: {
          reason: reason.trim() || undefined,
          include_sessions: includeSessions,
          excluded_tabs: Array.from(excludedTabs),
          use_cache: useCache,
        },
      });
      saveBlob(blob, filename);
      setDone(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2 rounded-lg border border-border bg-card p-3">
        <div className="flex items-start gap-2">
          <FileText className="mt-0.5 size-4 shrink-0 text-primary" />
          <div>
            <p className="text-sm font-semibold">Reporte ITR</p>
            <p className="text-xs text-muted-foreground">
              Genera el reporte completo (Resumen, Compliance, Errores de CPM, Componentes,
              Safes, Platforms, Accounts, Users, KPI's, Licencias, Connector Management y SIA
              Connectors) como un único archivo HTML listo para compartir con el cliente. Puede
              tardar 1-2 minutos en un tenant grande.
            </p>
          </div>
        </div>

        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 size-4"
            checked={includeSessions}
            onChange={(e) => setIncludeSessions(e.target.checked)}
          />
          <span>
            Incluir Sesiones
            <span className="block text-xs text-muted-foreground">
              Actividad PSM y sesiones concurrentes — 1 request extra POR CUENTA a CyberArk.
              Puede sumar varios minutos más en un tenant grande.
            </span>
          </span>
        </label>

        <div className="text-xs">
          <span className="font-medium text-muted-foreground">Pestañas a incluir</span>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
            {OPTIONAL_TABS.map((t) => (
              <label key={t.id} className="flex items-center gap-1.5">
                <input
                  type="checkbox"
                  className="size-3.5"
                  checked={!excludedTabs.has(t.id)}
                  onChange={(e) => toggleTab(t.id, e.target.checked)}
                />
                {t.label}
              </label>
            ))}
          </div>
        </div>

        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 size-4"
            checked={useCache}
            onChange={(e) => setUseCache(e.target.checked)}
          />
          <span>
            Usar caché de datos crudos
            <span className="block text-xs text-muted-foreground">
              Sólo para testear cambios al reporte sin repegarle a CyberArk cada vez — no usar
              para el reporte real que se comparte con el cliente (puede quedar desactualizado).
            </span>
          </span>
        </label>

        <label className="text-xs font-medium text-muted-foreground">
          Razón (opcional, queda en el audit)
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={2}
            className="mt-1 w-full rounded-md border border-input bg-card px-2 py-1.5 text-sm"
            placeholder="ej. revisión mensual, ticket OPS-1234"
          />
        </label>

        {error && <Alert variant="destructive">{error}</Alert>}
        {done && <Alert variant="success">Descargado.</Alert>}

        <div>
          <Button variant="default" size="sm" onClick={run} disabled={downloading}>
            {downloading && <Loader2 className="size-3.5 animate-spin" />}
            Generar reporte ITR
          </Button>
        </div>
      </div>

      <InfraFilesSection tenant={tenant} />
    </div>
  );
}
