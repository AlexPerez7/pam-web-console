import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, clearToken, setToken, tryRefresh } from "./api";
import type { Operator } from "./types";

type LogoutScope = "all" | "current";

interface AuthState {
  operator: Operator | null;
  loading: boolean;
  login: (accessToken: string, operator: Operator) => void;
  /** `all` (default): cierra TODAS las sesiones del operador. `current`: sólo
   *  la de este navegador (cierre por inactividad). `reason` se muestra en
   *  la pantalla de login. */
  logout: (scope?: LogoutScope, reason?: string) => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthCtx = createContext<AuthState>(null as unknown as AuthState);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [operator, setOperator] = useState<Operator | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // el access token vive sólo en memoria: al cargar la página se pide uno
    // nuevo con la cookie HttpOnly de refresh (si hay sesión).
    tryRefresh()
      .then((ok) => (ok ? api<Operator>("/auth/me").then(setOperator) : undefined))
      .catch(() => clearToken())
      .finally(() => setLoading(false));
  }, []);

  const value: AuthState = {
    operator,
    loading,
    login: (accessToken, op) => {
      setToken(accessToken);
      setOperator(op);
    },
    logout: async (scope = "all", reason) => {
      // best-effort: invalida la sesión en el server (y borra la cookie de
      // refresh) antes de limpiar el estado local; si falla, igual desloguea.
      try {
        await api(`/auth/logout?scope=${scope}`, { method: "POST" });
      } catch {
        /* ya se desloguea localmente abajo */
      }
      clearToken();
      setOperator(null);
      location.assign(reason ? `/login?motivo=${encodeURIComponent(reason)}` : "/login");
    },
    refresh: async () => {
      const op = await api<Operator>("/auth/me");
      setOperator(op);
    },
  };
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

// provider y hook colocados a propósito (un solo consumidor del contexto)
// eslint-disable-next-line react-refresh/only-export-components
export const useAuth = () => useContext(AuthCtx);
