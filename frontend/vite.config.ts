import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev: `npm run dev` sirve en :5173 y proxea /api al backend. Por defecto
// asume un túnel SSH al server (ssh -L 8000:127.0.0.1:8000 ...); overridable
// con VITE_API_TARGET.
export default defineConfig({
  // ruta base: "/" en el despliegue real; "/pam-web-console/" para la demo
  // estática en GitHub Pages (ver .github/workflows/pages.yml)
  base: process.env.VITE_BASE || "/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  // app interna para pocos operadores en LAN; el tamaño del bundle no es
  // crítico. Se sube el umbral del warning para no ensuciar el log de CI.
  build: { chunkSizeWarningLimit: 700, target: "es2022" },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET || "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
