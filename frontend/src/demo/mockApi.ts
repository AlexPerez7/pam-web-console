// Demo estática (GitHub Pages): responde `/api/...` en el navegador, sin
// backend. Sólo se incluye en el build con `VITE_DEMO=true` (ver main.tsx).
//
// Los datos son respuestas REALES de la API, generadas por
// `backend/tools/export_demo_site.py` con el backend en modo demo (tenants
// ficticios) y servidas como `demo-data/<tenant>.json`. Las listas grandes
// (cuentas, safes) se guardan completas y aquí se paginan, filtran y ordenan
// como lo haría el servidor. Las escrituras responden un 403 explicativo:
// esta página es de solo lectura (el modo demo completo, con dry-run y
// escrituras, corre con Docker — ver README).

type Store = Record<string, unknown>;
type Row = Record<string, unknown>;

const READ_ONLY =
  "Demo estática de solo lectura: las escrituras están deshabilitadas en esta página. " +
  "Para probar dry-run, aprobaciones y cambios, levante el modo demo completo con Docker (ver README).";
const NOT_AVAILABLE = "No disponible en la demo estática.";

const stores: Record<string, Promise<Store>> = {};

function load(name: string): Promise<Store> {
  stores[name] ??= fetch(`${import.meta.env.BASE_URL}demo-data/${name}.json`)
    .then((r) => (r.ok ? (r.json() as Promise<Store>) : {}))
    .catch(() => ({}));
  return stores[name];
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** Misma normalización que el exportador: path + query ordenada. */
function storeKey(path: string, params: URLSearchParams): string {
  const entries = [...params.entries()].sort(([a], [b]) => a.localeCompare(b));
  if (!entries.length) return path;
  return `${path}?${new URLSearchParams(entries).toString()}`;
}

function text(row: unknown): string {
  return JSON.stringify(row).toLowerCase();
}

function get(row: Row, field: string): unknown {
  return field.split(".").reduce<unknown>((v, k) => (v as Row | undefined)?.[k], row);
}

function accountMatches(a: Row, params: URLSearchParams): boolean {
  const sm = (a.secretManagement ?? {}) as Row;
  const eq = (field: string, param: string) => {
    const v = params.get(param);
    return !v || String(a[field] ?? "").toLowerCase() === v.toLowerCase();
  };
  const status = params.get("status");
  if (status === "failure" && sm.status !== "failure") return false;
  if (status === "manual" && sm.automaticManagementEnabled !== false) return false;
  if (status === "cpm_disabled") return false;
  return eq("safeName", "safe_name") && eq("platformId", "platform_id") && eq("userName", "user_name") &&
    eq("address", "address");
}

/** Pagina/filtra/ordena una respuesta de lista completa como el servidor. */
function paginate(full: Row, params: URLSearchParams, extra?: (r: Row) => boolean): Row {
  const listKey = Object.keys(full).find((k) => Array.isArray(full[k]));
  if (!listKey) return full;
  let rows = full[listKey] as Row[];
  const search = params.get("search")?.toLowerCase().split(/\s+/).filter(Boolean) ?? [];
  if (search.length) rows = rows.filter((r) => search.every((t) => text(r).includes(t)));
  if (extra) rows = rows.filter(extra);
  const sort = params.get("sort");
  if (sort) {
    const [field, dir] = sort.split(" ");
    rows = [...rows].sort((a, b) => {
      const c = String(get(a, field) ?? "").localeCompare(String(get(b, field) ?? ""), "es", {
        numeric: true,
      });
      return dir?.toLowerCase() === "desc" ? -c : c;
    });
  }
  const offset = Number(params.get("offset") ?? 0);
  const limit = Number(params.get("limit") ?? 100);
  const page: Row = { ...full, offset, limit, [listKey]: rows.slice(offset, offset + limit) };
  page["total" in full ? "total" : "count"] = rows.length;
  return page;
}

const SEARCH_LISTS: Record<string, [string, string]> = {
  accounts: ["accounts", "accounts"],
  safes: ["safes", "safes"],
  platforms: ["platforms", "platforms"],
  users: ["users", "users"],
  groups: ["groups", "groups"],
};

async function crossTenantSearch(resource: string, q: string): Promise<Row> {
  const tenants = ((await load("global"))["/tenants"] ?? []) as Row[];
  const [sub, field] = SEARCH_LISTS[resource] ?? [resource, resource];
  const term = q.toLowerCase();
  const results = await Promise.all(
    (Array.isArray(tenants) ? tenants : []).map(async (t) => {
      const store = await load(String(t.id));
      const items = (((store[`/tenants/${t.id}/${sub}`] ?? {}) as Row)[field] ?? []) as Row[];
      const hits = items.filter((r) => text(r).includes(term));
      return { tenant: t.id, tenant_name: t.name, count: hits.length, returned: hits.length, items: hits };
    }),
  );
  return { query: q, resource, results: results.filter((r) => r.count > 0), errors: [] };
}

function filterAudit(page: Row, params: URLSearchParams): Row {
  let items = (page.items ?? []) as Row[];
  for (const f of ["tenant_id", "category", "operator", "result"]) {
    const v = params.get(f);
    if (v) items = items.filter((i) => String(i[f] ?? "") === v);
  }
  const action = params.get("action");
  if (action) items = items.filter((i) => String(i.action).includes(action));
  const target = params.get("target");
  if (target) items = items.filter((i) => String(i.target ?? "").includes(target));
  if (params.get("include_reads") !== "true") items = items.filter((i) => i.kind !== "consulta");
  if (params.get("include_dry_run") !== "true") items = items.filter((i) => !i.dry_run);
  const before = params.get("before_id");
  if (before) items = items.filter((i) => Number(i.id) < Number(before));
  const limit = Number(params.get("limit") ?? 100);
  const pageItems = items.slice(0, limit);
  return {
    items: pageItems,
    next_before_id: items.length > limit ? pageItems[pageItems.length - 1].id : null,
  };
}

async function handle(method: string, path: string, params: URLSearchParams): Promise<Response> {
  if (path === "/auth/refresh") return json({ access_token: "demo", token_type: "bearer" });
  if (path.startsWith("/auth/logout")) return json({ ok: true });
  if (method !== "GET") return json({ detail: READ_ONLY }, 403);

  if (path.startsWith("/search/")) {
    return json(await crossTenantSearch(path.slice("/search/".length), params.get("q") ?? ""));
  }

  const tenant = /^\/tenants\/([^/]+)/.exec(path)?.[1];
  const store = await load(tenant ?? "global");

  if (path === "/audit") return json(filterAudit((store["/audit"] ?? {}) as Row, params));
  if (tenant && path === `/tenants/${tenant}/accounts`) {
    return json(paginate((store[path] ?? {}) as Row, params, (a) => accountMatches(a, params)));
  }
  if (tenant && /\/(safes|discovered-accounts)$/.test(path) && store[path]) {
    return json(paginate(store[path] as Row, params));
  }

  // `refresh=true` (botón "actualizar") devuelve lo mismo que sin el parámetro
  params.delete("refresh");
  const hit = store[storeKey(path, params)] ?? store[path];
  return hit === undefined ? json({ detail: NOT_AVAILABLE }, 404) : json(hit);
}

/** Reemplaza `window.fetch` para las URLs `/api/...`; el resto pasa directo. */
export function installMockApi(): void {
  const realFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const url = new URL(raw, location.origin);
    if (url.origin !== location.origin || !url.pathname.startsWith("/api/")) {
      return realFetch(input, init);
    }
    const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
    // latencia mínima, para que los estados de carga se vean como en la app real
    await new Promise((r) => setTimeout(r, 120));
    return handle(method, url.pathname.slice("/api".length), url.searchParams);
  };
}
