import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ClipboardPaste, Download, Loader2, Trash2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { PlatformField } from "@/components/PlatformField";
import { SafeField } from "@/components/SafeField";
import { api, apiForm } from "@/lib/api";
import { saveBlob } from "@/lib/download";
import { roleAtLeast } from "@/lib/roles";
import { parseBulkPasteBlocks, type ParsedPasteRow } from "@/lib/bulkPasteParse";
import type {
  BulkCreateExecuteResponse,
  BulkCreatePreviewResponse,
  PlatformsResponse,
  TenantSummary,
} from "@/lib/types";

const MIN_REASON = 8;

function csvField(value: string): string {
  if (/[",\n]/.test(value)) return `"${value.replace(/"/g, '""')}"`;
  return value;
}

/** `LogonDomain` es la propiedad de plataforma que CyberArk muestra como
 *  "Log On To". Con `logonToFromAddress`, las filas sin valor propio la
 *  completan con su Address (cuentas locales de Windows). */
function effectiveLogonTo(r: ParsedPasteRow, logonToFromAddress: boolean): string {
  return r.logonTo.trim() || (logonToFromAddress ? r.address.trim() : "");
}

/** Heurística SÓLO para sugerir (los IDs de plataforma son de cada
 *  cliente, ej. `TIV-WIN-LOC` / `TIV-WIN-DOM`): nunca decide por sí sola. */
const LOCAL_PLATFORM_RE = /(^|[-_ .])(loc|local)([-_ .]|$)/i;
const DOMAIN_PLATFORM_RE = /(^|[-_ .])(dom|domain|dominio)([-_ .]|$)/i;

function rowsToCsvFile(
  rows: ParsedPasteRow[],
  logonToFromAddress: boolean,
  windowsFields: boolean,
): File {
  if (!windowsFields) {
    // Campos Windows ocultos: no se mandan (aunque haya algo tipeado antes).
    rows = rows.map((r) => ({ ...r, logonTo: "", remoteMachines: "" }));
    logonToFromAddress = false;
  }
  const withLogon = rows.some((r) => effectiveLogonTo(r, logonToFromAddress));
  const header = ["safe_name", "platform_id", "user_name", "address", "secret"];
  if (withLogon) header.push("LogonDomain");
  const withRemote = rows.some((r) => r.remoteMachines.trim());
  if (withRemote) header.push("remote_machines");
  const lines = [header.join(",")];
  for (const r of rows) {
    const cols = [r.safeName, r.platformId, r.userName, r.address, r.secret];
    if (withLogon) cols.push(effectiveLogonTo(r, logonToFromAddress));
    if (withRemote) cols.push(r.remoteMachines.split(/[;,\s]+/).filter(Boolean).join(";"));
    lines.push(cols.map(csvField).join(","));
  }
  return new File([lines.join("\n") + "\n"], "pegado.csv", { type: "text/csv" });
}

const TEMPLATE_HEADER = [
  "safe_name",
  "platform_id",
  "user_name",
  "address",
  "name",
  "secret",
  "automatic_management_enabled",
  "manual_management_reason",
  "remote_machines",
  "access_restricted_to_remote_machines",
];
const TEMPLATE_EXAMPLE = ["CajaFuerte", "WinDomain", "jdoe", "10.0.0.1", "", "", "", "", "", ""];

function downloadTemplate() {
  const csv = `${TEMPLATE_HEADER.join(",")}\n${TEMPLATE_EXAMPLE.join(",")}\n`;
  saveBlob(new Blob([csv], { type: "text/csv;charset=utf-8" }), "plantilla-onboarding-cuentas.csv");
}

/** Sin `open`, el componente maneja su propio estado y muestra su botón.
 *  Con `open`/`onOpenChange` lo controla el padre (ej. el botón "Crear
 *  cuenta" con desplegable de la grilla de Cuentas) y `hideTrigger` oculta
 *  el botón propio. */
interface DialogControl {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  hideTrigger?: boolean;
}

/** Botón + diálogo "Importar CSV" del toolbar de Cuentas — onboarding
 *  masivo (Fase 4). Sin cola de jobs (v1 síncrono): una fila que falla no
 *  aborta el resto, mismo criterio que `account.reveal.bulk`. Reusa la
 *  feature `account.create` (misma operación, repetida N veces) — sin
 *  feature-flag nueva. Ver `docs/specs/done/account-bulk-onboard.md`. */
export function AccountBulkCreateDialog({
  tenant,
  ...props
}: { tenant: string } & DialogControl) {
  const qc = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [openState, setOpenState] = useState(false);
  const open = props.open ?? openState;
  const setOpen = (v: boolean) => {
    if (props.open === undefined) setOpenState(v);
    props.onOpenChange?.(v);
  };
  const [mode, setMode] = useState<"file" | "paste">("file");
  const [file, setFile] = useState<File | null>(null);
  const [pasteText, setPasteText] = useState("");
  const [pasteRows, setPasteRows] = useState<ParsedPasteRow[]>([]);
  const [pasteUnmatched, setPasteUnmatched] = useState<string[]>([]);
  const [defaultPlatformId, setDefaultPlatformId] = useState("");
  const [defaultSafeName, setDefaultSafeName] = useState("");
  const [defaultAddress, setDefaultAddress] = useState("");
  const [logonToFromAddress, setLogonToFromAddress] = useState(false);
  // null = automático (según ticket/plataformas); true/false = decisión
  // manual del operador, que gana sobre la detección.
  const [windowsOverride, setWindowsOverride] = useState<boolean | null>(null);
  const [showSecrets, setShowSecrets] = useState(false);
  const [reason, setReason] = useState("");
  const [preview, setPreview] = useState<BulkCreatePreviewResponse | null>(null);
  const [done, setDone] = useState<BulkCreateExecuteResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
  });

  const platforms = useQuery({
    queryKey: ["inv", tenant, "platforms"],
    queryFn: () => api<PlatformsResponse>(`/tenants/${tenant}/platforms`),
    staleTime: 5 * 60_000,
    enabled: mode === "paste",
  });

  const allowed = roleAtLeast(t.data?.my_role, "operator");
  if (!allowed) return null;

  // Detección de cuentas Windows: por el `systemType` real de la plataforma
  // (robusto) y, si no hay dato, por el ID. Local vs dominio sólo por el
  // nombre -> eso sólo se SUGIERE abajo.
  const systemTypeById = new Map(
    (platforms.data?.platforms ?? []).map((p) => [p.general?.id, p.general?.systemType]),
  );
  const isWindowsPlatform = (id: string) =>
    !!id && (systemTypeById.get(id) === "Windows" || /win/i.test(id));
  const hasWindowsRows = pasteRows.some((r) => isWindowsPlatform(r.platformId));
  const hasWindowsData = pasteRows.some((r) => r.logonTo.trim() || r.remoteMachines.trim());
  const windowsFields = windowsOverride ?? (hasWindowsRows || hasWindowsData);
  const localRows = pasteRows.filter(
    (r) => isWindowsPlatform(r.platformId) && LOCAL_PLATFORM_RE.test(r.platformId),
  ).length;
  const domainRows = pasteRows.filter(
    (r) => isWindowsPlatform(r.platformId) && DOMAIN_PLATFORM_RE.test(r.platformId),
  ).length;

  function reset() {
    setOpen(false);
    setMode("file");
    setFile(null);
    setPasteText("");
    setPasteRows([]);
    setPasteUnmatched([]);
    setDefaultPlatformId("");
    setDefaultSafeName("");
    setDefaultAddress("");
    setLogonToFromAddress(false);
    setWindowsOverride(null);
    setShowSecrets(false);
    setReason("");
    setPreview(null);
    setDone(null);
    setError(null);
    if (fileInput.current) fileInput.current.value = "";
  }

  function doParsePaste() {
    const { rows, unmatchedLines } = parseBulkPasteBlocks(pasteText);
    setPasteRows(rows);
    setPasteUnmatched(unmatchedLines);
  }

  function updatePasteRow(i: number, field: keyof ParsedPasteRow, value: string) {
    setPasteRows((rows) => rows.map((r, idx) => (idx === i ? { ...r, [field]: value } : r)));
  }

  function removePasteRow(i: number) {
    setPasteRows((rows) => rows.filter((_, idx) => idx !== i));
  }

  function applyDefaultPlatformToAll() {
    setPasteRows((rows) => rows.map((r) => ({ ...r, platformId: defaultPlatformId })));
  }

  // A diferencia de la plataforma (nunca viene en el ticket), el safe A
  // VECES sí viene por cuenta (ticket-firewall) y a veces se menciona una
  // sola vez para todo el lote en texto libre (ticket-esxi) -- por eso
  // esto sólo completa las filas vacías, nunca pisa un safe ya parseado.
  function applyDefaultAddress(onlyEmpty: boolean) {
    const address = defaultAddress.trim();
    setPasteRows((rows) =>
      rows.map((r) => (onlyEmpty && r.address ? r : { ...r, address })),
    );
  }

  function applyDefaultSafeToEmpty() {
    setPasteRows((rows) =>
      rows.map((r) => (r.safeName ? r : { ...r, safeName: defaultSafeName })),
    );
  }

  async function doPreview() {
    const f = mode === "paste" ? rowsToCsvFile(pasteRows, logonToFromAddress, windowsFields) : file;
    if (!f) return;
    setLoading(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", f);
      setPreview(
        await apiForm<BulkCreatePreviewResponse>(
          `/tenants/${tenant}/accounts/bulk-create/preview`,
          form,
        ),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function confirm() {
    const f = mode === "paste" ? rowsToCsvFile(pasteRows, logonToFromAddress, windowsFields) : file;
    if (!f) return;
    setLoading(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", f);
      form.append("reason", reason);
      const result = await apiForm<BulkCreateExecuteResponse>(
        `/tenants/${tenant}/accounts/bulk-create`,
        form,
      );
      setDone(result);
      qc.invalidateQueries({ queryKey: ["inv", tenant, "accounts"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      {!props.hideTrigger && (
        <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
          <Upload className="size-3.5" />
          Alta masiva de cuentas
        </Button>
      )}

      <Sheet open={open} onOpenChange={(o) => !o && reset()}>
        <SheetContent
          className="flex w-full flex-col gap-4 p-6"
          // Mismo criterio que AccountCreateWizard.tsx: panel grande hasta
          // el borde del sidebar, no un modal chico centrado -- con la
          // tabla de "Pegar filas" (5 columnas + selector de plataforma
          // por fila) el ancho fijo de un Dialog cortaba los textos.
          style={{ maxWidth: "min(calc(100vw - 14rem), 64rem)" }}
        >
          <SheetTitle>Alta masiva de cuentas</SheetTitle>

          {done ? (
            <div className="flex flex-col gap-3">
              <Alert variant={done.failed.length === 0 ? "success" : "warning"}>
                <div className="flex items-start gap-2">
                  <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
                  <span>
                    {done.created} de {done.total} cuentas creadas
                    {done.failed.length > 0 ? `, ${done.failed.length} fallaron` : ""}.
                  </span>
                </div>
              </Alert>
              {done.failed.length > 0 && (
                <div className="max-h-96 overflow-y-auto rounded-lg border border-border">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-border text-left text-muted-foreground">
                        <th className="px-3 py-1.5 font-medium">Fila</th>
                        <th className="px-3 py-1.5 font-medium">Error</th>
                      </tr>
                    </thead>
                    <tbody>
                      {done.failed.map((f) => (
                        <tr key={f.row} className="border-t border-border">
                          <td className="px-3 py-1.5 font-medium">{f.row}</td>
                          <td className="px-3 py-1.5 text-destructive">{f.error}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {!preview && (
                <div className="flex gap-1 self-start rounded-md border border-border p-0.5">
                  <Button
                    variant={mode === "file" ? "default" : "ghost"}
                    size="sm"
                    onClick={() => setMode("file")}
                  >
                    <Upload className="size-3.5" />
                    Importar CSV
                  </Button>
                  <Button
                    variant={mode === "paste" ? "default" : "ghost"}
                    size="sm"
                    onClick={() => setMode("paste")}
                  >
                    <ClipboardPaste className="size-3.5" />
                    Pegar filas
                  </Button>
                </div>
              )}

              {mode === "file" && !preview && (
                <>
                  <p className="text-sm text-muted-foreground">
                    CSV con una cuenta por fila. Columnas obligatorias:{" "}
                    <code>safe_name, platform_id, user_name, address</code>. Opcionales:{" "}
                    <code>
                      name, secret, automatic_management_enabled, manual_management_reason,
                      remote_machines, access_restricted_to_remote_machines
                    </code>
                    . Cualquier otra columna se manda como propiedad de plataforma.
                  </p>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="self-start"
                    onClick={downloadTemplate}
                  >
                    <Download className="size-3.5" />
                    Descargar plantilla
                  </Button>
                  <label className="text-xs text-muted-foreground">
                    Archivo
                    <input
                      ref={fileInput}
                      type="file"
                      accept=".csv"
                      className="mt-1 block w-full text-sm"
                      disabled={preview !== null}
                      onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                    />
                  </label>

                  <Button
                    size="sm"
                    className="self-start"
                    disabled={!file || loading}
                    onClick={doPreview}
                  >
                    {loading && <Loader2 className="size-3.5 animate-spin" />}
                    Previsualizar
                  </Button>
                </>
              )}

              {mode === "paste" && !preview && (
                <>
                  <p className="text-sm text-muted-foreground">
                    Pegue el bloque del ticket tal cual (una cuenta por grupo de líneas{" "}
                    <code>Address</code>/<code>User</code>/<code>Pass</code>, con o sin{" "}
                    <code>Safe</code> por cuenta). La plataforma nunca viene en el ticket, y a
                    veces el safe se menciona una sola vez para todo el lote en el texto libre —
                    ambas se completan abajo, con un valor por defecto para todo el lote y la
                    posibilidad de cambiarlas fila por fila si el lote mezcla varias.
                  </p>
                  <textarea
                    value={pasteText}
                    onChange={(e) => {
                      setPasteText(e.target.value);
                      setPasteRows([]);
                      setPasteUnmatched([]);
                    }}
                    rows={8}
                    className="w-full rounded-md border border-input bg-card px-2 py-1.5 font-mono text-xs"
                    placeholder={"Address: 10.0.0.1\nUser: jdoe\nPass: ...\nSafe: CajaFuerte"}
                  />
                  {pasteText.trim() && pasteRows.length === 0 && (
                    <Button
                      size="sm"
                      variant="outline"
                      className="self-start"
                      onClick={doParsePaste}
                    >
                      Reconocer filas
                    </Button>
                  )}

                  {pasteUnmatched.length > 0 && (
                    <Alert variant="warning">
                      {pasteUnmatched.length} línea(s) no se pudieron interpretar y se
                      ignoraron: <code>{pasteUnmatched.slice(0, 3).join(" / ")}</code>
                      {pasteUnmatched.length > 3 ? "…" : ""}
                    </Alert>
                  )}

                  {pasteRows.length > 0 && (
                    <>
                      <div className="grid grid-cols-2 gap-3">
                        <label className="text-xs font-medium text-muted-foreground">
                          Plataforma por defecto
                          <div className="mt-1 flex items-center gap-2">
                            <div className="flex-1">
                              <PlatformField
                                tenant={tenant}
                                value={defaultPlatformId}
                                onChange={setDefaultPlatformId}
                              />
                            </div>
                            <Button
                              variant="outline"
                              size="sm"
                              type="button"
                              disabled={!defaultPlatformId}
                              onClick={applyDefaultPlatformToAll}
                            >
                              Aplicar a todas
                            </Button>
                          </div>
                        </label>
                        <label className="text-xs font-medium text-muted-foreground">
                          Safe por defecto
                          <div className="mt-1 flex items-center gap-2">
                            <div className="flex-1">
                              <SafeField
                                tenant={tenant}
                                value={defaultSafeName}
                                onChange={setDefaultSafeName}
                              />
                            </div>
                            <Button
                              variant="outline"
                              size="sm"
                              type="button"
                              disabled={!defaultSafeName}
                              onClick={applyDefaultSafeToEmpty}
                            >
                              Aplicar a vacías
                            </Button>
                          </div>
                        </label>
                      </div>
                      <label className="text-xs font-medium text-muted-foreground">
                        Address por defecto
                        <div className="mt-1 flex items-center gap-2">
                          <input
                            value={defaultAddress}
                            onChange={(e) => setDefaultAddress(e.target.value)}
                            placeholder="ej. ARMZACVMAGNT01"
                            className="h-9 flex-1 rounded-md border border-input bg-card px-2 text-sm font-normal text-foreground"
                          />
                          <Button
                            variant="outline"
                            size="sm"
                            type="button"
                            disabled={!defaultAddress.trim()}
                            onClick={() => applyDefaultAddress(true)}
                          >
                            Aplicar a vacías
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            type="button"
                            disabled={!defaultAddress.trim()}
                            onClick={() => applyDefaultAddress(false)}
                          >
                            Aplicar a todas
                          </Button>
                        </div>
                      </label>
                      <p className="text-xs text-muted-foreground">
                        "Aplicar a todas" pisa la plataforma de las {pasteRows.length} filas
                        (nunca viene en el ticket); "Aplicar a vacías" sólo completa el Safe de
                        las filas que no lo trajeron (a veces se menciona una sola vez para todo
                        el lote, en el texto libre del ticket). Corrija después fila por fila si
                        el lote mezcla varias plataformas o safes.
                      </p>

                      <label className="flex items-center gap-2 text-xs text-muted-foreground">
                        <input
                          type="checkbox"
                          checked={windowsFields}
                          onChange={(e) => setWindowsOverride(e.target.checked)}
                        />
                        Cuentas Windows: mostrar "Log On To" y "Remote machines"
                        {windowsOverride === null && windowsFields && " (detectado)"}
                      </label>
                      {windowsFields && (
                        <>
                          <label className="flex items-center gap-2 text-xs text-muted-foreground">
                            <input
                              type="checkbox"
                              checked={logonToFromAddress}
                              onChange={(e) => setLogonToFromAddress(e.target.checked)}
                            />
                            Cuentas locales: completar "Log On To" con el Address (las filas con
                            "Log On To" propio no se tocan)
                          </label>
                          {localRows > 0 && !logonToFromAddress && (
                            <Alert variant="info">
                              {localRows} fila(s) con plataforma que parece de Windows local:
                              suele necesitar "Log On To" igual al Address.{" "}
                              <button
                                type="button"
                                className="underline"
                                onClick={() => setLogonToFromAddress(true)}
                              >
                                Completar con el Address
                              </button>
                            </Alert>
                          )}
                          {domainRows > 0 && (
                            <Alert variant="info">
                              {domainRows} fila(s) con plataforma que parece de dominio: complete
                              "Log On To" (el dominio) y "Remote machines" (una por línea).
                            </Alert>
                          )}
                        </>
                      )}

                      <div className="flex items-center justify-between">
                        <span className="text-xs font-medium text-muted-foreground">
                          Revise/corrija antes de previsualizar
                        </span>
                        <label className="flex items-center gap-1 text-xs text-muted-foreground">
                          <input
                            type="checkbox"
                            checked={showSecrets}
                            onChange={(e) => setShowSecrets(e.target.checked)}
                          />
                          Mostrar contraseñas
                        </label>
                      </div>
                      <div className="max-h-96 overflow-y-auto rounded-lg border border-border">
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="border-b border-border text-left text-muted-foreground">
                              <th className="px-2 py-1.5 font-medium">Address</th>
                              {windowsFields && (
                                <>
                                  <th className="px-2 py-1.5 font-medium">Log On To</th>
                                  <th className="px-2 py-1.5 font-medium">Remote machines</th>
                                </>
                              )}
                              <th className="px-2 py-1.5 font-medium">Usuario</th>
                              <th className="px-2 py-1.5 font-medium">Safe</th>
                              <th className="px-2 py-1.5 font-medium">Contraseña</th>
                              <th className="px-2 py-1.5 font-medium">Plataforma</th>
                              <th className="px-2 py-1.5"></th>
                            </tr>
                          </thead>
                          <tbody>
                            {pasteRows.map((r, i) => (
                              <tr key={i} className="border-t border-border">
                                <td className="px-1 py-1 align-top">
                                  <input
                                    value={r.address}
                                    onChange={(e) => updatePasteRow(i, "address", e.target.value)}
                                    aria-label={`Address fila ${i + 1}`}
                                    className="w-full rounded border border-transparent bg-transparent px-1 py-0.5 placeholder:text-muted-foreground/60 hover:border-input focus:border-input focus:outline-none"
                                  />
                                </td>
                                {windowsFields && (
                                  <>
                                <td className="px-1 py-1 align-top">
                                  <input
                                    value={r.logonTo}
                                    onChange={(e) => updatePasteRow(i, "logonTo", e.target.value)}
                                    aria-label={`Log On To fila ${i + 1}`}
                                    placeholder={logonToFromAddress ? r.address : undefined}
                                    className="w-full rounded border border-transparent bg-transparent px-1 py-0.5 placeholder:text-muted-foreground/60 hover:border-input focus:border-input focus:outline-none"
                                  />
                                </td>
                                <td className="min-w-[220px] px-1 py-1 align-top">
                                  <textarea
                                    value={r.remoteMachines}
                                    onChange={(e) =>
                                      updatePasteRow(i, "remoteMachines", e.target.value)
                                    }
                                    aria-label={`Remote machines fila ${i + 1}`}
                                    rows={4}
                                    placeholder="Una por línea"
                                    className="w-full rounded border border-transparent bg-transparent px-1 py-0.5 placeholder:text-muted-foreground/60 hover:border-input focus:border-input focus:outline-none resize-y"
                                  />
                                </td>
                                  </>
                                )}
                                <td className="px-1 py-1 align-top">
                                  <input
                                    value={r.userName}
                                    onChange={(e) => updatePasteRow(i, "userName", e.target.value)}
                                    aria-label={`Usuario fila ${i + 1}`}
                                    className="w-full rounded border border-transparent bg-transparent px-1 py-0.5 placeholder:text-muted-foreground/60 hover:border-input focus:border-input focus:outline-none"
                                  />
                                </td>
                                <td className="min-w-[160px] px-1 py-1">
                                  <SafeField
                                    tenant={tenant}
                                    value={r.safeName}
                                    onChange={(v) => updatePasteRow(i, "safeName", v)}
                                  />
                                </td>
                                <td className="px-1 py-1">
                                  <input
                                    type={showSecrets ? "text" : "password"}
                                    value={r.secret}
                                    onChange={(e) => updatePasteRow(i, "secret", e.target.value)}
                                    className="w-full rounded border border-transparent bg-transparent px-1 py-0.5 hover:border-input focus:border-input focus:outline-none"
                                  />
                                </td>
                                <td className="min-w-[160px] px-1 py-1">
                                  <PlatformField
                                    tenant={tenant}
                                    value={r.platformId}
                                    onChange={(v) => updatePasteRow(i, "platformId", v)}
                                  />
                                </td>
                                <td className="px-1 py-1">
                                  <button
                                    type="button"
                                    onClick={() => removePasteRow(i)}
                                    aria-label={`Quitar fila ${i + 1}`}
                                    className="text-muted-foreground hover:text-destructive"
                                  >
                                    <Trash2 className="size-3.5" />
                                  </button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>

                      {(pasteRows.some((r) => !r.platformId) || pasteRows.some((r) => !r.safeName)) && (
                        <Alert variant="warning">
                          {pasteRows.some((r) => !r.platformId) &&
                            `Falta elegir plataforma en ${pasteRows.filter((r) => !r.platformId).length} fila(s). `}
                          {pasteRows.some((r) => !r.safeName) &&
                            `Falta elegir Safe en ${pasteRows.filter((r) => !r.safeName).length} fila(s).`}
                        </Alert>
                      )}
                      <Button
                        size="sm"
                        className="self-start"
                        disabled={
                          pasteRows.length === 0 ||
                          pasteRows.some((r) => !r.platformId || !r.safeName) ||
                          loading
                        }
                        onClick={doPreview}
                      >
                        {loading && <Loader2 className="size-3.5 animate-spin" />}
                        Previsualizar
                      </Button>
                    </>
                  )}
                </>
              )}

              {preview && (
                <>
                  <div className="flex items-center gap-2 text-sm">
                    <Badge variant={preview.invalid === 0 ? "success" : "warning"}>
                      {preview.valid} de {preview.total} válidas
                    </Badge>
                  </div>
                  <div className="max-h-96 overflow-y-auto rounded-lg border border-border">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b border-border text-left text-muted-foreground">
                          <th className="px-3 py-1.5 font-medium">Fila</th>
                          <th className="px-3 py-1.5 font-medium">Cuenta</th>
                          <th className="px-3 py-1.5 font-medium">Estado</th>
                        </tr>
                      </thead>
                      <tbody>
                        {preview.rows.map((r) => (
                          <tr key={r.row} className="border-t border-border">
                            <td className="px-3 py-1.5 font-medium">{r.row}</td>
                            <td className="px-3 py-1.5">{r.target ?? "—"}</td>
                            <td className="px-3 py-1.5">
                              {r.ok ? (
                                <Badge variant="success">OK</Badge>
                              ) : (
                                <span className="text-destructive">{r.error}</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  {preview.valid > 0 && (
                    <>
                      <label className="text-xs font-medium text-muted-foreground">
                        Razón (queda en el audit, mínimo {MIN_REASON} caracteres)
                        <textarea
                          value={reason}
                          onChange={(e) => setReason(e.target.value)}
                          rows={2}
                          className="mt-1 w-full rounded-md border border-input bg-card px-2 py-1.5 text-sm"
                          placeholder="ej. onboarding cliente nuevo; ticket OPS-20"
                        />
                      </label>
                      <Button
                        size="sm"
                        className="self-start"
                        disabled={reason.trim().length < MIN_REASON || loading}
                        onClick={confirm}
                      >
                        {loading && <Loader2 className="size-3.5 animate-spin" />}
                        Crear {preview.valid} cuenta(s)
                      </Button>
                    </>
                  )}
                </>
              )}

              {error && <Alert variant="destructive">{error}</Alert>}
            </div>
          )}

          <div className="flex justify-end border-t border-border pt-4">
            <Button variant="ghost" onClick={reset}>
              {done ? "Cerrar" : "Cancelar"}
            </Button>
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
