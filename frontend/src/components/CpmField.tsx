import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api } from "@/lib/api";
import type { SafeNamesResponse } from "@/lib/types";

/** Selector de CPM: lista los que ya gestionan alguna safe del tenant
 *  (`GET /cpm-names` — CyberArk no expone un "listar CPMs" directo, se
 *  infiere de las safes existentes) + opción de tipearlo a mano para un CPM
 *  que todavía no gestiona ninguna. Usado por `SafeCreateDialog` y
 *  `SafeEditSection`. */
export function CpmField({
  tenant,
  value,
  onChange,
}: {
  tenant: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const [manual, setManual] = useState(false);
  const cpms = useQuery({
    queryKey: ["cpm-names", tenant],
    queryFn: () => api<SafeNamesResponse>(`/tenants/${tenant}/cpm-names`),
    staleTime: 5 * 60_000,
  });
  const names = cpms.data?.names ?? [];

  useEffect(() => {
    // el valor prefilled (de una safe existente o de /safes/defaults) puede
    // no estar en la lista -> arrancar en modo manual para no perderlo.
    if (value && names.length > 0 && !names.includes(value)) setManual(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [names.length]);

  if (manual || (names.length === 0 && !cpms.isLoading)) {
    return (
      <div className="mt-1 flex flex-col gap-1">
        <Input
          className="h-8 text-sm"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="Nombre exacto del CPM"
        />
        {names.length > 0 && (
          <button
            type="button"
            onClick={() => setManual(false)}
            className="self-start text-xs text-sky-600 hover:underline"
          >
            ← elegir de la lista
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="mt-1 flex flex-col gap-1">
      <Select value={value || undefined} onValueChange={onChange}>
        <SelectTrigger aria-label="CPM" className="h-8 text-sm">
          <SelectValue placeholder="Elegir CPM…" />
        </SelectTrigger>
        <SelectContent>
          {names.map((n) => (
            <SelectItem key={n} value={n}>
              {n}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <button
        type="button"
        onClick={() => setManual(true)}
        className="self-start text-xs text-sky-600 hover:underline"
      >
        …o ingresarlo a mano
      </button>
    </div>
  );
}
