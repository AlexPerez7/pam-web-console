import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Check, ChevronDown, Search } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api } from "@/lib/api";
import type { PlatformsResponse } from "@/lib/types";

/** Selector de plataforma — reusa la lista cacheada de `/platforms`
 *  (mismo query key que `AccountCreateWizard`, sin round-trip extra).
 *  Sólo plataformas activas. Usado por `AccountEditSection` (editar una
 *  cuenta) y `AccountBulkEditDialog` (editar varias). Ver
 *  `docs/specs/account-change-platform.md`. */
export function PlatformField({
  tenant,
  value,
  onChange,
  systemType,
}: {
  tenant: string;
  value: string;
  onChange: (v: string) => void;
  systemType?: string;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");

  const platforms = useQuery({
    queryKey: ["inv", tenant, "platforms"],
    queryFn: () => api<PlatformsResponse>(`/tenants/${tenant}/platforms`),
    staleTime: 5 * 60_000,
  });

  const allRows = (platforms.data?.platforms ?? [])
    .filter((p) => p.general?.active !== false && p.general?.id)
    .filter((p) => !systemType || p.general?.systemType === systemType)
    .sort((a, b) => String(a.general?.name ?? "").localeCompare(String(b.general?.name ?? "")));

  const filteredRows = allRows.filter((p) => {
    if (!search) return true;
    const term = search.toLowerCase();
    return (
      p.general?.name?.toLowerCase().includes(term) ||
      p.general?.id?.toLowerCase().includes(term)
    );
  });

  const selectedName = allRows.find((p) => p.general?.id === value)?.general?.name ?? value;

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="h-9 w-full justify-between border-input bg-card px-3 text-sm font-normal text-foreground hover:bg-card hover:text-foreground"
        >
          <span className="truncate">{selectedName || "Elegir plataforma…"}</span>
          <ChevronDown className="ml-2 size-4 shrink-0 opacity-50" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="start"
        className="w-[300px] sm:w-[400px] max-h-80 overflow-hidden flex flex-col p-0"
      >
        <div className="flex items-center border-b border-border px-3">
          <Search className="mr-2 size-4 shrink-0 opacity-50" />
          <input
            className="flex h-10 w-full rounded-md bg-transparent py-3 text-sm outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50"
            placeholder="Buscar plataforma..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.stopPropagation()}
          />
        </div>
        <div className="overflow-y-auto p-1">
          {platforms.isLoading ? (
            <p className="py-6 text-center text-sm text-muted-foreground">Cargando…</p>
          ) : platforms.isError ? (
            <p className="py-6 text-center text-sm text-destructive">
              {(platforms.error as Error).message}
            </p>
          ) : filteredRows.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              No se encontraron plataformas.
            </p>
          ) : (
            filteredRows.map((p) => (
              <DropdownMenuItem
                key={p.general!.id}
                onSelect={() => {
                  onChange(p.general!.id as string);
                  setOpen(false);
                  setSearch("");
                }}
                className="flex cursor-pointer items-center justify-between"
              >
                <span>
                  {p.general?.name ?? p.general?.id}{" "}
                  <span className="text-xs text-muted-foreground">({p.general?.id})</span>
                </span>
                {value === p.general!.id && <Check className="ml-2 size-4" />}
              </DropdownMenuItem>
            ))
          )}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
