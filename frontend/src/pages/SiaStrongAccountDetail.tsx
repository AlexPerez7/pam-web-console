import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, Loader2, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { dash, fmtDateTime } from "@/lib/format";
import {
  SECRET_TYPE_LABEL,
  TARGET_SET_TYPE_LABEL,
  strongAccountsQuery,
  type SiaDbStrongAccount,
  type SiaVmStrongAccount,
} from "@/lib/sia";
import type { SafeMembersResponse } from "@/lib/types";

/** Rol que el portal pide agregar como miembro del safe de la strong
 *  account ("In PAM add the DPA Privilege Cloud Secret Access role as a
 *  member of the Safe"). En Globex el miembro real se llama "DPA RDP
 *  Privilege Cloud Secrets Access" (2026-10-07): hay una variante por
 *  protocolo y en plural, así que se acepta cualquier "DPA … Privilege
 *  Cloud Secret(s) Access". */
const SIA_SAFE_ROLE = "DPA Privilege Cloud Secret Access";
const SIA_SAFE_ROLE_RE = /^dpa .*privilege cloud secrets? access$/i;

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border bg-card p-5">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h2>
      <div className="flex flex-col gap-3 text-sm">{children}</div>
    </section>
  );
}

function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="break-words font-medium text-navy-900">{children}</span>
      {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
    </div>
  );
}

function yesNo(v: boolean | null | undefined): string {
  return v == null ? "—" : v ? "Sí" : "No";
}

/** Verifica en vivo que el rol de SIA sea miembro del safe: es el paso del
 *  portal que más se olvida y sin él SIA no puede leer la cuenta. */
function SafeRoleCheck({ tenant, safe }: { tenant: string; safe: string }) {
  const q = useQuery({
    queryKey: ["safe-members", tenant, safe],
    queryFn: () =>
      api<SafeMembersResponse>(`/tenants/${tenant}/safes/${encodeURIComponent(safe)}/members`),
    retry: false,
  });
  if (q.isLoading)
    return (
      <span className="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" /> Verificando los miembros del safe…
      </span>
    );
  if (q.isError)
    return (
      <span className="text-xs text-muted-foreground">
        No se pudieron leer los miembros del safe: {(q.error as Error).message}
      </span>
    );
  const member = q.data?.members.find((m) => SIA_SAFE_ROLE_RE.test(String(m.memberName ?? "").trim()));
  if (!member)
    return (
      <Alert variant="warning" className="flex gap-2">
        <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
        <span>
          El rol <strong>{SIA_SAFE_ROLE}</strong> no es miembro del safe <strong>{safe}</strong>. Sin él, SIA no
          puede usar esta cuenta para crear los usuarios efímeros.
        </span>
      </Alert>
    );
  const perms = member.permissions ?? {};
  const missing = ["listAccounts", "retrieveAccounts"].filter((k) => perms[k] === false);
  return (
    <span className="flex items-center gap-2 text-xs text-success-ink">
      <CheckCircle2 className="size-4" aria-hidden="true" />
      El rol {String(member.memberName)} es miembro del safe
      {missing.length ? ` (le falta: ${missing.join(", ")})` : ""}.
    </span>
  );
}

function VmDetail({ tenant, a }: { tenant: string; a: SiaVmStrongAccount }) {
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Section title="Cuenta">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Tipo de cuenta">{a.accountType === "domain" ? "Cuenta de dominio" : "Cuenta local"}</Field>
          <Field label="Dominio">{a.accountType === "domain" ? a.domain : "—"}</Field>
          <Field label="Safe">{dash(a.linkedSafe)}</Field>
          <Field label="Nombre de la cuenta">{dash(a.linkedAccount)}</Field>
          <Field label="Origen">{a.type ? (SECRET_TYPE_LABEL[a.type] ?? a.type) : "—"}</Field>
          {/* En Globex la API no trae estos tres: se ocultan si vienen vacíos. */}
          {a.rotatable != null && <Field label="Rotable">{yesNo(a.rotatable)}</Field>}
          {a.createdAt && <Field label="Creada">{fmtDateTime(a.createdAt)}</Field>}
          {a.updatedAt && <Field label="Modificada">{fmtDateTime(a.updatedAt)}</Field>}
        </div>
        {a.linkedSafe && <SafeRoleCheck tenant={tenant} safe={a.linkedSafe} />}
      </Section>

      <Section title="Usuarios efímeros de dominio">
        {a.accountType === "local" ? (
          <p className="text-muted-foreground">No aplica a una cuenta local.</p>
        ) : !a.ephemeralDomainUsers ? (
          <p className="text-muted-foreground">La creación de usuarios efímeros de dominio está deshabilitada.</p>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Creación de usuarios efímeros">Habilitada</Field>
            <Field
              label="Ubicación (OU)"
              hint={a.ephemeralUserLocation ? undefined : "Por defecto: la OU más común de los grupos de la política."}
            >
              {a.ephemeralUserLocation ?? "Por defecto"}
            </Field>
            <Field label="Controlador de dominio">{dash(a.domainController)}</Field>
            <Field label="Nombre NetBIOS">{dash(a.domainControllerNetbios)}</Field>
            <Field label="LDAPS hacia el controlador">{yesNo(a.domainControllerLdaps)}</Field>
            <Field label="Valida el certificado LDAPS">{yesNo(a.domainControllerCertValidation)}</Field>
            <Field label="WinRM por HTTPS">{yesNo(a.winrmHttps)}</Field>
            <Field label="Valida el certificado de WinRM">{yesNo(a.winrmCertValidation)}</Field>
          </div>
        )}
      </Section>

      <Section title={`Target sets (${a.targetSets.length})`}>
        {a.targetSets.length === 0 ? (
          <p className="text-muted-foreground">Ningún target set usa esta strong account.</p>
        ) : (
          <table className="w-full text-left text-xs">
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-1 font-medium">Target set</th>
                <th className="py-1 font-medium">Tipo</th>
                <th className="py-1 font-medium">Formato del usuario efímero</th>
                <th className="py-1 font-medium">Descripción</th>
              </tr>
            </thead>
            <tbody>
              {a.targetSets.map((t) => (
                <tr key={t.name} className="border-t border-border">
                  <td className="py-1 font-mono">{t.name}</td>
                  <td className="py-1">{t.type ? (TARGET_SET_TYPE_LABEL[t.type] ?? t.type) : "—"}</td>
                  <td className="py-1 font-mono">{dash(t.provisionFormat)}</td>
                  <td className="py-1">{dash(t.description)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>
    </div>
  );
}

function DbDetail({ tenant, a }: { tenant: string; a: SiaDbStrongAccount }) {
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Section title="Cuenta">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Tipo">{a.type ? (SECRET_TYPE_LABEL[a.type] ?? a.type) : "—"}</Field>
          <Field label="Usuario">{dash(a.username ?? a.account)}</Field>
          <Field label="Se guarda en">{dash(a.store)}</Field>
          <Field label="Cuenta vinculada">
            {a.linkedSafe || a.linkedAccount ? `${dash(a.linkedSafe)} / ${dash(a.linkedAccount)}` : "—"}
          </Field>
          {a.createdAt && (
            <Field label="Creada">
              {fmtDateTime(a.createdAt)}
              {a.createdBy ? ` por ${a.createdBy}` : ""}
            </Field>
          )}
          {a.updatedAt && (
            <Field label="Modificada">
              {fmtDateTime(a.updatedAt)}
              {a.updatedBy ? ` por ${a.updatedBy}` : ""}
            </Field>
          )}
        </div>
        {a.description && <Field label="Descripción">{a.description}</Field>}
        {a.linkedSafe && <SafeRoleCheck tenant={tenant} safe={a.linkedSafe} />}
      </Section>
      <Section title={`Bases de datos (${a.databases.length})`}>
        {a.databases.length === 0 ? (
          <p className="text-muted-foreground">Ninguna base de datos usa esta strong account.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-border">
            {a.databases.map((d) => (
              <li key={d.id || d.name} className="py-1.5">
                <span className="font-medium text-navy-900">{d.name}</span>{" "}
                <span className="text-xs text-muted-foreground">
                  {[d.engine, d.endpoint ? `${d.endpoint}${d.port ? `:${d.port}` : ""}` : null]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}

export default function SiaStrongAccountDetail() {
  const { kind = "vm", accountId = "" } = useParams<{ kind: string; accountId: string }>();
  const [params] = useSearchParams();
  const tenant = params.get("tenant") ?? "";
  const q = useQuery({ ...strongAccountsQuery(tenant), enabled: !!tenant });
  const account =
    kind === "db" ? q.data?.db.find((a) => a.id === accountId) : q.data?.vm.find((a) => a.id === accountId);

  return (
    <div className="flex flex-col gap-4">
      <Link
        to={`/sia/strong-accounts?tenant=${tenant}&tab=${kind}`}
        className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        Volver a Strong accounts
      </Link>

      {q.isLoading && (
        <div className="flex justify-center py-10">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      )}
      {q.isError && <Alert variant="destructive">{(q.error as Error).message}</Alert>}
      {q.data && !account && <Alert variant="warning">La strong account ya no existe en este tenant.</Alert>}

      {account && (
        <>
          <div className="flex flex-col gap-2 rounded-lg border border-border bg-card p-6">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="break-all text-[22px] font-bold text-navy-900">{account.name}</h1>
              {account.active ? (
                <Badge variant="success">Habilitada</Badge>
              ) : (
                <Badge variant="neutral">Deshabilitada</Badge>
              )}
            </div>
            <p className="text-sm text-muted-foreground">
              Strong account de {kind === "db" ? "bases de datos" : "máquinas virtuales"} · sólo metadatos, PAM Web Console
              nunca muestra su contraseña.
            </p>
          </div>
          {account.kind === "vm" ? <VmDetail tenant={tenant} a={account} /> : <DbDetail tenant={tenant} a={account} />}
        </>
      )}
    </div>
  );
}
