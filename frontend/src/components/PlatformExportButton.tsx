import { Download, Loader2 } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { apiBlob } from "@/lib/api";
import { saveBlob } from "@/lib/download";

/** "Descargar configuración" en el detalle de una plataforma — el mismo
 *  paquete de "Export Platform" que `platform.delete` guarda como backup,
 *  pero disponible en cualquier momento, no sólo antes de borrar.
 *  Read-only, `viewer+` (mismo criterio que `ExportCsvButton`, sin
 *  feature-flag). Ver `docs/specs/platform-lifecycle.md`. */
export function PlatformExportButton({
  tenant,
  platformId,
}: {
  tenant: string;
  platformId: string;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setLoading(true);
    setError(null);
    try {
      const { blob, filename } = await apiBlob(
        `/tenants/${tenant}/platforms/${encodeURIComponent(platformId)}/export`,
      );
      saveBlob(blob, filename);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      <Button variant="outline" size="sm" onClick={run} disabled={loading}>
        {loading ? <Loader2 className="size-3.5 animate-spin" /> : <Download className="size-3.5" />}
        {loading ? "Descargando…" : "Descargar configuración"}
      </Button>
      {error && <span className="text-xs text-destructive">{error}</span>}
    </div>
  );
}
