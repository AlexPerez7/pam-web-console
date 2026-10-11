import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api } from "@/lib/api";
import { dash } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { ResourceSearchResponse, SearchResource } from "@/lib/types";

type Item = Record<string, unknown>;

const RESOURCES: { value: SearchResource; label: string }[] = [
  { value: "accounts", label: "Cuentas" },
  { value: "safes", label: "Safes" },
  { value: "platforms", label: "Plataformas" },
  { value: "users", label: "Usuarios" },
  { value: "groups", label: "Grupos" },
];

/** Ruta de cada recurso — Cuentas/Safes/Plataformas tienen página propia.
 *  Usuarios/Grupos del Vault NO tienen página desde 2026-09-24 ("Users and
 *  Groups" es un concepto de PAM Self-Hosted, no aplica a Privilege Cloud —
 *  la administración real de usuarios es vía Identity, ver `/sso-users`) —
 *  la búsqueda cross-tenant sigue andando, sólo sin el link "Ver en
 *  inventario". */
const RESOURCE_PATH: Partial<Record<SearchResource, string>> = {
  accounts: "/",
  safes: "/safes",
  platforms: "/platforms",
};

const g = (i: Item, k: string) => (i.general as Item | undefined)?.[k];
const pd = (i: Item, k: string) => (i.personalDetails as Item | undefined)?.[k];

const COLUMNS: Record<
  SearchResource,
  { headers: string[]; cells: (i: Item) => React.ReactNode[] }
> = {
  accounts: {
    headers: ["Nombre", "Usuario", "Address", "Safe", "Plataforma"],
    cells: (i) => [
      dash(i.name),
      dash(i.userName),
      dash(i.address),
      dash(i.safeName),
      dash(i.platformId),
    ],
  },
  safes: {
    headers: ["Safe", "Descripción", "CPM"],
    cells: (i) => [dash(i.safeName), dash(i.description), dash(i.managingCPM)],
  },
  platforms: {
    headers: ["ID", "Nombre", "Tipo de sistema"],
    cells: (i) => [dash(g(i, "id")), dash(g(i, "name")), dash(g(i, "systemType"))],
  },
  users: {
    headers: ["Usuario", "Nombre", "Tipo"],
    cells: (i) => [
      dash(i.username),
      dash([pd(i, "firstName"), pd(i, "lastName")].filter(Boolean).join(" ")),
      dash(i.userType),
    ],
  },
  groups: {
    headers: ["Grupo", "Tipo", "Directorio", "Descripción"],
    cells: (i) => [
      dash(i.groupName),
      dash(i.groupType),
      dash(i.directory),
      dash(i.description),
    ],
  },
};

export default function Search() {
  const [term, setTerm] = useState("");
  const [query, setQuery] = useState("");
  const [resource, setResource] = useState<SearchResource>("accounts");

  const q = useQuery({
    queryKey: ["search", resource, query],
    queryFn: () =>
      api<ResourceSearchResponse>(
        `/search/${resource}?q=${encodeURIComponent(query)}`,
      ),
    enabled: query.length >= 2,
    staleTime: 60_000,
  });

  function submit() {
    const t = term.trim();
    if (t.length >= 2) setQuery(t);
  }

  const cols = COLUMNS[resource];
  const withHits = q.data?.results.filter((r) => r.count > 0) ?? [];
  const noHits = q.data?.results.filter((r) => r.count === 0) ?? [];

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-[22px] font-bold text-navy-900">Búsqueda cross-tenant</h1>

      <div className="flex flex-wrap gap-1">
        {RESOURCES.map((r) => (
          <button
            key={r.value}
            onClick={() => setResource(r.value)}
            className={cn(
              "rounded-md px-3 py-1.5 text-sm transition-colors",
              resource === r.value
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-muted-foreground hover:text-foreground",
            )}
          >
            {r.label}
          </button>
        ))}
      </div>

      <div className="flex max-w-xl gap-2">
        <Input
          className="flex-1"
          placeholder="mínimo 2 caracteres"
          value={term}
          onChange={(e) => setTerm(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
        <Button onClick={submit} disabled={term.trim().length < 2 || q.isFetching}>
          {q.isFetching && <Loader2 className="animate-spin" />}
          Buscar
        </Button>
      </div>

      {!query && (
        <p className="text-muted-foreground">
          Busca en todos los tenants accesibles a la vez.
        </p>
      )}

      {q.isLoading && query && (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          <span>Buscando "{query}" en todos los tenants…</span>
        </div>
      )}

      {q.isError && <Alert variant="destructive">{(q.error as Error).message}</Alert>}

      {q.data && (
        <>
          {q.data.errors.map((e) => (
            <Alert key={e.tenant} variant="warning">
              <b>{e.tenant_name}</b>: no se pudo buscar — {e.error}
            </Alert>
          ))}

          {withHits.length === 0 && q.data.errors.length === 0 && (
            <p className="text-muted-foreground">
              Sin coincidencias para "{q.data.query}" en ningún tenant.
            </p>
          )}

          {withHits.map((r) => (
            <Card key={r.tenant}>
              <CardHeader className="flex-row items-center justify-between gap-3 space-y-0">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-navy-900">{r.tenant_name}</span>
                  <Badge variant="info">{r.count}</Badge>
                  {r.returned < r.count && (
                    <span className="text-xs text-muted-foreground">
                      mostrando {r.returned}
                    </span>
                  )}
                </div>
                {RESOURCE_PATH[resource] && (
                  <Button asChild variant="outline" size="sm">
                    <Link to={`${RESOURCE_PATH[resource]}?tenant=${r.tenant}`}>
                      Ver en inventario
                    </Link>
                  </Button>
                )}
              </CardHeader>
              <CardContent>
                <div className="rounded-lg border border-border">
                  <Table style={{ minWidth: 600 }}>
                    <TableHeader>
                      <TableRow>
                        {cols.headers.map((h) => (
                          <TableHead key={h}>{h}</TableHead>
                        ))}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {r.items.map((it, i) => (
                        <TableRow key={i}>
                          {cols.cells(it).map((c, j) => (
                            <TableCell key={j}>{c}</TableCell>
                          ))}
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </CardContent>
            </Card>
          ))}

          {noHits.length > 0 && (
            <p className="text-sm text-muted-foreground">
              Sin coincidencias: {noHits.map((r) => r.tenant_name).join(", ")}
            </p>
          )}
        </>
      )}
    </div>
  );
}
