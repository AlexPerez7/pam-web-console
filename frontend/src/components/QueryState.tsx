import { Loader2 } from "lucide-react";
import { Alert } from "@/components/ui/alert";

/** Estado de carga/error de una query, con el nombre del tenant en el
 *  mensaje — común a toda página con selector de tenant. */
export function QueryState({
  isLoading,
  error,
  tenant,
}: {
  isLoading: boolean;
  error: unknown;
  tenant: string;
}) {
  if (isLoading)
    return (
      <div className="flex items-center gap-2 text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        <span>Cargando datos de {tenant}…</span>
      </div>
    );
  if (error) return <Alert variant="destructive">{(error as Error).message}</Alert>;
  return null;
}
