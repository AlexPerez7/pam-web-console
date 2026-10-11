import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { DryRunDialog } from "@/components/DryRunDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/** Formulario "Duplicar plataforma" del detalle de una plataforma — riesgo
 *  bajo: crea una copia, no toca la original ni ninguna cuenta (`operator+`,
 *  sin confirmación reforzada). Controlado: el botón que lo abre vive en la
 *  barra de acciones de `PlatformDetailPanel`. Ver
 *  `docs/specs/done/platform-lifecycle.md`. */
export function PlatformDuplicateSection({
  tenant,
  platformId,
  open,
  onClose,
}: {
  tenant: string;
  platformId: string;
  open: boolean;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [dialog, setDialog] = useState<Record<string, unknown> | null>(null);

  function close() {
    setName("");
    setDescription("");
    onClose();
  }

  return (
    <>
      {open && (
        <div className="flex flex-col gap-2 rounded-lg border border-border bg-muted/40 p-3">
          <p className="text-sm font-semibold text-navy-900">Duplicar plataforma</p>
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="text-xs text-muted-foreground">
              Nombre de la copia
              <Input
                className="mt-1 h-8 text-sm"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={`ej. ${platformId}-Copia`}
              />
            </label>
            <label className="text-xs text-muted-foreground">
              Descripción (opcional)
              <Input
                className="mt-1 h-8 text-sm"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </label>
          </div>
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={!name.trim() || name.trim() === platformId}
              onClick={() => setDialog({ name: name.trim(), description: description.trim() })}
            >
              Previsualizar
            </Button>
            <Button variant="ghost" size="sm" onClick={close}>
              Cancelar
            </Button>
          </div>
        </div>
      )}

      <DryRunDialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        path={`/tenants/${tenant}/platforms/${encodeURIComponent(platformId)}/duplicate`}
        changes={dialog ?? {}}
        title="Duplicar plataforma"
        onDone={() => {
          setDialog(null);
          close();
          qc.invalidateQueries({ queryKey: ["inv", tenant, "platforms"] });
        }}
      />
    </>
  );
}
