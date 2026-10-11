import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { api, apiForm } from "@/lib/api";
import { roleAtLeast } from "@/lib/roles";
import type { DryRunResult, TenantSummary, WriteResult } from "@/lib/types";

const MIN_REASON = 8;

/** Botón + diálogo "Importar plataforma" del toolbar de Plataformas —
 *  crea una plataforma NUEVA a partir de un `.zip` exportado (el mismo
 *  formato que descarga `PlatformExportButton`). Componente propio, no
 *  reusa `<DryRunDialog>`: CyberArk no expone el contenido de un `.zip`
 *  sin importarlo, así que no hay un diff real que previsualizar — sólo
 *  nombre/tamaño del archivo. Sólo si `platform.import` está habilitada
 *  y el operador es `operator+`. Ver `docs/specs/platform-import.md`. */
export function PlatformImportDialog({ tenant }: { tenant: string }) {
  const qc = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [reason, setReason] = useState("");
  const [preview, setPreview] = useState<DryRunResult | null>(null);
  const [done, setDone] = useState<WriteResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
  });

  const allowed = roleAtLeast(t.data?.my_role, "operator");
  if (!allowed) return null;

  function reset() {
    setOpen(false);
    setFile(null);
    setReason("");
    setPreview(null);
    setDone(null);
    setError(null);
    if (fileInput.current) fileInput.current.value = "";
  }

  async function doPreview() {
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("dry_run", "true");
      setPreview(await apiForm<DryRunResult>(`/tenants/${tenant}/platforms/import`, form));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function confirm() {
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("dry_run", "false");
      form.append("reason", reason);
      const result = await apiForm<WriteResult>(`/tenants/${tenant}/platforms/import`, form);
      setDone(result);
      qc.invalidateQueries({ queryKey: ["inv", tenant, "platforms"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <Upload className="size-3.5" />
        Importar plataforma
      </Button>

      <Dialog open={open} onOpenChange={(o) => !o && reset()}>
        <DialogContent className="max-w-lg">
          <DialogTitle>Importar plataforma</DialogTitle>

          {done ? (
            <Alert variant="success">
              <div className="flex items-start gap-2">
                <CheckCircle2 className="mt-0.5 size-4 shrink-0" />
                <span>Plataforma creada.</span>
              </div>
            </Alert>
          ) : (
            <div className="flex flex-col gap-3">
              <p className="text-sm text-muted-foreground">
                Sube el paquete <code>.zip</code> de "Export Platform" de CyberArk (el mismo
                formato que descarga "Descargar configuración"). Crea una plataforma nueva; no
                toca ninguna existente.
              </p>
              <label className="text-xs text-muted-foreground">
                Archivo
                <input
                  ref={fileInput}
                  type="file"
                  accept=".zip"
                  className="mt-1 block w-full text-sm"
                  disabled={preview !== null}
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                />
              </label>

              {!preview && (
                <Button size="sm" className="self-start" disabled={!file || loading} onClick={doPreview}>
                  {loading && <Loader2 className="size-3.5 animate-spin" />}
                  Previsualizar
                </Button>
              )}

              {preview && (
                <>
                  <p className="text-sm">{preview.effect?.replace(/\*\*/g, "")}</p>
                  {preview.warnings.map((w) => (
                    <Alert key={w} variant="warning">
                      {w}
                    </Alert>
                  ))}
                  <label className="text-xs font-medium text-muted-foreground">
                    Razón (queda en el audit, mínimo {MIN_REASON} caracteres)
                    <textarea
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      rows={2}
                      className="mt-1 w-full rounded-md border border-input bg-card px-2 py-1.5 text-sm"
                      placeholder="ej. reimportar plataforma exportada de Globex; ticket OPS-5"
                    />
                  </label>
                  <Button
                    size="sm"
                    className="self-start"
                    disabled={reason.trim().length < MIN_REASON || loading}
                    onClick={confirm}
                  >
                    {loading && <Loader2 className="size-3.5 animate-spin" />}
                    Confirmar
                  </Button>
                </>
              )}

              {error && <Alert variant="destructive">{error}</Alert>}
            </div>
          )}

          <DialogFooter>
            <Button variant="ghost" onClick={reset}>
              {done ? "Cerrar" : "Cancelar"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
