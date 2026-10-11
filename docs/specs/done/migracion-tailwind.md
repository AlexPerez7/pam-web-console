# migracion-tailwind

- **Estado:** hecha (build local verde; pendiente de probar en el server)
- **Roadmap:** Fase 1, ítem "tema/branding" — cambio de UI kit
- **Fecha:** 2026-09-09

## Problema

El frontend usa Mantine 7. Se decide migrar a **Tailwind CSS v4 + shadcn/ui**
(componentes que uno posee, sobre primitivas Radix) para tener más control de
diseño y alinear con el stack más usado hoy. El design system del MSP
(tokens navy/sky/business-line + Noto Sans) se mantiene, ahora expresado como
tema de Tailwind.

## Alcance

**Entra:** reemplazo completo del UI kit.
- Tooling: `tailwindcss` + `@tailwindcss/vite`, shadcn/ui (CVA, clsx,
  tailwind-merge, `tw-animate-css`, `lucide-react`, primitivas `@radix-ui/*`).
- Sacar `@mantine/core` y `@mantine/hooks`.
- Tokens del MSP como `@theme` en `src/index.css`.
- Primitivas shadcn necesarias: button, card, input, label, select, badge,
  tabs, table, sheet (panel lateral), dialog, dropdown-menu, alert, skeleton,
  separator.
- Reescribir shell y las 6 páginas.

**No cambia:** `lib/api.ts`, `lib/auth.tsx`, `lib/types.ts`, `lib/format.ts`,
react-router, TanStack Query, el backend.

## Entrega por fases

1. **Tooling + shell + Dashboard** (esta iteración). `npm run build` verde.
   Verificación visual antes de seguir.
2. Login + Account.
3. Inventory (tabs + DataTable + DetailDrawer).
4. Search + Operators.
5. Borrar restos de Mantine, pase de pulido, mover a `done/`.

## Diseño técnico

- `src/index.css` — `@import "tailwindcss"; @import "tw-animate-css";` +
  `@theme { --color-navy-*: …; --font-sans: "Noto Sans", … }` con los tokens
  del MSP; `@font-face` de Noto Sans; variables shadcn (`--background`,
  `--foreground`, `--primary`, `--border`, `--radius`, …) mapeadas a los
  tokens del design system (primary = navy, ring = sky).
- `src/lib/utils.ts` — `cn()` (clsx + tailwind-merge).
- `src/components/ui/*` — primitivas shadcn (estilo "new-york").
- `src/components/layout/` — `Sidebar` (navy, isotipo, nav activa sky),
  `Topbar`.
- `vite.config.ts` — plugin `@tailwindcss/vite`; alias `@` → `src`.
- `tsconfig.json` — `paths` para `@/*`.
- Los gráficos siguen siendo `BarList` / `MiniBars` (SVG/CSS propio) por
  ahora; `@mantine/charts` quedó descartado con Mantine. Evaluar `recharts`
  directo en otra spec si hace falta.

## Tareas

- [x] Fase 1: tooling (Tailwind v4 + shadcn/Radix), `index.css` con tokens
      el MSP, `lib/utils`, primitivas ui (button/card/badge/select/
      dropdown-menu/alert/skeleton), shell nuevo, Dashboard, BarList/MiniBars.
      `tsc` + `npm run build` verdes localmente (Node 24). Mantine sigue
      montado para las páginas sin migrar.
- [x] Fase 2: Login + Account.
- [x] Fase 3: Inventory + DataTable + DetailDrawer (Sheet).
- [x] Fase 4: Search (Card por tenant) + Operators (Dialog).
- [x] Fase 5: Mantine desinstalado (`@mantine/core`, `@mantine/hooks`),
      `theme.ts` y `styles/brand.css` borrados, `CLAUDE.md` + `PLAN.md` +
      READMEs actualizados. Bundle: CSS 229→28 KB, JS 556→400 KB.

## Notas de implementación

- Node 24 en la PC de dev → `npx tsc --noEmit` + `npm run build` en cada fase.
- `DetailDrawer` pasó de Mantine Drawer a shadcn `Sheet`; los modales de
  Operators a `Dialog`; los tabs de Inventory a Radix Tabs (contenido de tab
  inactivo desmontado → la query solo corre en el tab activo, como antes).
- Search dejó el accordion: ahora una `Card` por tenant, todas expandidas.
- Gráficos: siguen siendo `BarList` / `MiniBars` (SVG/CSS propio).
