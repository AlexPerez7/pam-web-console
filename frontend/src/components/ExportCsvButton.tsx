import { Download, Loader2 } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { apiBlob } from "@/lib/api";
import { saveBlob } from "@/lib/download";
import { toast, toastError } from "@/components/ui/toast";

type Resource = "accounts" | "safes" | "platforms" | "users" | "groups";

export function ExportCsvButton({
  tenant,
  resource,
  iconOnly = false,
}: {
  tenant: string;
  resource: Resource;
  /** sólo el ícono (con aria-label/tooltip), para barras compactas */
  iconOnly?: boolean;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setLoading(true);
    setError(null);
    try {
      const { blob, filename } = await apiBlob(`/tenants/${tenant}/export/${resource}`);
      saveBlob(blob, filename);
      toast({ title: "CSV descargado", description: filename });
    } catch (e) {
      setError((e as Error).message);
      toastError("No se pudo exportar el CSV", e);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      <Button
        variant="outline"
        size={iconOnly ? "icon" : "sm"}
        onClick={run}
        disabled={loading}
        aria-label={iconOnly ? "Exportar CSV" : undefined}
        title={iconOnly ? "Exportar CSV" : undefined}
        className={iconOnly ? "size-8" : undefined}
      >
        {loading ? <Loader2 className="size-3.5 animate-spin" /> : <Download className="size-3.5" />}
        {!iconOnly && (loading ? "Exportando…" : "Exportar CSV")}
      </Button>
      {error && !iconOnly && <span className="text-xs text-danger-ink">{error}</span>}
    </div>
  );
}
