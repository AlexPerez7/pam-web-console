import { useState } from "react";
import { dash, fmtDateTime } from "@/lib/format";
import { kindBadge, resultBadge } from "@/lib/auditFormat";
import type { AuditEntry } from "@/lib/types";

function fmtValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "sí" : "no";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

interface DiffRow {
  field: string;
  from: unknown;
  to: unknown;
}

/** `detail.diff` ya viene como `[{field, from, to}]` (ops de edición); las
 *  ops de acción sin diff a veces sólo dejan `before`/`after` planos — se
 *  arma la misma forma de tabla a partir de la unión de sus claves. */
function diffRows(detail: Record<string, unknown>): DiffRow[] {
  if (Array.isArray(detail.diff) && detail.diff.length > 0) {
    return detail.diff as DiffRow[];
  }
  const before = detail.before as Record<string, unknown> | undefined;
  const after = detail.after as Record<string, unknown> | undefined;
  if (before && after) {
    const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
    return [...keys].map((k) => ({ field: k, from: before[k], to: after[k] }));
  }
  return [];
}

function DiffTable({ rows }: { rows: DiffRow[] }) {
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-muted-foreground">
          <th className="pb-1 pr-2 font-medium">Campo</th>
          <th className="pb-1 pr-2 font-medium">Antes</th>
          <th className="pb-1 font-medium">Después</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i} className="border-t border-border">
            <td className="py-1 pr-2 font-medium">{r.field}</td>
            <td className="py-1 pr-2 text-muted-foreground">{fmtValue(r.from)}</td>
            <td className="py-1">{fmtValue(r.to)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Panel de detalle de un registro de auditoría (R8, docs/specs/
 *  audit-page.md Tanda 2): la razón destacada, antes/después como tabla,
 *  el efecto, las cuentas que fallaron en un lote, los datos de la
 *  aprobación si hubo una, y un link para ver todo el mismo request. El
 *  JSON crudo de `detail` queda al final, colapsado. */
export function AuditEntryPanel({
  entry,
  onFilterRequestId,
  onFilterOperator,
  onFilterTarget,
}: {
  entry: AuditEntry;
  onFilterRequestId: (requestId: string) => void;
  onFilterOperator: (operator: string) => void;
  onFilterTarget: (target: string) => void;
}) {
  const [showRaw, setShowRaw] = useState(false);
  const d = entry.detail ?? {};
  const rows = diffRows(d);
  const failed = Array.isArray(d.failed) ? d.failed : [];
  const reason = typeof d.reason === "string" ? d.reason : null;
  const error = typeof d.error === "string" ? d.error : null;
  const effect = typeof d.effect === "string" ? d.effect : null;
  const hasApproval = d.approval_id !== undefined && d.approval_id !== null;

  return (
    <div className="flex flex-col gap-3 text-sm">
      <div className="grid grid-cols-[110px_1fr] gap-x-3 gap-y-1 text-xs">
        <span className="text-muted-foreground">Fecha</span>
        <span>{fmtDateTime(entry.ts)}</span>
        <span className="text-muted-foreground">Operador</span>
        <span>
          <button
            type="button"
            className="underline decoration-dotted underline-offset-2 hover:text-primary"
            onClick={() => onFilterOperator(entry.operator)}
          >
            {dash(entry.operator)}
          </button>{" "}
          · {entry.role}
        </span>
        <span className="text-muted-foreground">Tenant</span>
        <span>{dash(entry.tenant_id)}</span>
        <span className="text-muted-foreground">Categoría</span>
        <span>{entry.category}</span>
        <span className="text-muted-foreground">Acción</span>
        <span className="flex items-center gap-1.5">
          {entry.label} {kindBadge(entry.kind)}
        </span>
        <span className="text-muted-foreground">Target</span>
        <span>
          {entry.target ? (
            <button
              type="button"
              className="underline decoration-dotted underline-offset-2 hover:text-primary"
              onClick={() => onFilterTarget(entry.target!)}
            >
              {dash(entry.target_label)}
            </button>
          ) : (
            dash(entry.target_label)
          )}
        </span>
        <span className="text-muted-foreground">Resultado</span>
        <span>{resultBadge(entry.result)}</span>
      </div>

      {reason && (
        <div className="rounded-md border border-border bg-muted/50 p-2 text-xs">
          <span className="font-semibold">Razón: </span>
          {reason}
        </div>
      )}

      {error && (
        <div className="rounded-md border border-[#f5c6c6] bg-[#fbe2e2] p-2 text-xs text-danger-ink">
          {error}
        </div>
      )}

      {rows.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-semibold">Antes / después</p>
          <DiffTable rows={rows} />
        </div>
      )}

      {effect && (
        <div>
          <p className="mb-1 text-xs font-semibold">Efecto</p>
          <p className="text-xs">{effect}</p>
        </div>
      )}

      {failed.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-semibold text-danger-ink">Cuentas que fallaron</p>
          <ul className="list-disc pl-4 text-xs">
            {failed.map((f, i) => (
              <li key={i}>{typeof f === "string" ? f : JSON.stringify(f)}</li>
            ))}
          </ul>
        </div>
      )}

      {hasApproval && (
        <div className="rounded-md border border-border p-2 text-xs">
          <p className="mb-1 font-semibold">Aprobación</p>
          {typeof d.requested_by === "string" && (
            <p>
              Solicitó: {d.requested_by}
              {typeof d.request_reason === "string" && d.request_reason
                ? ` — ${d.request_reason}`
                : ""}
            </p>
          )}
          {typeof d.approved_by === "string" && <p>Aprobó: {d.approved_by}</p>}
        </div>
      )}

      {entry.request_id && (
        <button
          type="button"
          className="self-start text-xs text-primary underline"
          onClick={() => onFilterRequestId(entry.request_id!)}
        >
          Ver todo el mismo request ({entry.request_id})
        </button>
      )}

      <div>
        <button
          type="button"
          className="text-xs text-muted-foreground underline"
          onClick={() => setShowRaw((v) => !v)}
        >
          {showRaw ? "Ocultar" : "Ver"} JSON crudo
        </button>
        {showRaw && (
          <pre className="mt-1 max-h-64 overflow-auto rounded-md bg-muted p-2 text-[11px]">
            {JSON.stringify(d, null, 2)}
          </pre>
        )}
      </div>
    </div>
  );
}
