import type { ChartDatum } from "@/lib/types";

interface Props {
  data: ChartDatum[];
  height?: number;
  color?: string;
  /** cuántas etiquetas del eje X mostrar como máximo */
  maxLabels?: number;
}

/**
 * Barras verticales compactas para series temporales (muchos puntos). Sin
 * librería. Responsive: las barras reparten el ancho; las etiquetas se ralean.
 */
export function MiniBars({
  data,
  height = 120,
  color = "var(--color-line-cloud)",
  maxLabels = 8,
}: Props) {
  if (data.length === 0)
    return <p className="text-sm text-muted-foreground">Sin datos</p>;

  const max = Math.max(1, ...data.map((d) => d.value));
  const step = Math.max(1, Math.ceil(data.length / maxLabels));

  return (
    <div>
      <div className="flex items-end gap-0.5" style={{ height }}>
        {data.map((d) => (
          <div
            key={d.label}
            title={`${d.label}: ${d.value}`}
            className="min-w-0.5 flex-1 rounded-[1px]"
            style={{
              height: `${(d.value / max) * 100}%`,
              minHeight: d.value > 0 ? 2 : 0,
              background: color,
            }}
          />
        ))}
      </div>
      <div className="mt-1 flex gap-0.5">
        {data.map((d, i) => (
          <div key={d.label} className="min-w-0.5 flex-1 overflow-hidden">
            {i % step === 0 && (
              <span className="whitespace-nowrap text-[9px] text-muted-foreground">
                {d.label}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
