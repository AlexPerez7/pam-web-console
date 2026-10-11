import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Loader2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, apiBlob, apiForm } from "@/lib/api";
import type { TenantSummary } from "@/lib/types";
import { toast } from "@/components/ui/toast";

/** Logo del cliente para el header del reporte ITR, junto al logo del equipo
 *  (`app.itr.report_builder`, `client_logo_path`) — ITR ya lo tenía por
 *  cliente vía `config.json`; acá se sube una vez por tenant y queda
 *  guardado en la DB (no hay archivo local por operador). PNG/JPEG, 2 MB. */
function LogoSettings({ tenant }: { tenant: TenantSummary }) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [objectUrl, setObjectUrl] = useState<string | null>(null);

  const preview = useQuery({
    queryKey: ["tenant-logo", tenant.id, tenant.has_logo],
    queryFn: () => apiBlob(`/tenants/${tenant.id}/logo`),
    enabled: tenant.has_logo,
  });

  useEffect(() => {
    if (!preview.data) return;
    const url = URL.createObjectURL(preview.data.blob);
    setObjectUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [preview.data]);

  function invalidate() {
    qc.invalidateQueries({ queryKey: ["tenants"] });
    qc.invalidateQueries({ queryKey: ["tenant-logo", tenant.id] });
  }

  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return apiForm(`/tenants/${tenant.id}/logo`, form);
    },
    onSuccess: () => {
      invalidate();
      toast({ title: "Logo actualizado", description: tenant.name });
    },
  });

  const remove = useMutation({
    mutationFn: () => api(`/tenants/${tenant.id}/logo`, { method: "DELETE" }),
    onSuccess: () => {
      invalidate();
      toast({ title: "Logo quitado", description: tenant.name });
    },
  });

  return (
    <div className="mb-4 flex flex-col gap-2 border-b border-border pb-4">
      <Label>Logo del cliente (reporte ITR)</Label>
      <p className="text-xs text-muted-foreground">
        Aparece junto al logo del equipo en el header del reporte ITR. PNG o JPEG, hasta 2 MB.
      </p>
      <div className="flex items-center gap-3">
        {tenant.has_logo && objectUrl && (
          <img
            src={objectUrl}
            alt={`Logo de ${tenant.client}`}
            className="h-10 max-w-[10rem] rounded border border-border bg-white object-contain p-1"
          />
        )}
        <input
          ref={fileRef}
          type="file"
          accept="image/png,image/jpeg"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) upload.mutate(file);
            e.target.value = "";
          }}
        />
        <Button
          size="sm"
          variant="outline"
          disabled={upload.isPending || remove.isPending}
          onClick={() => fileRef.current?.click()}
        >
          {upload.isPending && <Loader2 className="size-3.5 animate-spin" />}
          {tenant.has_logo ? "Reemplazar" : "Subir logo"}
        </Button>
        {tenant.has_logo && (
          <Button
            size="sm"
            variant="ghost"
            disabled={upload.isPending || remove.isPending}
            onClick={() => remove.mutate()}
          >
            <X className="size-3.5" /> Quitar
          </Button>
        )}
      </div>
      {upload.isError && <Alert variant="destructive">{(upload.error as Error).message}</Alert>}
      {remove.isError && <Alert variant="destructive">{(remove.error as Error).message}</Alert>}
    </div>
  );
}

function ComplianceSettings({ tenant }: { tenant: TenantSummary }) {
  const qc = useQueryClient();
  const [days, setDays] = useState(String(tenant.compliance_stale_days));

  const put = useMutation({
    mutationFn: (value: number) =>
      api(`/tenants/${tenant.id}/compliance-settings`, {
        method: "PUT",
        body: { compliance_stale_days: value },
      }),
    onSuccess: (_, value) => {
      qc.invalidateQueries({ queryKey: ["tenants"] });
      toast({ title: "Umbral de Compliance guardado", description: `${tenant.name}: ${value} días` });
    },
  });

  const parsed = Number(days);
  const valid = Number.isInteger(parsed) && parsed >= 1 && parsed <= 3650;
  const dirty = valid && parsed !== tenant.compliance_stale_days;

  return (
    <div className="mb-4 flex flex-col gap-2 border-b border-border pb-4">
      <Label htmlFor={`stale-${tenant.id}`}>Compliance: días sin rotar (gestión deshabilitada)</Label>
      <p className="text-xs text-muted-foreground">
        Una cuenta con la gestión automática deshabilitada y sin rotar hace más de esto cuenta
        como "no compliant" en el Dashboard — CyberArk no la marca sola (sin gestión activa, no
        hay política que incumplir).
      </p>
      <div className="flex items-center gap-2">
        <Input
          id={`stale-${tenant.id}`}
          type="number"
          min={1}
          max={3650}
          className="w-24"
          value={days}
          disabled={put.isPending}
          onChange={(e) => setDays(e.target.value)}
        />
        <Button
          size="sm"
          variant="outline"
          disabled={!dirty || put.isPending}
          onClick={() => put.mutate(parsed)}
        >
          Guardar
        </Button>
        {put.isSuccess && !dirty && (
          <span className="text-xs text-muted-foreground">Guardado</span>
        )}
      </div>
      {!valid && <p className="text-xs text-destructive">Tiene que ser un número entre 1 y 3650.</p>}
      {put.isError && <Alert variant="destructive">{(put.error as Error).message}</Alert>}
    </div>
  );
}

function TenantCard({ tenant }: { tenant: TenantSummary }) {
  const [open, setOpen] = useState(false);

  return (
    <Card>
      <CardHeader className="space-y-0">
        <CardTitle className="text-base">
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            className="flex w-full items-center justify-between gap-2 text-left"
          >
            <span className="flex items-center gap-1.5">
              {open ? (
                <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
              ) : (
                <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
              )}
              {tenant.name}
            </span>
            <span className="flex items-center gap-2 text-xs font-normal text-muted-foreground">
              {tenant.id}
            </span>
          </button>
        </CardTitle>
      </CardHeader>
      {open && (
        <CardContent>
          <LogoSettings tenant={tenant} />
          <ComplianceSettings tenant={tenant} />
        </CardContent>
      )}
    </Card>
  );
}

export default function Tenants() {
  const q = useQuery({
    queryKey: ["tenants"],
    queryFn: () => api<TenantSummary[]>("/tenants"),
  });

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-[22px] font-bold text-navy-900">Tenants</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          Registro de tenants. El acceso a cada operación de escritura lo define el rol RBAC del
          operador sobre el tenant (viewer / operator / approver / tenant_admin / platform_admin —
          ver <code>Operadores</code>). Haga clic en el nombre para ver/editar su logo y el umbral
          de Compliance.
        </p>
      </div>

      {q.isLoading && (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Cargando…
        </div>
      )}
      {q.isError && <Alert variant="destructive">{(q.error as Error).message}</Alert>}

      <div className="grid items-start gap-3 md:grid-cols-2">
        {(q.data ?? []).map((t) => (
          <TenantCard key={t.id} tenant={t} />
        ))}
      </div>
    </div>
  );
}
