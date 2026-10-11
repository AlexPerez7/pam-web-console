import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil } from "lucide-react";
import { useState } from "react";
import { CpmField } from "@/components/CpmField";
import { DryRunDialog } from "@/components/DryRunDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { invalidateSafe } from "@/lib/invalidate";
import { roleAtLeast } from "@/lib/roles";
import type { TenantSummary } from "@/lib/types";

type Obj = Record<string, unknown>;
type RetentionType = "days" | "versions";

function currentRetention(safe: Obj): { type: RetentionType; value: number } | null {
  const days = safe.numberOfDaysRetention;
  const versions = safe.numberOfVersionsRetention;
  if (typeof days === "number" && days > 0) return { type: "days", value: days };
  if (typeof versions === "number" && versions > 0) return { type: "versions", value: versions };
  return null;
}

/** Sección "Editar" en el detalle de una safe (Fase 3: descripción, CPM,
 *  retención, OLAC). Sólo aparece si el tenant tiene `safe.update`
 *  habilitada y el operador es `approver+`. Ver `docs/specs/safe-update.md`. */
export function SafeEditSection({
  tenant,
  safeId,
  safe,
}: {
  tenant: string;
  safeId: string;
  safe: Obj;
}) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [dialog, setDialog] = useState<Obj | null>(null);
  const [description, setDescription] = useState("");
  const [managingCpm, setManagingCpm] = useState("");
  const [retentionType, setRetentionType] = useState<RetentionType>("days");
  const [retentionValue, setRetentionValue] = useState("");
  const [olacEnabled, setOlacEnabled] = useState(false);

  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
  });

  const allowed = roleAtLeast(t.data?.my_role, "approver");
  if (!allowed) return null;

  function start() {
    setDescription(String(safe.description ?? ""));
    setManagingCpm(String(safe.managingCPM ?? ""));
    const ret = currentRetention(safe);
    setRetentionType(ret?.type ?? "days");
    setRetentionValue(ret ? String(ret.value) : "");
    setOlacEnabled(!!safe.olacEnabled);
    setEditing(true);
  }

  function preview() {
    const changes: Obj = {};
    if (description !== String(safe.description ?? "")) changes.description = description;
    if (managingCpm !== String(safe.managingCPM ?? "")) changes.managing_cpm = managingCpm;
    const ret = currentRetention(safe);
    if (
      retentionValue &&
      (retentionType !== ret?.type || Number(retentionValue) !== ret?.value)
    ) {
      changes.retention_type = retentionType;
      changes.retention_value = Number(retentionValue);
    }
    if (olacEnabled !== !!safe.olacEnabled) changes.olac_enabled = olacEnabled;
    setDialog(changes);
  }

  return (
    <section className="min-w-[160px] flex-1">
      {!editing ? (
        <Button variant="outline" size="sm" onClick={start}>
          <Pencil className="size-3.5" />
          Editar safe
        </Button>
      ) : (
        <div className="flex flex-col gap-2">
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
          <div className="flex gap-2">
            <Button size="sm" onClick={preview}>
              Previsualizar cambios
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
              Cancelar
            </Button>
          </div>
        </div>
      )}

      <DryRunDialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        path={`/tenants/${tenant}/safes/${encodeURIComponent(safeId)}`}
        method="PUT"
        changes={dialog ?? {}}
        title="Editar safe"
        onDone={() => {
          setEditing(false);
          invalidateSafe(qc, tenant, safeId);
        }}
      />
    </section>
  );
}
