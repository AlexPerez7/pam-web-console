import type { CpmAction } from "@/lib/cpmActions";
import type { AccountActivity } from "@/lib/types";

/** Tarea del CPM pedida a mano y todavía no procesada. */
export interface PendingCpmTask {
  action: CpmAction;
  /** Fecha de la actividad que la programó (mismo formato que `Date`). */
  since?: number;
  by?: string;
}

// "Cambiar ahora"/"Verificar"/"Reconciliar" (desde el PVWA o desde PAM Web Console)
// no ejecutan nada en el momento: CyberArk agrega a la cuenta la file
// category `ResetImmediately` con valor ChangeTask/VerifyTask/
// ReconcileTask y el CPM la toma en su próximo ciclo (puede tardar varios
// minutos). En la actividad de la cuenta queda como "Add File Category"
// con `Reason` "Value=[ChangeTask]". Es lo que usa el PVWA para mostrar
// "The password for this account has been manually scheduled for change";
// la API Gen2 no lo expone en `secretManagement` (el `status` queda vacío
// hasta que el CPM arranca), así que se deduce de la actividad.
const TASKS: Record<string, CpmAction> = {
  changetask: "change",
  verifytask: "verify",
  reconciletask: "reconcile",
};

const CPM_KEYWORD: Record<CpmAction, string[]> = {
  change: ["change", "cambio"],
  verify: ["verif"],
  reconcile: ["reconcil"],
};

/** Tarea en `Reason` de una actividad ("Value=[ChangeTask]"), si la hay. */
export function scheduledTaskOf(a: AccountActivity): CpmAction | null {
  const m = /value=\[(\w+task)\]/i.exec(String(a.Reason ?? ""));
  return m ? (TASKS[m[1].toLowerCase()] ?? null) : null;
}

/** La tarea del CPM programada más reciente que todavía no tiene una
 *  ejecución del CPM posterior, o `null`. Recorre la actividad de la más
 *  nueva a la más vieja: una ejecución del CPM (éxito o falla) o el borrado
 *  de la file category "limpia" las programaciones anteriores. */
export function pendingCpmTask(activities: AccountActivity[]): PendingCpmTask | null {
  const cleared = new Set<CpmAction>();
  const sorted = [...activities].sort((a, b) => (b.Date ?? 0) - (a.Date ?? 0));
  for (const a of sorted) {
    const action = (a.Action ?? "").toLowerCase();
    const task = scheduledTaskOf(a);
    if (action.includes("file category")) {
      if (action.includes("delet")) {
        // se quitó ResetImmediately: el CPM tomó la tarea o se canceló.
        if (task) cleared.add(task);
        else if (/resetimmediately/i.test(String(a.Reason ?? "")))
          (Object.values(TASKS) as CpmAction[]).forEach((t) => cleared.add(t));
        continue;
      }
      if (task && !cleared.has(task))
        return { action: task, since: a.Date, by: a.User ? String(a.User) : undefined };
      continue;
    }
    if (action.includes("cpm")) {
      for (const [t, kws] of Object.entries(CPM_KEYWORD) as [CpmAction, string[]][])
        if (kws.some((k) => action.includes(k))) cleared.add(t);
    }
  }
  return null;
}
