import { useState } from "react";
import { useSearchParams } from "react-router-dom";

/** Tenant seleccionado, como query param `?tenant=`. Mismo patrón en todas
 *  las páginas con selector de tenant — al navegar entre ellas conservando
 *  el query string (ver `Sidebar` en `App.tsx`), el tenant elegido persiste
 *  sin tener que volver a elegirlo. */
export function useTenantParam(): [string | null, (v: string) => void] {
  const [params, setParams] = useSearchParams();
  const [tenant, setTenantState] = useState<string | null>(params.get("tenant"));

  function setTenant(v: string) {
    setTenantState(v);
    setParams(
      (p) => {
        p.set("tenant", v);
        return p;
      },
      { replace: true },
    );
  }

  return [tenant, setTenant];
}
