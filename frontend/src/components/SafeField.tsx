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
import type { SafeNamesResponse } from "@/lib/types";

export function SafeField({
  tenant,
  value,
  onChange,
}: {
  tenant: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");

  const safes = useQuery({
    queryKey: ["inv", tenant, "safes", "names"],
    queryFn: () => api<SafeNamesResponse>(`/tenants/${tenant}/safe-names`),
    staleTime: 5 * 60_000,
  });

  const allRows: string[] = (safes.data?.names ?? []).sort((a: string, b: string) => a.localeCompare(b));

  const filteredRows = allRows.filter((name: string) => {
    if (!search) return true;
    return name.toLowerCase().includes(search.toLowerCase());
  });

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="h-9 w-full justify-between border-input bg-card px-3 text-sm font-normal text-foreground hover:bg-card hover:text-foreground"
        >
          <span className="truncate">{value || "Elegir Safe…"}</span>
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
            placeholder="Buscar Safe..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.stopPropagation()}
          />
        </div>
        <div className="overflow-y-auto p-1">
          {safes.isLoading ? (
            <p className="py-6 text-center text-sm text-muted-foreground">Cargando…</p>
          ) : safes.isError ? (
            <p className="py-6 text-center text-sm text-destructive">
              {(safes.error as Error).message}
            </p>
          ) : filteredRows.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              No se encontraron Safes.
            </p>
          ) : (
            filteredRows.map((name: string) => (
              <DropdownMenuItem
                key={name}
                onSelect={() => {
                  onChange(name);
                  setOpen(false);
                  setSearch("");
                }}
                className="flex cursor-pointer items-center justify-between"
              >
                <span>{name}</span>
                {value === name && <Check className="ml-2 size-4" />}
              </DropdownMenuItem>
            ))
          )}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
