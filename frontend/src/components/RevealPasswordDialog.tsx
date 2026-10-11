import { useMutation } from "@tanstack/react-query";
import { Copy, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { api } from "@/lib/api";
import type { RevealResponse } from "@/lib/types";
import { toast } from "@/components/ui/toast";

const MIN_REASON = 8;

interface Props {
  open: boolean;
  onClose: () => void;
  /** endpoint POST que devuelve {"password": "..."} */
  path: string;
  title: string;
  /** campos extra del body además de `reason` — ej. `{version: 3}` para
   *  revelar una versión histórica puntual (`account.reveal.history`) */
  extraBody?: Record<string, unknown>;
}

/** Diálogo genérico "Revelar" — razón obligatoria -> contraseña de sólo
 *  lectura + copiar al portapapeles. Nunca queda en el DOM tras cerrar (el
 *  estado se resetea al abrir). Lo usan el reveal de backups
 *  (`AccountBackups.tsx`), el de cuentas activas (`AccountRevealSection`) y
 *  el de versiones históricas (pestaña "Versiones" de
 *  `AccountDetailPanel.tsx`). Ver `docs/specs/account-safe-delete.md`,
 *  `docs/specs/account-reveal.md` y
 *  `docs/specs/done/account-password-versions.md`. */
export function RevealPasswordDialog({ open, onClose, path, title, extraBody }: Props) {
  const [reason, setReason] = useState("");
  const [copied, setCopied] = useState(false);

  const reveal = useMutation({
    mutationFn: () =>
      api<RevealResponse>(path, { method: "POST", body: { reason, ...extraBody } }),
  });

  useEffect(() => {
    if (open) {
      setReason("");
      setCopied(false);
      reveal.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  async function copy() {
    if (!reveal.data) return;
    try {
      await navigator.clipboard.writeText(reveal.data.password);
      setCopied(true);
    } catch {
      // clipboard no disponible (ej. contexto sin HTTPS): avisar, no fallar en silencio
      toast({
        title: "No se pudo copiar al portapapeles",
        description: "Seleccione el valor y cópielo a mano.",
        variant: "warning",
      });
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogTitle>{title}</DialogTitle>

        {!reveal.data ? (
          <>
            <p className="text-sm text-muted-foreground">
              Queda auditado, con la razón y el operador.
            </p>
            <label className="text-xs font-medium text-muted-foreground">
              Razón (mínimo {MIN_REASON} caracteres)
              <textarea
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                rows={2}
                className="mt-1 w-full rounded-md border border-input bg-card px-2 py-1.5 text-sm"
                placeholder="ej. verificar credencial con el cliente"
              />
            </label>
            {reveal.isError && (
              <Alert variant="destructive">{(reveal.error as Error).message}</Alert>
            )}
          </>
        ) : (
          <label className="text-xs font-medium text-muted-foreground">
            Contraseña
            <div className="mt-1 flex gap-2">
              <input
                readOnly
                value={reveal.data.password}
                className="h-8 flex-1 rounded-md border border-input bg-muted px-2 font-mono text-sm"
              />
              <Button variant="outline" size="sm" onClick={copy}>
                <Copy className="size-3.5" />
                {copied ? "Copiado" : "Copiar"}
              </Button>
            </div>
          </label>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cerrar
          </Button>
          {!reveal.data && (
            <Button
              onClick={() => reveal.mutate()}
              disabled={reason.trim().length < MIN_REASON || reveal.isPending}
            >
              {reveal.isPending && <Loader2 className="size-4 animate-spin" />}
              Revelar
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
