import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Loader2, Monitor, Shield, Terminal, User, Users } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { dash, fmtDate, fmtDateTime } from "@/lib/format";
import {
  DB_AUTH_LABEL,
  KIND_LABEL,
  OPERATOR_LABEL,
  POLICY_STATUS,
  PRINCIPAL_LABEL,
  daysLabel,
  hoursLabel,
  locationLabel,
  type SiaPolicyDetail as Detail,
  type SiaPolicyResponse,
  type SiaPolicyRule,
  type SiaPrincipal,
  type SiaTargetGroup,
} from "@/lib/sia";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3 rounded-lg border border-border bg-card p-5">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h2>
      <div className="flex flex-col gap-3 text-sm">{children}</div>
    </section>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="break-words font-medium text-navy-900">{children}</span>
    </div>
  );
}

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5 border-border pr-6 md:border-r md:last:border-r-0">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="truncate text-sm font-medium text-navy-900">{children}</span>
    </div>
  );
}

function Chips({ values, empty = "—" }: { values: string[]; empty?: string }) {
  if (values.length === 0) return <span className="text-muted-foreground">{empty}</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {values.map((v) => (
        <Badge key={v} variant="neutral">
          {v}
        </Badge>
      ))}
    </span>
  );
}

function PrincipalIcon({ type }: { type: string }) {
  const cls = "size-4 shrink-0 text-muted-foreground";
  if (type === "group") return <Users className={cls} aria-hidden="true" />;
  if (type === "role") return <Shield className={cls} aria-hidden="true" />;
  return <User className={cls} aria-hidden="true" />;
}

function Identities({ principals }: { principals: SiaPrincipal[] }) {
  if (principals.length === 0)
    return <p className="text-muted-foreground">La política no tiene identidades asignadas.</p>;
  return (
    <ul className="flex flex-col divide-y divide-border">
      {principals.map((p, i) => (
        <li key={`${p.type}-${p.name}-${i}`} className="flex items-center gap-2 py-1.5">
          <PrincipalIcon type={p.type} />
          <span className="font-medium text-navy-900">{p.name}</span>
          <span className="text-xs text-muted-foreground">
            {PRINCIPAL_LABEL[p.type] ?? p.type}
            {p.source ? ` · ${p.source}` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}

function TargetGroup({ g }: { g: SiaTargetGroup }) {
  if (g.type === "fqdn_ip") {
    const empty = g.fqdnRules.length === 0 && g.ipRules.length === 0;
    return (
      <div className="flex flex-col gap-2">
        {empty && <p className="text-muted-foreground">Sin reglas: aplica a cualquier IP o FQDN.</p>}
        {g.fqdnRules.length > 0 && (
          <table className="w-full text-left text-xs">
            <caption className="mb-1 text-left text-xs font-semibold text-navy-900">Reglas de FQDN</caption>
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-1 font-medium">Condición</th>
                <th className="py-1 font-medium">Nombre de equipo</th>
                <th className="py-1 font-medium">Dominio</th>
              </tr>
            </thead>
            <tbody>
              {g.fqdnRules.map((r, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="py-1">{r.operator ? (OPERATOR_LABEL[r.operator] ?? r.operator) : "—"}</td>
                  <td className="py-1 font-mono">{dash(r.pattern)}</td>
                  <td className="py-1 font-mono">{dash(r.domain)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {g.ipRules.length > 0 && (
          <table className="w-full text-left text-xs">
            <caption className="mb-1 text-left text-xs font-semibold text-navy-900">Reglas de IP</caption>
            <thead className="text-muted-foreground">
              <tr>
                <th className="py-1 font-medium">Condición</th>
                <th className="py-1 font-medium">Direcciones IP</th>
                <th className="py-1 font-medium">Red (nombre lógico)</th>
              </tr>
            </thead>
            <tbody>
              {g.ipRules.map((r, i) => (
                <tr key={i} className="border-t border-border">
                  <td className="py-1">{r.operator ? (OPERATOR_LABEL[r.operator] ?? r.operator) : "—"}</td>
                  <td className="py-1 font-mono">{r.ips.length ? r.ips.join(", ") : "Cualquiera"}</td>
                  <td className="py-1">{dash(r.logicalName)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    );
  }
  if (g.type === "cloud")
    return (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {g.fields.map((f) => (
          <Field key={f.label} label={f.label}>
            <Chips values={f.values} empty="Todas" />
          </Field>
        ))}
        <Field label="Tags">
          <Chips values={g.tags.map((t) => `${t.key}=${t.values.join("|") || "*"}`)} empty="Cualquiera" />
        </Field>
      </div>
    );
  if (g.type === "db")
    return (
      <ul className="flex flex-col gap-2">
        {g.instances.map((inst, i) => (
          <li key={`${inst.name}-${i}`} className="rounded-md border border-border p-3">
            <p className="font-medium text-navy-900">
              {inst.name} <span className="text-xs text-muted-foreground">{dash(inst.type)}</span>
            </p>
            <p className="text-xs text-muted-foreground">
              Autenticación: {inst.authMethod ? (DB_AUTH_LABEL[inst.authMethod] ?? inst.authMethod) : "—"}
            </p>
            {inst.permissions.length > 0 && (
              <ul className="mt-1 text-xs">
                {inst.permissions.map((l) => (
                  <li key={l}>{l}</li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    );
  return (
    <ul className="text-xs">
      {g.lines.map((l) => (
        <li key={l}>{l}</li>
      ))}
    </ul>
  );
}

function VmPermissions({ d }: { d: Detail }) {
  const perm = d.permissions;
  if (!perm || (!perm.ssh && !perm.rdp))
    return <p className="text-muted-foreground">La política no define cómo conectarse.</p>;
  return (
    <div className="flex flex-col gap-3">
      {perm.rdp && (
        <div className="flex gap-2">
          <Monitor className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          <div className="flex flex-col gap-1">
            <span className="font-medium text-navy-900">
              RDP con usuario efímero {perm.rdp.mode === "domain" ? "de dominio" : "local"}
            </span>
            <Field label="Grupos locales asignados">
              <Chips values={perm.rdp.groups} />
            </Field>
            {perm.rdp.mode === "domain" && (
              <Field label="Grupos de dominio asignados">
                <Chips values={perm.rdp.domainGroups} />
              </Field>
            )}
            <span className="text-xs text-muted-foreground">
              Reconexión del usuario efímero: {perm.rdp.reconnect ? "permitida" : "no permitida"}
            </span>
          </div>
        </div>
      )}
      {perm.ssh && (
        <div className="flex gap-2">
          <Terminal className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          <div className="flex flex-col gap-1">
            <span className="font-medium text-navy-900">SSH con certificado de corta duración</span>
            <Field label="Usuario en el target">{dash(perm.ssh.username)}</Field>
          </div>
        </div>
      )}
    </div>
  );
}

/** Una frase con lo esencial de la política (no existe en el portal). */
function summary(d: Detail): string {
  const who = `${d.principals.length} ${d.principals.length === 1 ? "identidad" : "identidades"}`;
  const how: string[] = [];
  if (d.permissions?.rdp) how.push("RDP");
  if (d.permissions?.ssh) how.push("SSH");
  const via = how.length ? ` por ${how.join(" y ")}` : "";
  const loc = d.locations[0];
  const where =
    d.kind === "db"
      ? "bases de datos"
      : !loc
        ? "máquinas"
        : loc === "FQDN/IP"
          ? "máquinas por IP o FQDN"
          : `máquinas de ${locationLabel(loc)}`;
  const c = d.conditions;
  const when = c ? `, ${daysLabel(c.days).toLowerCase()} ${hoursLabel(c)}` : "";
  const max = c?.maxSessionHours ? `, en sesiones de hasta ${c.maxSessionHours} h` : "";
  return `Permite a ${who} conectarse${via} a ${where}${when}${max}.`;
}

function LegacyRule({ rule }: { rule: SiaPolicyRule }) {
  return (
    <div className="rounded-md border border-border p-3">
      <p className="mb-2 font-semibold text-navy-900">{rule.name || "Regla"}</p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Identidades">
          <Chips values={rule.principals.map((p) => `${PRINCIPAL_LABEL[p.type] ?? p.type}: ${p.name}`)} />
        </Field>
        <Field label="Horario">
          {daysLabel(rule.days)} {hoursLabel(rule)}
          {rule.timeZone ? ` (${rule.timeZone})` : ""}
        </Field>
        <Field label="Duración máxima">
          {rule.maxSessionHours != null ? `${rule.maxSessionHours} h` : "—"}
        </Field>
        <Field label="Conexión">
          {rule.connectAs.length ? rule.connectAs.map((l) => <span key={l} className="block">{l}</span>) : "—"}
        </Field>
      </div>
    </div>
  );
}

export default function SiaPolicyDetail() {
  const { policyId = "" } = useParams<{ policyId: string }>();
  const [params] = useSearchParams();
  const tenant = params.get("tenant") ?? "";
  const source = params.get("source") ?? "uap";
  const kind = params.get("kind") ?? "vm";

  const q = useQuery({
    queryKey: ["sia-policy", tenant, source, kind, policyId],
    queryFn: () =>
      api<SiaPolicyResponse>(
        `/tenants/${tenant}/sia/policies/${encodeURIComponent(policyId)}?source=${source}&kind=${kind}`,
      ),
    enabled: !!tenant && !!policyId,
  });
  const d = q.data?.policy;
  const st = d ? POLICY_STATUS[d.status] : null;

  return (
    <div className="flex flex-col gap-4">
      <Link
        to={`/sia/policies?tenant=${tenant}`}
        className="inline-flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        Volver a Políticas
      </Link>

      {q.isLoading && (
        <div className="flex justify-center py-10">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      )}
      {q.isError && <Alert variant="destructive">{(q.error as Error).message}</Alert>}

      {d && st && (
        <>
          <div className="flex flex-col gap-4 rounded-lg border border-border bg-card p-6">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-[22px] font-bold text-navy-900">{d.name}</h1>
              <Badge variant={st.variant}>{st.label}</Badge>
            </div>
            {d.statusDetail && <p className="text-sm text-warning-ink">{d.statusDetail}</p>}
            <div className="grid grid-cols-2 gap-4 md:flex md:flex-wrap">
              <Meta label="ID de política">
                <span title={d.id} className="font-mono text-xs">
                  {d.id}
                </span>
              </Meta>
              <Meta label="Categoría">{KIND_LABEL[d.kind]}</Meta>
              <Meta label="Ubicación">{dash(d.locations.map(locationLabel).join(", "))}</Meta>
              <Meta label="Creada por">
                {dash(d.createdBy)} · {fmtDate(d.createdAt)}
              </Meta>
              <Meta label="Actualizada por">
                {dash(d.modifiedBy)} · {fmtDate(d.modifiedAt)}
              </Meta>
              <Meta label="Origen">{d.source === "uap" ? "Políticas unificadas (UAP)" : "API legado de SIA"}</Meta>
            </div>
            {d.source === "uap" && (
              <p className="rounded-md bg-sky-50 px-3 py-2 text-sm text-navy-900">{summary(d)}</p>
            )}
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Section title="Datos generales">
              {d.description && <Field label="Descripción">{d.description}</Field>}
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="Zona horaria">{dash(d.timeZone)}</Field>
                <Field label="Vigencia">
                  {d.validFrom || d.validTo
                    ? `${fmtDateTime(d.validFrom)} → ${d.validTo ? fmtDateTime(d.validTo) : "sin vencimiento"}`
                    : "Sin vencimiento"}
                </Field>
                {d.policyType && (
                  <Field label="Tipo">{d.policyType === "OnDemand" ? "A demanda" : "Recurrente"}</Field>
                )}
                <Field label="Tags">
                  <Chips values={d.tags} />
                </Field>
              </div>
            </Section>

            {d.source === "uap" ? (
              <>
                <Section title="Identidades">
                  <Identities principals={d.principals} />
                </Section>
                <Section title="Targets">
                  {d.targetGroups.length === 0 ? (
                    <p className="text-muted-foreground">Sin targets definidos.</p>
                  ) : (
                    d.targetGroups.map((g) => (
                      <div key={g.location} className="flex flex-col gap-2">
                        <span className="text-xs font-semibold text-navy-900">
                          {locationLabel(g.location)}
                        </span>
                        <TargetGroup g={g} />
                      </div>
                    ))
                  )}
                </Section>
                <Section title="Permisos">
                  {d.kind === "vm" ? (
                    <VmPermissions d={d} />
                  ) : (
                    <p className="text-muted-foreground">
                      En bases de datos, los permisos van por instancia (ver Targets).
                    </p>
                  )}
                </Section>
                <Section title="Condiciones">
                  {d.conditions ? (
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <Field label="Días">{daysLabel(d.conditions.days)}</Field>
                      <Field label="Horario">{hoursLabel(d.conditions)}</Field>
                      <Field label="Duración máxima de la sesión">
                        {d.conditions.maxSessionHours != null ? `${d.conditions.maxSessionHours} h` : "—"}
                      </Field>
                      <Field label="Cierre por inactividad">
                        {d.conditions.idleMinutes != null ? `${d.conditions.idleMinutes} min` : "—"}
                      </Field>
                    </div>
                  ) : (
                    <p className="text-muted-foreground">Sin condiciones.</p>
                  )}
                </Section>
              </>
            ) : (
              <Section title={`Reglas (${d.rules.length})`}>
                {d.rules.map((r, i) => (
                  <LegacyRule key={`${r.name}-${i}`} rule={r} />
                ))}
                {d.targets.length > 0 && (
                  <Field label="Targets">
                    {d.targets.map((t) => (
                      <span key={t} className="block">
                        {t}
                      </span>
                    ))}
                  </Field>
                )}
              </Section>
            )}
          </div>

          <details className="rounded-lg border border-border bg-card p-4 text-xs">
            <summary className="cursor-pointer text-muted-foreground">Respuesta original de CyberArk</summary>
            <pre className="mt-2 max-h-[32rem] overflow-auto rounded-md bg-muted p-2">
              {JSON.stringify(d.raw, null, 2)}
            </pre>
          </details>
        </>
      )}
    </div>
  );
}
