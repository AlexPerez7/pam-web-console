import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { DryRunDialog } from "@/components/DryRunDialog";
import { Button } from "@/components/ui/button";

/** Confirmación "Borrar plataforma" del detalle de una plataforma (Fase 3).
 *  Sólo funciona si NINGUNA cuenta la usa — si tiene cuentas, el backend
 *  rechaza con 422 antes de tocar CyberArk. PAM Web Console guarda un backup del
 *  paquete de "Export Platform" antes de borrar. Controlado: el botón que
 *  la abre vive en la barra de acciones de `PlatformDetailPanel`
 *  (`approver+`). Ver `docs/specs/done/platform-lifecycle.md`. */
export function PlatformDeleteSection({
  tenant,
  platformId,
  open,
  onClose,
  onDeleted,
}: {
  tenant: string;
  platformId: string;
  open: boolean;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const qc = useQueryClient();
  const [dialog, setDialog] = useState<Record<string, unknown> | null>(null);

  return (
    <>
      {open && (
        <div className="flex flex-col gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3">
          <p className="text-sm">
            ¿Borrar la plataforma <span className="font-semibold">{platformId}</span>? Sólo
            funciona si ninguna cuenta la usa — PAM Web Console guarda un backup del paquete de
            exportación de CyberArk antes de borrarla.
          </p>
          <div className="flex gap-2">
            <Button variant="destructive" size="sm" onClick={() => setDialog({})}>
              Continuar
            </Button>
            <Button variant="ghost" size="sm" onClick={onClose}>
              Cancelar
            </Button>
          </div>
        </div>
      )}

      <DryRunDialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        path={`/tenants/${tenant}/platforms/${encodeURIComponent(platformId)}`}
        method="DELETE"
        changes={dialog ?? {}}
        title="Borrar plataforma"
        onDone={() => {
          setDialog(null);
          onClose();
          qc.invalidateQueries({ queryKey: ["inv", tenant, "platforms"] });
          onDeleted();
        }}
      />
    </>
  );
}
