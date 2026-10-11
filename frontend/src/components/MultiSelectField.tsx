import { ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

/**
 * Selector de varios valores (parámetro `multiselect` de un reporte). El
 * valor viaja como una sola cadena separada por comas ("A,B"), igual que
 * lo espera el backend (`app.reports.base.multi_values`) — así entra tal
 * cual en la query string y en la cache key de los reportes.
 */
export function MultiSelectField({
  options,
  labels,
  value,
  onChange,
  placeholder = "Todos",
  ariaLabel,
  className,
}: {
  options: string[];
  labels?: Record<string, string> | null;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  ariaLabel: string;
  className?: string;
}) {
  const selected = value ? value.split(",").filter((v) => options.includes(v)) : [];
  const label = (o: string) => labels?.[o] ?? o;

  function toggle(o: string, checked: boolean) {
    const next = checked ? [...selected, o] : selected.filter((v) => v !== o);
    // conserva el orden de `options`, para que la misma selección siempre
    // arme la misma query (y reuse el resultado cacheado)
    onChange(options.filter((v) => next.includes(v)).join(","));
  }

  const summary =
    selected.length === 0
      ? placeholder
      : selected.length === 1
        ? label(selected[0])
        : `${selected.length} seleccionados`;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="outline"
          aria-label={ariaLabel}
          title={selected.map(label).join(", ") || undefined}
          className={cn("mt-1 h-9 w-56 justify-between font-normal", className)}
        >
          <span className={cn("truncate", selected.length === 0 && "text-muted-foreground")}>
            {summary}
          </span>
          <ChevronDown className="size-4 shrink-0 opacity-50" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-[16rem]">
        {options.map((o) => (
          <DropdownMenuCheckboxItem
            key={o}
            checked={selected.includes(o)}
            onCheckedChange={(c) => toggle(o, c === true)}
            // no cerrar el menú al tildar: se eligen varios de una
            onSelect={(e) => e.preventDefault()}
          >
            {label(o)}
          </DropdownMenuCheckboxItem>
        ))}
        {selected.length > 0 && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => onChange("")}>Limpiar selección</DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
