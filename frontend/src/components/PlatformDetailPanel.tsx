import { useQuery } from "@tanstack/react-query";
import {
  Check,
  Copy,
  CopyPlus,
  Layers,
  Link2,
  MonitorPlay,
  Power,
  RefreshCw,
  Trash2,
  Workflow,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Card } from "@/components/AccountDetailPanel";
import { PlatformConfigExplorer } from "@/components/PlatformConfigExplorer";
import { PlatformActivateSection } from "@/components/PlatformActivateSection";
import { PlatformDeleteSection } from "@/components/PlatformDeleteSection";
import { PlatformDuplicateSection } from "@/components/PlatformDuplicateSection";
import { PlatformExportButton } from "@/components/PlatformExportButton";
import { PlatformPsmSection } from "@/components/PlatformPsmSection";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import { roleAtLeast } from "@/lib/roles";
import type { Platform, TenantSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

type Obj = Record<string, unknown>;
type Tab = "resumen" | "configuracion" | "json";
type Action = "duplicate" | "activate" | "delete" | null;

/** Nombres legibles de los campos conocidos de `GET /API/Platforms`. Lo que
 *  no esté acá se muestra igual, con la clave "humanizada" — CyberArk suma
 *  campos según la versión y no queremos esconder ninguno. */
const LABELS: Record<string, string> = {
  // general
  id: "ID",
  name: "Nombre",
  systemType: "Tipo de sistema",
  platformType: "Clase",
  platformBaseID: "Plataforma base",
  platformBaseType: "Tipo base",
  platformBaseProtocol: "Protocolo base",
  description: "Descripción",
  active: "Activa",
  // credentialsManagement
  allowedSafes: "Safes permitidas (regex)",
  allowManualChange: "Permite cambio manual",
  performPeriodicChange: "Rotación periódica",
  requirePasswordChangeEveryXDays: "Rotar cada (días)",
  allowManualVerification: "Permite verificación manual",
  performPeriodicVerification: "Verificación periódica",
  requirePasswordVerificationEveryXDays: "Verificar cada (días)",
  allowManualReconciliation: "Permite reconciliación manual",
  automaticReconcileWhenUnsynced: "Reconciliar al desincronizarse",
  // sessionManagement
  requirePrivilegedSessionMonitoringAndIsolation: "Exige monitoreo y aislamiento (PSM)",
  recordAndSaveSessionActivity: "Graba la actividad de la sesión",
  // privilegedAccessWorkflows
  requireDualControlPasswordAccessApproval: "Exige aprobación dual para acceder",
  enforceCheckinCheckoutExclusiveAccess: "Acceso exclusivo (check-in/check-out)",
  enforceOnetimePasswordAccess: "Contraseña de un solo uso",
  enforceOneTimePasswordAccess: "Contraseña de un solo uso",
  requireUsersToSpecifyReasonForAccess: "Exige motivo para acceder",
};

/** Campos del PSM que ya muestra `PlatformPsmSection` — no se repiten. */
const PSM_KEYS = new Set(["psmServerId", "PSMServerID", "psmServerID", "PSMServerId", "psmServerName", "PSMServerName"]);

function humanize(key: string): string {
  if (LABELS[key]) return LABELS[key];
  const spaced = key
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/_/g, " ")
    .toLowerCase();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function asObj(v: unknown): Obj {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : {};
}

function YesNo({ value }: { value: boolean }) {
  return value ? (
    <span className="inline-flex items-center gap-1 text-sm font-semibold text-success-ink">
      <Check className="size-3.5" />
      Sí
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-sm text-muted-foreground">
      <X className="size-3.5" />
      No
    </span>
  );
}

function renderValue(v: unknown): React.ReactNode {
  if (v == null || v === "") return <span className="text-muted-foreground">—</span>;
  if (typeof v === "boolean") return <YesNo value={v} />;
  if (Array.isArray(v)) {
    if (v.length === 0) return <span className="text-muted-foreground">—</span>;
    if (v.every((x) => typeof x !== "object")) return v.join(", ");
    return <code className="text-xs">{JSON.stringify(v)}</code>;
  }
  if (typeof v === "object") return <code className="text-xs">{JSON.stringify(v)}</code>;
  return <span className="break-words">{String(v)}</span>;
}

/** Lista clave -> valor de una sección de la plataforma, una fila por
 *  campo. `skip` oculta los que ya se muestran en otro lado. */
function FieldList({ data, skip }: { data: Obj; skip?: Set<string> }) {
  const entries = Object.entries(data).filter(([k]) => !skip?.has(k));
  if (entries.length === 0) {
    return <p className="p-4 text-sm text-muted-foreground">CyberArk no devolvió datos para esta sección.</p>;
  }
  return (
    <dl className="divide-y divide-border">
      {entries.map(([k, v]) => (
        <div key={k} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] items-center gap-3 px-4 py-2.5">
          <dt className="text-sm text-muted-foreground" title={k}>
            {humanize(k)}
          </dt>
          <dd className="text-sm">{renderValue(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Mosaico de la política del CPM: "cada 30 días" o "desactivada". */
function PolicyTile({
  label,
  enabled,
  days,
  hint,
}: {
  label: string;
  enabled: boolean | undefined;
  days?: unknown;
  hint?: string;
}) {
  const known = enabled !== undefined;
  return (
    <div className="flex flex-col gap-1 p-4" title={hint}>
      <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      {!known ? (
        <span className="text-sm font-bold text-muted-foreground">sin datos</span>
      ) : enabled ? (
        <span className="text-lg font-bold leading-tight text-navy-900">
          {days != null && days !== "" ? `cada ${String(days)} días` : "activa"}
        </span>
      ) : (
        <span className="text-lg font-bold leading-tight text-muted-foreground">desactivada</span>
      )}
    </div>
  );
}

/** Panel de detalle de una plataforma — mismo lenguaje visual que
 *  `AccountDetailPanel` (encabezado fijo con eyebrow + título + barra de
 *  acciones, pestañas, tarjetas). Reemplaza la rama de plataformas del
 *  `DetailDrawer` genérico, que mostraba el objeto crudo de CyberArk con
 *  las claves en inglés y las acciones apiladas al final. Todo sale de la
 *  fila que ya trajo la grilla (`GET /platforms`, cacheado) — el único
 *  pedido extra es el PSM asignado, igual que antes. */
export function PlatformDetailPanel({
  tenant,
  platform,
  onClose,
}: {
  tenant: string;
  platform: Platform | null;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<Tab>("resumen");
  const [action, setAction] = useState<Action>(null);
  const [copied, setCopied] = useState(false);

  const platformId = platform?.general?.id ?? "";
  useEffect(() => {
    setTab("resumen");
    setAction(null);
  }, [platformId]);

  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
  });
  const canOperate = roleAtLeast(t.data?.my_role, "operator");
  const canApprove = roleAtLeast(t.data?.my_role, "approver");

  const raw = (platform ?? {}) as Obj;
  const general = asObj(raw.general);
  const cred = asObj(raw.credentialsManagement);
  const session = asObj(raw.sessionManagement ?? raw.privilegedSessionManagement);
  const workflows = asObj(raw.privilegedAccessWorkflows);
  const linked = Array.isArray(raw.linkedAccounts) ? (raw.linkedAccounts as Obj[]) : [];

  const isActive = general.active !== false;
  const heading = String(general.name ?? platformId ?? "Plataforma");
  const bool = (v: unknown) => (typeof v === "boolean" ? v : undefined);

  async function copyJson() {
    try {
      await navigator.clipboard.writeText(JSON.stringify(platform, null, 2));
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* portapapeles no disponible: el JSON sigue visible para copiarlo a mano */
    }
  }

  function toggle(a: Exclude<Action, null>) {
    setAction((cur) => (cur === a ? null : a));
  }

  return (
    <Sheet open={!!platform} onOpenChange={(o) => !o && onClose()}>
      <SheetContent
        className={cn(
          "flex w-full flex-col gap-0 p-0 transition-[max-width] duration-300",
          tab === "configuracion" ? "max-w-5xl" : "max-w-2xl",
        )}
      >
        <SheetTitle className="sr-only">{heading}</SheetTitle>
        {platform && (
          <>
            <div className="sticky top-0 z-10 flex flex-col gap-3 bg-card px-6 pb-0 pt-6">
              <div className="flex items-center gap-1.5 pr-8 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                <Layers className="size-3.5 shrink-0" />
                Plataforma · {(t.data?.name ?? tenant).toString().toUpperCase()}
              </div>

              <div className="flex flex-col gap-1 pr-8">
                <h1 className="text-[21px] font-bold leading-tight text-balance break-words text-navy-900">
                  {heading}
                </h1>
                {typeof general.description === "string" && general.description.trim() && (
                  <p className="text-sm text-muted-foreground">{general.description}</p>
                )}
              </div>

              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <Badge variant={isActive ? "success" : "neutral"}>
                  {isActive ? "activa" : "inactiva"}
                </Badge>
                {heading !== platformId && (
                  <span>
                    ID: <code className="font-semibold text-navy-900">{platformId}</code>
                  </span>
                )}
                {general.systemType != null && (
                  <span>
                    Tipo:{" "}
                    <span className="font-semibold text-navy-900">{String(general.systemType)}</span>
                  </span>
                )}
                {general.platformType != null && (
                  <span>
                    Clase:{" "}
                    <span className="font-semibold text-navy-900">{String(general.platformType)}</span>
                  </span>
                )}
                {platform.psmServerId && (
                  <span>
                    PSM: <span className="font-semibold text-navy-900">{platform.psmServerId}</span>
                  </span>
                )}
              </div>

              <div className="flex flex-wrap items-center gap-2 border-b border-border pb-3">
                <PlatformExportButton tenant={tenant} platformId={platformId} />
                {canOperate && (
                  <Button
                    variant={action === "duplicate" ? "secondary" : "outline"}
                    size="sm"
                    onClick={() => toggle("duplicate")}
                  >
                    <CopyPlus className="size-3.5" />
                    Duplicar
                  </Button>
                )}
                {canApprove && (
                  <Button
                    variant={action === "activate" ? "secondary" : "outline"}
                    size="sm"
                    onClick={() => toggle("activate")}
                  >
                    <Power className="size-3.5" />
                    {isActive ? "Desactivar" : "Activar"}
                  </Button>
                )}
                {canApprove && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="ml-auto border-destructive/30 text-destructive hover:bg-destructive/10"
                    onClick={() => toggle("delete")}
                  >
                    <Trash2 className="size-3.5" />
                    Borrar
                  </Button>
                )}
              </div>

              {action && (
                <div className="pb-1">
                  <PlatformDuplicateSection
                    tenant={tenant}
                    platformId={platformId}
                    open={action === "duplicate"}
                    onClose={() => setAction(null)}
                  />
                  <PlatformActivateSection
                    tenant={tenant}
                    platformId={platformId}
                    active={isActive}
                    open={action === "activate"}
                    onClose={() => setAction(null)}
                  />
                  <PlatformDeleteSection
                    tenant={tenant}
                    platformId={platformId}
                    open={action === "delete"}
                    onClose={() => setAction(null)}
                    onDeleted={onClose}
                  />
                </div>
              )}

              <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
                <TabsList>
                  <TabsTrigger value="resumen">Resumen</TabsTrigger>
                  <TabsTrigger value="configuracion">Configuración</TabsTrigger>
                  <TabsTrigger value="json">JSON</TabsTrigger>
                </TabsList>
              </Tabs>
            </div>

            <div className="flex flex-col gap-4 px-6 py-5">
              {tab === "resumen" && (
                <>
                  <Card icon={<RefreshCw className="size-3.5" />} title="Política del CPM">
                    <div className="grid grid-cols-1 divide-y divide-border sm:grid-cols-3 sm:divide-x sm:divide-y-0">
                      <PolicyTile
                        label="Rotación"
                        enabled={bool(cred.performPeriodicChange)}
                        days={cred.requirePasswordChangeEveryXDays}
                        hint="El CPM cambia la contraseña automáticamente con esta frecuencia"
                      />
                      <PolicyTile
                        label="Verificación"
                        enabled={bool(cred.performPeriodicVerification)}
                        days={cred.requirePasswordVerificationEveryXDays}
                        hint="El CPM chequea que la contraseña guardada siga siendo la del target"
                      />
                      <PolicyTile
                        label="Reconciliación automática"
                        enabled={bool(cred.automaticReconcileWhenUnsynced)}
                        hint="Si la contraseña se desincroniza, el CPM la resetea con la reconcile account"
                      />
                    </div>
                  </Card>

                  <Card icon={<MonitorPlay className="size-3.5" />} title="Gestión de sesiones (PSM)">
                    <PlatformPsmSection tenant={tenant} platformId={platformId} />
                    {Object.keys(session).some((k) => !PSM_KEYS.has(k)) && (
                      <div className="border-t border-border">
                        <FieldList data={session} skip={PSM_KEYS} />
                      </div>
                    )}
                  </Card>

                  {Object.keys(workflows).length > 0 && (
                    <Card icon={<Workflow className="size-3.5" />} title="Flujos de acceso">
                      <FieldList data={workflows} />
                    </Card>
                  )}

                  <Card
                    icon={<Link2 className="size-3.5" />}
                    title="Cuentas vinculadas"
                    headerRight={
                      <span className="text-xs text-muted-foreground">{linked.length}</span>
                    }
                  >
                    {linked.length === 0 ? (
                      <p className="p-4 text-sm text-muted-foreground">
                        La plataforma no define cuentas vinculadas (logon / reconcile).
                      </p>
                    ) : (
                      <ul className="flex flex-wrap gap-2 p-4">
                        {linked.map((l, i) => (
                          <li
                            key={String(l.name ?? i)}
                            className="rounded-full border border-navy-100 bg-navy-50 px-2.5 py-1 text-xs font-semibold text-navy-800"
                          >
                            {String(l.displayName ?? l.name ?? "—")}
                          </li>
                        ))}
                      </ul>
                    )}
                  </Card>
                </>
              )}

              {tab === "configuracion" && (
                <PlatformConfigExplorer tenant={tenant} platformId={platformId} />
              )}

              {tab === "json" && (
                <Card
                  title="Objeto de CyberArk"
                  headerRight={
                    <Button variant="ghost" size="sm" onClick={copyJson}>
                      {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
                      {copied ? "Copiado" : "Copiar"}
                    </Button>
                  }
                >
                  <pre
                    className={cn(
                      "max-h-[60vh] overflow-auto p-4 text-[11px] leading-relaxed",
                      "font-mono text-foreground",
                    )}
                  >
                    {JSON.stringify(platform, null, 2)}
                  </pre>
                </Card>
              )}
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
