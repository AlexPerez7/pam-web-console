import { CheckCircle2, Eye, Pencil, XCircle } from "lucide-react";
import { useState, type ComponentType } from "react";
import { classify, cleanActivityNote } from "@/lib/activityClassify";
import { fmtDateTime, relTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { AccountActivity, AuditEntry } from "@/lib/types";

// --- item genérico de la línea de tiempo ------------------------------- //
function Item({
  icon: Icon,
  tone = "default",
  title,
  outcome,
  when,
  meta,
  note,
  rawNote,
}: {
  icon: ComponentType<{ className?: string }>;
  tone?: "default" | "fail";
  title: string;
  /** resultado de una operación del CPM: marca explícita junto al título */
  outcome?: "ok" | "fail";
  when: unknown;
  meta?: string;
  note?: string;
  /** texto original de la nota, si `note` es una versión limpia */
  rawNote?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  // sólo las notas largas (las que se cortan en 2 líneas) se expanden; al
  // expandir se muestra el texto original de CyberArk, sin limpiar
  const canExpand = !!note && note.length > 140;
  return (
    <li className="flex gap-2.5 text-xs">
      <span
        className={cn(
          "mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full",
          tone === "fail"
            ? "bg-[#fbe2e2] text-danger-ink"
            : "bg-muted text-muted-foreground",
        )}
      >
        <Icon className="size-3.5" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="flex items-center gap-1.5 font-medium text-foreground">
            {title}
            {outcome === "ok" && (
              <span className="inline-flex items-center gap-0.5 text-[11px] font-semibold text-success-ink">
                <CheckCircle2 className="size-3" aria-hidden="true" />
                OK
              </span>
            )}
            {outcome === "fail" && (
              <span className="inline-flex items-center gap-0.5 text-[11px] font-semibold text-danger-ink">
                <XCircle className="size-3" aria-hidden="true" />
                Falló
              </span>
            )}
          </span>
          <span className="shrink-0 text-muted-foreground" title={fmtDateTime(when)}>
            {relTime(when)}
          </span>
        </div>
        {meta && <div className="truncate text-muted-foreground">{meta}</div>}
        {note && (
          <div className="mt-0.5 text-muted-foreground">
            <span className={cn("break-words", !expanded && "line-clamp-2")}>
              {expanded && rawNote ? rawNote : note}
            </span>
            {canExpand && (
              <button
                type="button"
                onClick={() => setExpanded((v) => !v)}
                className="ml-1 font-semibold text-sky-600 hover:underline"
              >
                {expanded ? "ver menos" : "ver completo"}
              </button>
            )}
          </div>
        )}
      </div>
    </li>
  );
}

// --- actividad de CyberArk -------------------------------------------- //
export function AccountActivityFeed({ activities }: { activities: AccountActivity[] }) {
  if (activities.length === 0)
    return <p className="text-sm text-muted-foreground">Sin actividad registrada.</p>;
  return (
    <ol className="flex flex-col gap-3">
      {activities.slice(0, 50).map((a, i) => {
        const raw = a.Reason ? String(a.Reason) : undefined;
        const { label, icon, fail, outcome } = classify(a.Action, raw);
        const meta = [a.User, a.ClientType, a.Safe].filter(Boolean).join(" · ");
        return (
          <Item
            key={i}
            icon={icon}
            tone={fail ? "fail" : "default"}
            title={label}
            outcome={outcome}
            when={a.Date}
            meta={meta || undefined}
            note={raw ? cleanActivityNote(raw) || undefined : undefined}
            rawNote={raw}
          />
        );
      })}
    </ol>
  );
}

// --- rastro del audit de PAM Web Console ---------------------------------------- //
const ACTION_LABEL: Record<string, string> = {
  "account.get": "Consulta del detalle",
  "account.activities": "Consulta de la actividad",
  "account.update": "Editar metadata",
  "account.verify": "Verificación (on-demand)",
  "account.change": "Cambio / Reconciliación (on-demand)",
};

function prettyAction(action: string): string {
  return ACTION_LABEL[action] ?? action;
}

function resultText(result: string): string {
  if (result === "ok") return "ok";
  if (result === "denied") return "denegado";
  if (result === "locked") return "bloqueado";
  return "error";
}

export function AuditTrailFeed({ entries }: { entries: AuditEntry[] }) {
  if (entries.length === 0)
    return (
      <p className="text-sm text-muted-foreground">
        Ningún operador de PAM Web Console tocó este objeto todavía.
      </p>
    );
  return (
    <ol className="flex flex-col gap-3">
      {entries.map((e) => {
        const reason = typeof e.detail?.reason === "string" ? e.detail.reason : undefined;
        const isWrite = e.action.includes("update") || e.action.includes("change");
        return (
          <Item
            key={e.id}
            icon={isWrite ? Pencil : Eye}
            tone={e.result === "ok" ? "default" : "fail"}
            title={`${prettyAction(e.action)}${e.dry_run ? " · dry-run" : ""}`}
            when={e.ts}
            meta={`${e.operator} · ${resultText(e.result)}`}
            note={reason}
          />
        );
      })}
    </ol>
  );
}
