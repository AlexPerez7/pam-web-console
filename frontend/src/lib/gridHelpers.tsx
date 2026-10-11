import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";

const PAGE = 100;

type Srv = { key: string; asc: boolean } | null;

/** Estado de paginación server-side para una grilla. `extra` son filtros
 *  propios de la página (ej. safe_name en Cuentas); al cambiar, se resetea
 *  a la 1ª página. */
export function useServerGrid(
  extra?: Record<string, string | undefined>,
  initialLimit: number = PAGE,
) {
  const [offset, setOffset] = useState(0);
  const [limit, setLimitState] = useState(initialLimit);
  const [sort, setSort] = useState<Srv>(null);
  const [search, setSearch] = useState("");
  const extraStr = Object.entries(extra ?? {})
    .filter(([, v]) => v)
    .map(([k, v]) => `&${k}=${encodeURIComponent(v as string)}`)
    .join("");
  useEffect(() => setOffset(0), [extraStr]);
  const params =
    `offset=${offset}&limit=${limit}` +
    (search ? `&search=${encodeURIComponent(search)}` : "") +
    (sort ? `&sort=${encodeURIComponent((sort.asc ? "" : "-") + sort.key)}` : "") +
    extraStr;
  const onChange = (n: { offset?: number; sort?: Srv; search?: string }) => {
    if (n.offset !== undefined) setOffset(n.offset);
    if (n.sort !== undefined) setSort(n.sort);
    if (n.search !== undefined) setSearch(n.search);
  };
  /** cambia el tamaño de página y vuelve a la primera */
  const setLimit = (n: number) => {
    setLimitState(n);
    setOffset(0);
  };
  return { offset, limit, sort, search, params, onChange, setLimit };
}

export function boolBadge(v: boolean | undefined, on: string, off: string) {
  return v ? <Badge variant="success">{on}</Badge> : <Badge variant="neutral">{off}</Badge>;
}
