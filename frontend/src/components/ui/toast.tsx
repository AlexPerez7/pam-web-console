import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { cn } from "@/lib/utils";

// Notificaciones efímeras ("toasts"), sin dependencias. `toast()` se puede
// llamar desde cualquier lado (componentes, callbacks de mutaciones). Los
// éxitos/avisos se anuncian con aria-live="polite" y los errores con
// role="alert" (lectores de pantalla); se pausan con el mouse encima o con
// foco de teclado dentro, y se cierran con Esc o con el botón.

export type ToastVariant = "success" | "error" | "info" | "warning";

export interface ToastInput {
  title: string;
  description?: string;
  variant?: ToastVariant;
  /** ms antes de cerrarse solo (default 5 s; errores 8 s) */
  duration?: number;
}

interface ToastItem extends Required<Omit<ToastInput, "description">> {
  id: number;
  description?: string;
}

const MAX_VISIBLE = 4;
let items: ToastItem[] = [];
let nextId = 1;
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

export function toast(input: ToastInput): number {
  const variant = input.variant ?? "success";
  const item: ToastItem = {
    id: nextId++,
    title: input.title,
    description: input.description,
    variant,
    duration: input.duration ?? (variant === "error" ? 8000 : 5000),
  };
  items = [...items, item].slice(-MAX_VISIBLE);
  emit();
  return item.id;
}

export function dismissToast(id: number) {
  items = items.filter((t) => t.id !== id);
  emit();
}

/** Atajo para errores de una mutación/request. */
export function toastError(title: string, error: unknown) {
  toast({
    title,
    description: error instanceof Error ? error.message : String(error ?? ""),
    variant: "error",
  });
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

const STYLE: Record<ToastVariant, { cls: string; Icon: typeof Info }> = {
  success: { cls: "border-line-identity/40 text-success-ink", Icon: CheckCircle2 },
  error: { cls: "border-line-cyberdefense/40 text-danger-ink", Icon: XCircle },
  warning: { cls: "border-line-consulting/40 text-warning-ink", Icon: AlertTriangle },
  info: { cls: "border-sky-300 text-navy-900", Icon: Info },
};

function ToastView({ t }: { t: ToastItem }) {
  const [paused, setPaused] = useState(false);
  const remaining = useRef(t.duration);
  const startedAt = useRef(0);

  useEffect(() => {
    if (paused) return;
    startedAt.current = Date.now();
    const timer = setTimeout(() => dismissToast(t.id), remaining.current);
    return () => {
      clearTimeout(timer);
      remaining.current -= Date.now() - startedAt.current;
    };
  }, [paused, t.id]);

  const { cls, Icon } = STYLE[t.variant];
  return (
    <div
      role={t.variant === "error" ? "alert" : "status"}
      aria-atomic="true"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
      onKeyDown={(e) => e.key === "Escape" && dismissToast(t.id)}
      className={cn(
        "pointer-events-auto flex w-full items-start gap-3 rounded-lg border bg-card p-3 shadow-lg",
        "animate-in fade-in-0 slide-in-from-bottom-2",
        cls,
      )}
    >
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold">{t.title}</p>
        {t.description && (
          <p className="mt-0.5 break-words text-sm text-foreground/80">{t.description}</p>
        )}
      </div>
      <button
        type="button"
        onClick={() => dismissToast(t.id)}
        className="rounded-sm text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
        aria-label="Cerrar notificación"
      >
        <X className="size-4" aria-hidden="true" />
      </button>
    </div>
  );
}

/** Se monta una sola vez (main.tsx). */
export function Toaster() {
  const list = useSyncExternalStore(subscribe, () => items);
  return (
    <section
      aria-label="Notificaciones"
      className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2 print:hidden"
    >
      {list.map((t) => (
        <ToastView key={t.id} t={t} />
      ))}
    </section>
  );
}
