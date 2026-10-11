import { useQuery } from "@tanstack/react-query";
import { Check, ChevronDown, Loader2, Search } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api } from "@/lib/api";
import type { MemberSearchResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

const MIN_CHARS = 2;
const DEBOUNCE_MS = 300;

/**
 * Buscador de usuarios de un tenant (parámetro `user` de un reporte): se
 * escribe parte del nombre o del correo ("jdoe") y muestra coincidencias
 * de Identity y del Vault. Reusa `GET /tenants/{id}/member-search?kind=user`,
 * el mismo buscador de "Agregar miembro a safe". El valor elegido es el
 * nombre de usuario completo (ej. `jdoe@globex.example`).
 */
export function UserSearchField({
  tenant,
  value,
  onChange,
  ariaLabel,
  placeholder = "Buscar usuario…",
  className,
}: {
  tenant: string;
  value: string;
  onChange: (value: string) => void;
  ariaLabel: string;
  placeholder?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");

  useEffect(() => {
    const t = setTimeout(() => setDebounced(search.trim()), DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [search]);

  const enabled = !!tenant && debounced.length >= MIN_CHARS;
  const results = useQuery({
    queryKey: ["member-search", tenant, "user", debounced],
    queryFn: () =>
      api<MemberSearchResponse>(
        `/tenants/${tenant}/member-search?kind=user&q=${encodeURIComponent(debounced)}`,
      ),
    enabled,
    staleTime: 60_000,
  });
  const users = (results.data?.results ?? []).filter((r) => r.kind === "user");

  function pick(name: string) {
    onChange(name);
    setOpen(false);
    setSearch("");
  }

  let body;
  if (!tenant) {
    body = <Hint>Elija primero un tenant.</Hint>;
  } else if (search.trim().length < MIN_CHARS) {
    body = <Hint>Escriba al menos {MIN_CHARS} letras del nombre o del correo.</Hint>;
  } else if (!enabled || results.isFetching) {
    body = (
      <Hint>
        <Loader2 className="mx-auto size-4 animate-spin" aria-label="Buscando" />
      </Hint>
    );
  } else if (results.isError) {
    body = <p className="py-6 text-center text-sm text-danger-ink">{(results.error as Error).message}</p>;
  } else if (users.length === 0) {
    body = <Hint>No se encontraron usuarios.</Hint>;
  } else {
    body = users.map((u) => (
      <DropdownMenuItem
        key={`${u.source}:${u.name}`}
        onSelect={() => pick(u.name)}
        className="flex cursor-pointer items-center justify-between gap-2"
      >
        <span className="flex min-w-0 flex-col">
          <span className="truncate">{u.displayName || u.name}</span>
          <span className="truncate text-xs text-muted-foreground">
            {u.name}
            {u.source ? ` · ${u.source}` : ""}
          </span>
        </span>
        {value.toLowerCase() === u.name.toLowerCase() && <Check className="size-4 shrink-0" />}
      </DropdownMenuItem>
    ));
  }

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-label={ariaLabel}
          className={cn("mt-1 h-9 w-72 justify-between font-normal", className)}
        >
          <span className={cn("truncate", !value && "text-muted-foreground")}>
            {value || placeholder}
          </span>
          <ChevronDown className="size-4 shrink-0 opacity-50" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="flex max-h-80 w-[22rem] flex-col overflow-hidden p-0">
        <div className="flex items-center border-b border-border px-3">
          <Search className="mr-2 size-4 shrink-0 opacity-50" />
          <input
            autoFocus
            aria-label={`${ariaLabel}: buscar`}
            className="flex h-10 w-full bg-transparent py-3 text-sm outline-none placeholder:text-muted-foreground"
            placeholder="Nombre o correo (ej. jdoe)"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.stopPropagation()}
          />
        </div>
        <div className="overflow-y-auto p-1">{body}</div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function Hint({ children }: { children: ReactNode }) {
  return <div className="px-3 py-6 text-center text-sm text-muted-foreground">{children}</div>;
}
