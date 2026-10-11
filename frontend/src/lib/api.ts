// Cliente HTTP mínimo. Base /api (nginx lo proxea al backend, o el dev
// server de Vite). Ver docs/specs/session-hardening.md:
//  - el access token (corto, 15 min) vive SÓLO en memoria de esta pestaña,
//    nunca en localStorage -- un XSS no puede leerlo de ahí;
//  - el refresh token viaja en una cookie HttpOnly que JavaScript no ve;
//    el navegador la manda sola a /api/auth/refresh.
// Ante un 401 se renueva el access token (una vez) y se reintenta; si no
// hay sesión válida, se vuelve al login.

let accessToken: string | null = null;

export const getToken = (): string | null => accessToken;

export function setToken(access: string | null) {
  accessToken = access;
}

export function clearToken() {
  accessToken = null;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** Convierte el `detail` de un error a texto. Las `HTTPException` del backend
 *  lo mandan como string, pero los errores de validación de FastAPI (422)
 *  lo mandan como arreglo de `{loc, msg}` — pasarlo tal cual a `Error`
 *  mostraba "[object Object]". */
function detailMessage(detail: unknown, fallback: string): string {
  if (detail == null || detail === "") return fallback;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      if (typeof item === "string") return item;
      if (item && typeof item === "object") {
        const { loc, msg } = item as { loc?: unknown[]; msg?: string };
        const campo = Array.isArray(loc)
          ? loc.filter((p) => p !== "body" && p !== "query" && p !== "path").join(".")
          : "";
        if (msg) return campo ? `${campo}: ${msg}` : msg;
      }
      return JSON.stringify(item);
    });
    return parts.join("; ") || fallback;
  }
  if (typeof detail === "object") {
    const { message, msg } = detail as { message?: string; msg?: string };
    return message ?? msg ?? JSON.stringify(detail);
  }
  return String(detail);
}

let refreshing: Promise<boolean> | null = null;

/** Pide un access token nuevo con la cookie de refresh. Deduplicado: varias
 *  requests que reciben 401 a la vez comparten una sola renovación. */
export function tryRefresh(): Promise<boolean> {
  if (!refreshing) {
    refreshing = fetch("/api/auth/refresh", {
      method: "POST",
      credentials: "same-origin",
      // header anti-CSRF que exige el backend (otro sitio no puede mandarlo
      // sin un preflight CORS que la API no autoriza)
      headers: { "X-PAM-Refresh": "1" },
    })
      .then(async (r) => {
        if (!r.ok) return false;
        const d = await r.json();
        setToken(d.access_token);
        return true;
      })
      .catch(() => false)
      .finally(() => {
        refreshing = null;
      });
  }
  return refreshing;
}

function goToLogin() {
  clearToken();
  const login = `${import.meta.env.BASE_URL}login`;
  if (location.pathname !== login) location.assign(login);
}

/** Ejecuta `send` con el token actual; ante un 401 renueva y reintenta una
 *  vez. Común a JSON, multipart y descargas. */
async function sendWithAuth(
  send: (token: string | null) => Promise<Response>,
  auth = true,
): Promise<Response> {
  let res = await send(auth ? getToken() : null);
  if (res.status === 401 && auth) {
    if (await tryRefresh()) res = await send(getToken());
    if (res.status === 401) goToLogin();
  }
  return res;
}

function doFetch(
  path: string,
  opts: { method?: string; body?: unknown; headers?: Record<string, string> },
  token: string | null,
): Promise<Response> {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    ...opts.headers,
  };
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetch(`/api${path}`, {
    method: opts.method ?? "GET",
    headers,
    credentials: "same-origin",
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
}

async function parseJson<T>(res: Response): Promise<T> {
  const text = await res.text();
  let data: { detail?: unknown } | null = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    // Respuesta que no es JSON: típicamente la página HTML de nginx
    // (502/503/504) mientras el contenedor `api` está caído o reiniciando.
    // Se reporta con su status real, no como "sin conexión".
    if (!res.ok) throw new ApiError(res.status, res.statusText || "error del servidor");
    throw new ApiError(res.status, "respuesta inválida del servidor");
  }
  if (!res.ok) {
    throw new ApiError(res.status, detailMessage(data?.detail, res.statusText));
  }
  return data as T;
}

export async function api<T = unknown>(
  path: string,
  opts: {
    method?: string;
    body?: unknown;
    auth?: boolean;
    /** headers extra (ej. `X-PAM-Approval-Id` al aprobar una solicitud) */
    headers?: Record<string, string>;
  } = {},
): Promise<T> {
  const res = await sendWithAuth((t) => doFetch(path, opts, t), opts.auth !== false);
  return parseJson<T>(res);
}

/** POST multipart/form-data (ej. subir un archivo) que devuelve JSON.
 *  Mismo manejo de auth/refresh que `api()`, sin el content-type fijo en
 *  JSON (el browser arma el boundary de multipart/form-data solo). */
export async function apiForm<T = unknown>(path: string, form: FormData): Promise<T> {
  const res = await sendWithAuth((token) => {
    const headers: Record<string, string> = {};
    if (token) headers.Authorization = `Bearer ${token}`;
    return fetch(`/api${path}`, { method: "POST", headers, body: form });
  });
  return parseJson<T>(res);
}

function filenameFrom(res: Response, fallback: string): string {
  const cd = res.headers.get("content-disposition") ?? "";
  const m = /filename="?([^"]+)"?/.exec(cd);
  return m ? m[1] : fallback;
}

/** Descarga un archivo del backend (adjunta el JWT; un reintento con refresh).
 *  Por defecto GET sin body; algunos exports (ej. credentials-backup) van por
 *  POST con un body (razón obligatoria). `headers` (opcional) pide que se
 *  devuelvan valores puntuales de la respuesta además del blob — ej. el
 *  backup de credenciales viaja con la contraseña del zip en
 *  `x-backup-password`, que nunca se guarda en PAM Web Console. */
export async function apiBlob(
  path: string,
  opts: { method?: string; body?: unknown; headers?: string[] } = {},
): Promise<{ blob: Blob; filename: string; headers: Record<string, string> }> {
  // `opts.headers` acá son headers de la RESPUESTA a devolver, no de la request
  const res = await sendWithAuth((t) =>
    doFetch(path, { method: opts.method, body: opts.body }, t),
  );
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const d = JSON.parse(await res.text());
      detail = detailMessage(d?.detail, detail);
    } catch {
      /* respuesta no-JSON */
    }
    throw new ApiError(res.status, detail);
  }
  const headers: Record<string, string> = {};
  for (const name of opts.headers ?? []) {
    const v = res.headers.get(name);
    if (v != null) headers[name] = v;
  }
  return {
    blob: await res.blob(),
    filename: filenameFrom(res, path.split("/").pop() || "export"),
    headers,
  };
}
