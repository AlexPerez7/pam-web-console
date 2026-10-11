import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api } from "@/lib/api";
import type { SafeNamesResponse } from "@/lib/types";

/** Selector de PSM Server — lista los configurados en el tenant (`GET
 *  /psm-servers`) + opción de tipearlo a mano. Mismo patrón que
 *  `CpmField`. Usado por `PlatformPsmSection`. */
export function PsmServerField({
  tenant,
  value,
  onChange,
}: {
  tenant: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const [manual, setManual] = useState(false);
  const servers = useQuery({
    queryKey: ["psm-servers", tenant],
    queryFn: () => api<SafeNamesResponse>(`/tenants/${tenant}/psm-servers`),
    staleTime: 5 * 60_000,
  });
  const names = servers.data?.names ?? [];

  useEffect(() => {
    if (value && names.length > 0 && !names.includes(value)) setManual(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [names.length]);

  if (manual || (names.length === 0 && !servers.isLoading)) {
    return (
      <div className="flex flex-col gap-1">
        <Input
          className="h-8 text-sm"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="PSMServerId exacto"
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
    <div className="flex flex-col gap-1">
      <Select value={value || undefined} onValueChange={onChange}>
        <SelectTrigger aria-label="Servidor PSM" className="h-8 text-sm">
          <SelectValue placeholder="Elegir PSM…" />
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
