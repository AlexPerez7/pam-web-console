import { useQuery } from "@tanstack/react-query";
import { QueryState } from "@/components/QueryState";
import { TenantScope } from "@/components/TenantScope";
import { Card } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { SiaSettingField, SiaSettingsResponse } from "@/lib/sia";

function value(f: SiaSettingField): string {
  if (typeof f.value === "boolean") return f.value ? "Sí" : "No";
  if (f.value === "") return "—";
  return String(f.value);
}

function SettingsView({ tenant }: { tenant: string }) {
  const q = useQuery({
    queryKey: ["sia-settings", tenant],
    queryFn: () => api<SiaSettingsResponse>(`/tenants/${tenant}/sia/settings`),
  });
  if (!q.data) return <QueryState isLoading={q.isLoading} error={q.error} tenant={tenant} />;
  if (q.data.groups.length === 0)
    return <p className="text-sm text-muted-foreground">CyberArk no devolvió configuración.</p>;
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {q.data.groups.map((g) => (
        <Card key={g.key} className="p-4">
          <h2 className="mb-2 text-sm font-semibold">{g.label}</h2>
          <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-xs">
            {g.fields.map((f) => (
              <div key={f.key} className="contents">
                <dt className="text-muted-foreground">{f.label}</dt>
                <dd className="text-right font-medium">{value(f)}</dd>
              </div>
            ))}
          </dl>
        </Card>
      ))}
    </div>
  );
}

export default function SiaSettings() {
  return (
    <TenantScope title="Configuración de SIA">
      {(tenant) => <SettingsView tenant={tenant} />}
    </TenantScope>
  );
}
