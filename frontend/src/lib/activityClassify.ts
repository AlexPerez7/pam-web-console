import {
  Activity,
  CalendarClock,
  Eye,
  KeyRound,
  Pencil,
  Plus,
  RefreshCcw,
  ShieldCheck,
  Tag,
  Trash2,
} from "lucide-react";
import type { ComponentType } from "react";

const SCHEDULED_LABEL: Record<string, string> = {
  changetask: "Cambio de contraseña programado",
  verifytask: "Verificación programada",
  reconciletask: "Reconciliación programada",
};

/** `reason` es opcional: sólo desambigua las "Add File Category" que
 *  programan una tarea del CPM ("Value=[ChangeTask]"). Sin él, una file
 *  category cae en "Propiedad de la cuenta" -- antes caía en "Alta de la
 *  cuenta" por contener "add". */
export function classify(action?: string, reason?: string): {
  label: string;
  icon: ComponentType<{ className?: string }>;
  fail: boolean;
  /** resultado de una operación del CPM ("CPM Change Password", "CPM
   *  Verify Password Failed", ...); `undefined` si no es una operación del
   *  CPM (ahí no hay éxito/falla que mostrar). */
  outcome?: "ok" | "fail";
} {
  const a = (action ?? "").toLowerCase();
  const fail = a.includes("fail");
  if (a.includes("file category")) {
    const task = /value=\[(\w+task)\]/i.exec(reason ?? "")?.[1]?.toLowerCase();
    if (task && SCHEDULED_LABEL[task])
      return a.includes("delet")
        ? { label: "Fin de la tarea programada", icon: CalendarClock, fail }
        : { label: SCHEDULED_LABEL[task], icon: CalendarClock, fail };
    return { label: "Propiedad de la cuenta", icon: Tag, fail };
  }
  const base = classifyAction(a, action, fail);
  const isCpmOp =
    a.includes("cpm") && ["verif", "reconcil", "change"].some((k) => a.includes(k));
  return isCpmOp ? { ...base, outcome: fail ? "fail" : "ok" } : base;
}

function classifyAction(
  a: string,
  action: string | undefined,
  fail: boolean,
): { label: string; icon: ComponentType<{ className?: string }>; fail: boolean } {
  if (a.includes("verif")) return { label: "Verificación", icon: ShieldCheck, fail };
  if (a.includes("reconcil")) return { label: "Reconciliación", icon: RefreshCcw, fail };
  if (a.includes("change") || a.includes("cambio"))
    return { label: "Cambio de contraseña", icon: KeyRound, fail };
  if (a.includes("retriev") || a.includes("show") || a.includes("get password"))
    return { label: "Recuperación del secreto", icon: Eye, fail };
  if (a.includes("add") || a.includes("create"))
    return { label: "Alta de la cuenta", icon: Plus, fail };
  if (a.includes("updat") || a.includes("modif"))
    return { label: "Modificación", icon: Pencil, fail };
  if (a.includes("delet")) return { label: "Baja de la cuenta", icon: Trash2, fail };
  return { label: action || "Actividad", icon: Activity, fail };
}

const TASK_ES: Record<string, string> = {
  changetask: "cambio programado",
  verifytask: "verificación programada",
  reconciletask: "reconciliación programada",
  immediatetask: "tarea inmediata",
};

/** Limpia la nota cruda (`Reason`) de una actividad de CyberArk para
 *  mostrarla: saca el prefijo técnico del CPM ("ImmediateTask. Failure
 *  Description: ]") y traduce "Value=[ChangeTask]"/"Old Value=[...]". El
 *  texto original completo queda disponible al expandir. */
export function cleanActivityNote(reason?: string): string {
  let t = String(reason ?? "").trim();
  if (!t) return "";
  t = t.replace(/^\w*Task\.\s*/i, "");
  t = t.replace(/Failure Description:\s*/i, "");
  t = t.replace(/^[\]\s:.-]+/, "");
  const task = (v: string) => TASK_ES[v.toLowerCase()] ?? v;
  t = t.replace(/Old Value=\[([^\]]*)\]/gi, (_, v: string) => `antes: ${task(v)}`);
  t = t.replace(/Value=\[([^\]]*)\]/gi, (_, v: string) => task(v));
  return t.trim();
}
