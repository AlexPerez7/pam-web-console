import { useQuery } from "@tanstack/react-query";
import { Copy, Loader2, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { api, apiBlob } from "@/lib/api";
import { saveBlob } from "@/lib/download";
import { roleAtLeast } from "@/lib/roles";
import type { TenantSummary } from "@/lib/types";
import { toast } from "@/components/ui/toast";

const MIN_REASON = 8;

/** Backup de TODAS las cuentas de un tenant (usuario/address/plataforma/
 *  safe/remoteMachines/contraseña), a pedido de un cliente: tener todo lo
 *  necesario para recrear cuentas si hace falta borrarlas. Entrega un ZIP
 *  protegido con una contraseña generada al azar por export — pensado para
 *  mandarle el archivo al cliente y la clave por un canal separado. La
 *  clave viaja en un header de la respuesta y sólo se muestra acá, una vez;
 *  PAM Web Console nunca la guarda. Sólo visible si el operador es `tenant_admin+` —
 *  mismo techo que "Ver contraseña", pero acá el radio de exposición es
 *  todo el tenant de una vez. Puede tardar varios minutos (una llamada
 *  Retrieve por cuenta). Ver `docs/specs/account-credentials-backup.md`. */
export function CredentialsBackupSection({ tenant }: { tenant: string }) {
  const [reason, setReason] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [password, setPassword] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
    enabled: !!tenant,
  });

  const allowed = roleAtLeast(t.data?.my_role, "tenant_admin");
  if (!tenant || !allowed) return null;

  async function run() {
    setDownloading(true);
    setError(null);
    setPassword(null);
    setCopied(false);
    try {
      const { blob, filename, headers } = await apiBlob(
        `/tenants/${tenant}/credentials-backup/export`,
        { method: "POST", body: { reason }, headers: ["x-backup-password"] },
      );
      saveBlob(blob, filename);
      setPassword(headers["x-backup-password"] ?? null);
      setReason("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDownloading(false);
    }
  }

  async function copy() {
    if (!password) return;
    try {
      await navigator.clipboard.writeText(password);
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
    <div className="flex flex-col gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3">
      <div className="flex items-start gap-2">
        <ShieldAlert className="mt-0.5 size-4 shrink-0 text-destructive" />
        <div>
          <p className="text-sm font-semibold">Backup de credenciales (todas las cuentas)</p>
          <p className="text-xs text-muted-foreground">
            Descarga un ZIP protegido con contraseña (usuario, address, plataforma, safe, remote
            machines y contraseña de cada cuenta de este tenant) — pensado para entregarle al
            cliente. Puede tardar varios minutos y genera un evento "Retrieve" por cuenta en el
            audit nativo de CyberArk. La contraseña del zip se genera al azar en cada descarga y
            se muestra una sola vez abajo — comunicásela al cliente por un canal distinto del
            archivo.
          </p>
        </div>
      </div>

      <label className="text-xs font-medium text-muted-foreground">
        Razón (mínimo {MIN_REASON} caracteres)
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={2}
          className="mt-1 w-full rounded-md border border-input bg-card px-2 py-1.5 text-sm"
          placeholder="ej. backup previo a limpieza de cuentas, ticket OPS-1234"
        />
      </label>

      {error && <Alert variant="destructive">{error}</Alert>}

      {password && (
        <div className="flex flex-col gap-1">
          <Alert variant="success">
            Descargado. Copie la contraseña ahora — no se puede volver a ver.
          </Alert>
          <label className="text-xs font-medium text-muted-foreground">
            Contraseña del zip
            <div className="mt-1 flex gap-2">
              <input
                readOnly
                value={password}
                className="h-8 flex-1 rounded-md border border-input bg-muted px-2 font-mono text-sm"
              />
              <Button variant="outline" size="sm" onClick={copy}>
                <Copy className="size-3.5" />
                {copied ? "Copiada" : "Copiar"}
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setPassword(null)}>
                Ocultar
              </Button>
            </div>
          </label>
        </div>
      )}

      <div>
        <Button
          variant="destructive"
          size="sm"
          onClick={run}
          disabled={reason.trim().length < MIN_REASON || downloading}
        >
          {downloading && <Loader2 className="size-3.5 animate-spin" />}
          Descargar backup cifrado
        </Button>
      </div>
    </div>
  );
}
