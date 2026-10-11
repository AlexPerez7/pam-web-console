import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { DryRunDialog } from "@/components/DryRunDialog";
import { Button } from "@/components/ui/button";

/** Confirmación "Activar / Desactivar plataforma" del detalle de una
 *  plataforma (Fase 3). Afecta a TODAS las cuentas de la plataforma (el
 *  CPM deja/retoma de gestionarlas), así que va con confirmación inline +
 *  `<DryRunDialog>` de confirmación reforzada. Controlado: el botón que la
 *  abre vive en la barra de acciones de `PlatformDetailPanel`, que ya
 *  resolvió el rol (`approver+`). Ver `docs/specs/done/platform-lifecycle.md`. */
export function PlatformActivateSection({
  tenant,
  platformId,
  active,
  open,
  onClose,
}: {
  tenant: string;
  platformId: string;
  active?: boolean;
  open: boolean;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [dialog, setDialog] = useState<Record<string, unknown> | null>(null);

  const isActive = active !== false;
  const verb = isActive ? "Desactivar" : "Activar";
  const path = `/tenants/${tenant}/platforms/${encodeURIComponent(platformId)}/${
    isActive ? "deactivate" : "activate"
  }`;

  return (
    <>
      {open && (
        <div className="flex flex-col gap-2 rounded-lg border border-[#f0d58a] bg-[#fdf7e6] p-3">
          <p className="text-sm">
            ¿{verb} la plataforma <span className="font-semibold">{platformId}</span>? Afecta a
            TODAS las cuentas que la usan
            {isActive
              ? " — el CPM deja de rotarlas y verificarlas hasta reactivarla."
              : " — el CPM retoma la gestión de todas ellas."}
          </p>
          <div className="flex gap-2">
            <Button size="sm" onClick={() => setDialog({})}>
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
        path={path}
        changes={dialog ?? {}}
        title={`${verb} plataforma`}
        onDone={() => {
          setDialog(null);
          onClose();
          qc.invalidateQueries({ queryKey: ["inv", tenant, "platforms"] });
        }}
      />
    </>
  );
}
