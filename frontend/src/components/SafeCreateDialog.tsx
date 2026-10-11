import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { CpmField } from "@/components/CpmField";
import { DryRunDialog } from "@/components/DryRunDialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { roleAtLeast } from "@/lib/roles";
import type { SafeDefaultsResponse, TenantSummary } from "@/lib/types";

type Obj = Record<string, unknown>;
type RetentionType = "days" | "versions";

/** Botón + diálogo "Crear safe" del toolbar de Safes. Precarga CPM y
 *  retención con los valores más frecuentes entre las safes ya existentes
 *  del tenant. Sólo aparece si `safe.create` está habilitada y el operador
 *  es `operator+`. Ver `docs/specs/plantillas-alta-cuenta-safe.md`. */
export function SafeCreateDialog({ tenant }: { tenant: string }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [safeName, setSafeName] = useState("");
  const [description, setDescription] = useState("");
  const [managingCpm, setManagingCpm] = useState("");
  const [retentionType, setRetentionType] = useState<RetentionType>("days");
  const [retentionValue, setRetentionValue] = useState("");
  const [olacEnabled, setOlacEnabled] = useState(false);
  const [dialog, setDialog] = useState<Obj | null>(null);

  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
  });
  const defaults = useQuery({
    queryKey: ["safe-defaults", tenant],
    queryFn: () => api<SafeDefaultsResponse>(`/tenants/${tenant}/safes/defaults`),
    enabled: open,
  });

  useEffect(() => {
    if (!defaults.data) return;
    const d = defaults.data.defaults;
    if (d.managing_cpm) setManagingCpm(d.managing_cpm);
    if (d.retention_type) setRetentionType(d.retention_type);
    if (d.retention_value) setRetentionValue(String(d.retention_value));
  }, [defaults.data]);

  const allowed = roleAtLeast(t.data?.my_role, "operator");
  if (!allowed) return null;

  function reset() {
    setOpen(false);
    setSafeName("");
    setDescription("");
    setManagingCpm("");
    setRetentionType("days");
    setRetentionValue("");
    setOlacEnabled(false);
  }

  function preview() {
    const changes: Obj = { safe_name: safeName.trim(), olac_enabled: olacEnabled };
    if (description.trim()) changes.description = description.trim();
    if (managingCpm.trim()) changes.managing_cpm = managingCpm.trim();
    if (retentionValue) {
      changes.retention_type = retentionType;
      changes.retention_value = Number(retentionValue);
    }
    setDialog(changes);
  }

  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <Plus className="size-3.5" />
        Crear safe
      </Button>

      <Dialog open={open} onOpenChange={(o) => !o && reset()}>
        <DialogContent className="max-w-md">
          <DialogTitle>Crear safe</DialogTitle>
          <div className="flex flex-col gap-2">
            <label className="text-xs text-muted-foreground">
              Safe Name *
              <Input
                className="mt-1 h-8 text-sm"
                value={safeName}
                onChange={(e) => setSafeName(e.target.value)}
              />
            </label>
            <label className="text-xs text-muted-foreground">
              Descripción
              <Input
                className="mt-1 h-8 text-sm"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </label>
            <label className="text-xs text-muted-foreground">
              CPM
              <CpmField tenant={tenant} value={managingCpm} onChange={setManagingCpm} />
            </label>
            <div className="flex gap-2">
              <label className="flex-1 text-xs text-muted-foreground">
                Retención
                <Select
                  value={retentionType}
                  onValueChange={(v) => setRetentionType(v as RetentionType)}
                >
                  <SelectTrigger className="mt-1 h-8 text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="days">Días</SelectItem>
                    <SelectItem value="versions">Versiones</SelectItem>
                  </SelectContent>
                </Select>
              </label>
              <label className="flex-1 text-xs text-muted-foreground">
                Valor
                <Input
                  type="number"
                  min={1}
                  className="mt-1 h-8 text-sm"
                  value={retentionValue}
                  onChange={(e) => setRetentionValue(e.target.value)}
                />
              </label>
            </div>
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                className="size-3.5 accent-primary"
                checked={olacEnabled}
                onChange={() => setOlacEnabled((v) => !v)}
              />
              OLAC (permisos por objeto)
            </label>

            <Button size="sm" className="self-start" disabled={!safeName.trim()} onClick={preview}>
              Previsualizar
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      <DryRunDialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        path={`/tenants/${tenant}/safes`}
        changes={dialog ?? {}}
        title="Crear safe"
        onDone={() => {
          reset();
          qc.invalidateQueries({ queryKey: ["inv", tenant, "safes"] });
        }}
      />
    </>
  );
}
