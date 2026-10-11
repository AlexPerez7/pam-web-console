import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Loader2, RotateCcw } from "lucide-react";
import { useState } from "react";
import { DryRunDialog } from "@/components/DryRunDialog";
import { RevealPasswordDialog } from "@/components/RevealPasswordDialog";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api, apiBlob } from "@/lib/api";
import { saveBlob } from "@/lib/download";
import { fmtDateTime } from "@/lib/format";
import { roleAtLeast } from "@/lib/roles";
import type {
  AccountBackup,
  AccountBackupsResponse,
  PlatformBackup,
  PlatformBackupsResponse,
  TenantSummary,
} from "@/lib/types";

function BackupRow({ tenant, backup }: { tenant: string; backup: AccountBackup }) {
  const qc = useQueryClient();
  const [revealing, setRevealing] = useState(false);
  const [restoring, setRestoring] = useState<Record<string, unknown> | null>(null);

  return (
    <tr className="border-t border-border">
      <td className="px-3 py-2">{backup.account_name}</td>
      <td className="px-3 py-2">{backup.safe_name}</td>
      <td className="px-3 py-2">{backup.user_name}</td>
      <td className="px-3 py-2">{backup.deleted_by}</td>
      <td className="px-3 py-2">{fmtDateTime(backup.deleted_at)}</td>
      <td className="px-3 py-2">
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => setRevealing(true)}>
            Revelar
          </Button>
          <Button variant="outline" size="sm" onClick={() => setRestoring({})}>
            <RotateCcw className="size-3.5" />
            Restaurar
          </Button>
        </div>
      </td>

      <RevealPasswordDialog
        open={revealing}
        onClose={() => setRevealing(false)}
        path={`/tenants/${tenant}/account-backups/${backup.id}/reveal`}
        title={`Revelar contraseña — ${backup.account_name}`}
      />

      <DryRunDialog
        open={restoring !== null}
        onClose={() => setRestoring(null)}
        path={`/tenants/${tenant}/account-backups/${backup.id}/restore`}
        changes={restoring ?? {}}
        title={`Restaurar ${backup.account_name}`}
        onDone={() => {
          setRestoring(null);
          qc.invalidateQueries({ queryKey: ["inv", tenant, "accounts"] });
        }}
      />
    </tr>
  );
}

function PlatformBackupRow({ tenant, backup }: { tenant: string; backup: PlatformBackup }) {
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function download() {
    setDownloading(true);
    setError(null);
    try {
      const { blob, filename } = await apiBlob(
        `/tenants/${tenant}/platform-backups/${backup.id}/download`,
      );
      saveBlob(blob, filename);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDownloading(false);
    }
  }

  return (
    <tr className="border-t border-border">
      <td className="px-3 py-2">{backup.platform_id}</td>
      <td className="px-3 py-2">{(backup.size_bytes / 1024).toFixed(1)} KB</td>
      <td className="px-3 py-2">{backup.deleted_by}</td>
      <td className="px-3 py-2">{fmtDateTime(backup.deleted_at)}</td>
      <td className="px-3 py-2">
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={download} disabled={downloading}>
            {downloading ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Download className="size-3.5" />
            )}
            Descargar
          </Button>
          {error && <span className="text-xs text-destructive">{error}</span>}
        </div>
      </td>
    </tr>
  );
}

function PlatformBackupsSection({ tenant }: { tenant: string }) {
  const backups = useQuery({
    queryKey: ["platform-backups", tenant],
    queryFn: () => api<PlatformBackupsResponse>(`/tenants/${tenant}/platform-backups`),
    enabled: !!tenant,
  });

  if (!tenant) return null;

  return (
    <div className="flex flex-col gap-2">
      <h2 className="text-base font-semibold text-navy-900">Backups de plataformas</h2>
      <p className="text-sm text-muted-foreground">
        Paquete de "Export Platform" guardado antes de borrar una plataforma (
        <code>platform.delete</code>). Sin secretos adentro — es política/config del CPM, no
        hace falta revelar nada, sólo descargar el `.zip`.
      </p>

      {backups.isLoading && (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Cargando…
        </div>
      )}
      {backups.isError && <Alert variant="destructive">{(backups.error as Error).message}</Alert>}

      {backups.data &&
        (backups.data.backups.length === 0 ? (
          <p className="text-sm text-muted-foreground">Sin backups de plataformas en este tenant.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted-foreground">
                  <th className="px-3 py-2 font-medium">Plataforma</th>
                  <th className="px-3 py-2 font-medium">Tamaño</th>
                  <th className="px-3 py-2 font-medium">Borrado por</th>
                  <th className="px-3 py-2 font-medium">Cuándo</th>
                  <th className="px-3 py-2 font-medium">Acciones</th>
                </tr>
              </thead>
              <tbody>
                {backups.data.backups.map((b) => (
                  <PlatformBackupRow key={b.id} tenant={tenant} backup={b} />
                ))}
              </tbody>
            </table>
          </div>
        ))}
    </div>
  );
}

export default function AccountBackups() {
  const [tenant, setTenant] = useState("");

  const tenants = useQuery({
    queryKey: ["tenants"],
    queryFn: () => api<TenantSummary[]>("/tenants"),
  });
  const allowedTenants = (tenants.data ?? []).filter((t) => roleAtLeast(t.my_role, "tenant_admin"));

  const backups = useQuery({
    queryKey: ["account-backups", tenant],
    queryFn: () => api<AccountBackupsResponse>(`/tenants/${tenant}/account-backups`),
    enabled: !!tenant,
  });

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-[22px] font-bold text-navy-900">Backups de cuentas</h1>
        <p className="mt-0.5 text-sm text-muted-foreground">
          Copias de seguridad guardadas antes de borrar una cuenta (<code>account.delete</code>).
          Permiten revelar la contraseña vigente al momento del borrado o restaurar la cuenta.
        </p>
      </div>

      <label className="text-xs text-muted-foreground">
        Tenant
        <Select value={tenant} onValueChange={setTenant}>
          <SelectTrigger className="mt-1 w-64">
            <SelectValue placeholder="Elija un tenant" />
          </SelectTrigger>
          <SelectContent>
            {allowedTenants.map((t) => (
              <SelectItem key={t.id} value={t.id}>
                {t.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </label>

      {backups.isLoading && (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Cargando…
        </div>
      )}
      {backups.isError && <Alert variant="destructive">{(backups.error as Error).message}</Alert>}

      {tenant && backups.data && (
        backups.data.backups.length === 0 ? (
          <p className="text-sm text-muted-foreground">Sin backups en este tenant.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted-foreground">
                  <th className="px-3 py-2 font-medium">Cuenta</th>
                  <th className="px-3 py-2 font-medium">Safe</th>
                  <th className="px-3 py-2 font-medium">Usuario</th>
                  <th className="px-3 py-2 font-medium">Borrado por</th>
                  <th className="px-3 py-2 font-medium">Cuándo</th>
                  <th className="px-3 py-2 font-medium">Acciones</th>
                </tr>
              </thead>
              <tbody>
                {backups.data.backups.map((b) => (
                  <BackupRow key={b.id} tenant={tenant} backup={b} />
                ))}
              </tbody>
            </table>
          </div>
        )
      )}

      {tenant && <PlatformBackupsSection tenant={tenant} />}
    </div>
  );
}
