import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { useTenantParam } from "@/lib/useTenantParam";
import type { TenantSummary } from "@/lib/types";

/** Encabezado (título + selector de tenant) + estados vacíos, común a toda
 *  página con selector de tenant. `children` sólo se renderiza una vez hay
 *  un tenant elegido. */
export function TenantScope({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: (tenant: string) => ReactNode;
}) {
  const [tenant, setTenant] = useTenantParam();
  const tenants = useQuery({
    queryKey: ["tenants"],
    queryFn: () => api<TenantSummary[]>("/tenants"),
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h1 className="text-[22px] font-bold text-navy-900">{title}</h1>
        <div className="flex items-center gap-2">
          {action}
          <Select value={tenant ?? undefined} onValueChange={setTenant}>
            <SelectTrigger aria-label="Tenant" className="w-60">
              <SelectValue placeholder="Elija un tenant" />
            </SelectTrigger>
            <SelectContent>
              {(tenants.data ?? []).map((t) => (
                <SelectItem key={t.id} value={t.id}>
                  {t.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {tenants.data?.length === 0 && (
        <p className="text-muted-foreground">
          No tiene tenants asignados. Pida acceso a un <code>platform_admin</code>.
        </p>
      )}
      {!tenant && (tenants.data?.length ?? 0) > 0 && (
        <p className="text-muted-foreground">Seleccione un tenant.</p>
      )}

      {tenant && children(tenant)}
    </div>
  );
}
