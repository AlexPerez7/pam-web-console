import "./index.css";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "./lib/auth";
import { Toaster } from "./components/ui/toast";

// Tras un deploy, una pestaña ya abierta puede pedir un chunk de página que
// ya no existe (los nombres llevan hash). Vite emite este evento cuando falla
// esa carga: se recarga una vez para traer el index.html nuevo.
window.addEventListener("vite:preloadError", (event) => {
  const KEY = "pam:chunk-reload";
  try {
    if (sessionStorage.getItem(KEY)) return; // evita un bucle de recargas
    sessionStorage.setItem(KEY, "1");
    setTimeout(() => sessionStorage.removeItem(KEY), 10_000);
  } catch {
    /* sin storage: igual se recarga */
  }
  event.preventDefault();
  location.reload();
});

const qc = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 30_000 } },
});

// Demo estática (GitHub Pages, `VITE_DEMO=true`): la API se responde en el
// navegador con datos ficticios antes de montar la app (src/demo/).
if (import.meta.env.VITE_DEMO === "true") {
  const { installMockApi } = await import("./demo/mockApi");
  installMockApi();
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <BrowserRouter basename={import.meta.env.BASE_URL}>
        <AuthProvider>
          <App />
          <Toaster />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
