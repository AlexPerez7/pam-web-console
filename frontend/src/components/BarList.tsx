import { cn } from "@/lib/utils";
import type { ChartDatum } from "@/lib/types";

interface Props {
  data: ChartDatum[];
  /** color de la barra (CSS var o hex). Default: sky-500. */
  color?: string;
  labelWidth?: number;
  emptyLabel?: string;
  /** tope de filas; el resto se colapsa en "+N más" */
  maxItems?: number;
  /** si viene, cada fila es clickeable (drill-down por categoría) */
  onItemClick?: (label: string) => void;
}

/** Lista de barras horizontales (look de la maqueta del design system). Sin librería. */
export function BarList({
  data,
  color = "var(--color-sky-500)",
  labelWidth = 150,
  emptyLabel = "Sin datos",
  maxItems,
  onItemClick,
}: Props) {
  if (data.length === 0)
    return <p className="text-sm text-muted-foreground">{emptyLabel}</p>;

  const max = Math.max(1, ...data.map((d) => d.value));
  const shown = maxItems ? data.slice(0, maxItems) : data;
  const hidden = data.length - shown.length;

  return (
    <div className="flex flex-col gap-2.5">
      {shown.map((d) => (
        <div
          key={d.label}
          role={onItemClick ? "button" : undefined}
          tabIndex={onItemClick ? 0 : undefined}
          onClick={onItemClick ? () => onItemClick(d.label) : undefined}
          onKeyDown={
            onItemClick
              ? (e) => {
                  if (e.key === "Enter" || e.key === " ") onItemClick(d.label);
                }
              : undefined
          }
          className={cn(
            "grid items-center gap-2.5",
            onItemClick && "cursor-pointer rounded-sm hover:bg-muted/60",
          )}
          style={{ gridTemplateColumns: `${labelWidth}px 1fr 44px` }}
        >
          <span className="truncate text-xs text-foreground" title={d.label}>
            {d.label}
          </span>
          <div className="h-2 overflow-hidden rounded-sm bg-muted">
            <div
              className="h-full rounded-sm transition-[width] duration-500 ease-out motion-reduce:transition-none"
              style={{
                width: `${(d.value / max) * 100}%`,
                minWidth: d.value > 0 ? 3 : 0,
                background: color,
              }}
            />
          </div>
          <span className="text-right text-xs text-muted-foreground">
            {d.value.toLocaleString("es")}
          </span>
        </div>
      ))}
      {hidden > 0 && (
        <p className="text-[11px] text-muted-foreground">+{hidden} más</p>
      )}
    </div>
  );
}
