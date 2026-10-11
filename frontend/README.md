# frontend — SPA PAM Web Console

React 19 + TypeScript + Vite + **Tailwind CSS v4** + **shadcn/ui** (primitivas
Radix) + **lucide-react** + **TanStack Query**.

## Design system

Los tokens del design system (navy `#002142` / sky `#14baed` / acentos de
business-line / Noto Sans) viven en `src/index.css` como tema de Tailwind y
variables semánticas de shadcn (primary = navy, ring = sky). Ver
`docs/specs/done/migracion-tailwind.md` y `docs/specs/done/rediseno-ui.md`.

- `src/components/ui/*` — primitivas shadcn (código propio, estilo new-york):
  button, card, badge, input, label, select, tabs, table, sheet, dialog,
  dropdown-menu, alert, skeleton.
- `src/lib/utils.ts` — `cn()` (clsx + tailwind-merge).
- Alias `@/` → `src/`.

## Estado (Fase 1)

- `src/App.tsx` — shell: sidebar navy con el isotipo, topbar, menú de usuario,
  rutas protegidas.
- `src/pages/Login.tsx` — login en 2 pasos (credenciales → QR/TOTP).
- `src/pages/Inventory.tsx` — selector de tenant (sincronizado con `?tenant=`)
  + tabs (Cuentas / Safes / Plataformas / Usuarios) con `DataTable` (filtro +
  orden) + `DetailDrawer` (Sheet) al clickear una fila.
- `src/pages/Search.tsx` — búsqueda cross-tenant de cuentas, agrupada por tenant.
- `src/pages/Dashboard.tsx` — KPIs + gráficos de barras (`BarList` / `MiniBars`).
- `src/pages/Account.tsx` — "Mi cuenta" + cambio de contraseña.
- `src/pages/Operators.tsx` — gestión de operadores (solo `platform_admin`).

## Correr

Requiere Node 20+.

```bash
npm ci
npx tsc --noEmit          # chequeo de tipos
npm run build             # tsc + vite build (lo que corre el contenedor web)
npm run dev               # http://localhost:5173, proxy /api -> localhost:8000
```

**Local dev**: `npm run dev` necesita el backend accesible — túnel SSH al
server (`ssh -L 8000:127.0.0.1:8000 ...`) o `VITE_API_TARGET=http://otro:8000
npm run dev`.

## Pendiente (Fase 1)

Virtualización + orden + paginación server-side de las grillas grandes, más
recursos (Safe members, Groups, Sesiones PSM, Componentes), dashboard v3
(sesiones concurrentes), pase de pulido de diseño.
