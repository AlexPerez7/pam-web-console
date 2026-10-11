# rediseno-ui

- **Estado:** implementada — pendiente de probar en el server
- **Roadmap:** Fase 1, ítem "tema/branding"
- **Fecha:** 2026-09-09

## Problema

El frontend usaba el tema por defecto de Mantine (azul genérico, fuente del
sistema). el MSP tiene un design system propio (claude.ai/design, proyecto
"design system propio") y una maqueta del dashboard
(`PAM Web Console Dashboard - el MSP.dc.html`).

## Alcance

**Entra:**
- Tokens del MSP (colores, tipografía Noto Sans, radios, sombras,
  espaciado) como CSS + tema de Mantine — aplica a **toda la app**.
- Shell: barra lateral navy con el isotipo, navegación con estado activo
  sky, topbar blanca.
- Dashboard reestilado según la maqueta (KPIs, tarjetas de gráficos con
  barras `.bar-track`/`.bar-fill`, salud de componentes con badges del DS).

**Fuera (polish posterior):**
- Mover el título de cada página a la topbar (hoy cada página lo renderiza
  en su contenido).
- Reestilado fino de Inventario / Búsqueda / Operadores / Login (heredan el
  tema y quedan consistentes, pero no replican una maqueta).
- Iconografía (el DS sugiere Lucide, sin confirmar).

## Diseño técnico

- `frontend/src/styles/brand.css` — `@font-face` de Noto Sans (Google
  Fonts) + las custom properties de `tokens/{colors,typography,spacing}.css`
  del DS + las clases `.bar-track` / `.bar-fill` de la maqueta.
- `frontend/src/theme.ts` — `createTheme` de Mantine: escalas `navy` y `sky`
  derivadas de `#002142` / `#14baed`, `primaryColor: navy`, `fontFamily` Noto
  Sans, `defaultRadius: md`, `Card` con `withBorder + radius lg + shadow xs`
  por defecto.
- `frontend/src/main.tsx` — importa el CSS, pasa el tema,
  `forceColorScheme="light"` (el DS es light-only).
- `frontend/src/App.tsx` — `AppShell.Navbar` navy con el isotipo y
  `NavItem` propio (activo = fondo sky, texto navy).
- `frontend/src/assets/brand/isotipo-blanco.png` — del DS.
- `frontend/src/pages/Dashboard.tsx` — layout y colores de la maqueta;
  `DsBadge` replica el Badge del DS.
- `components/BarList.tsx` / `MiniBars.tsx` — usan las clases y los tokens;
  los colores de acento de business-line se usan solo en gráficos.

Los colores de business-line (`--line-*`) son identificadores de servicio,
no colores de UI general — se usan en gráficos, no en botones/fondos.

## Tareas

- [x] Tokens CSS + Noto Sans.
- [x] Tema de Mantine.
- [x] Shell (sidebar navy + topbar).
- [x] Dashboard según la maqueta.
- [x] BarList / MiniBars con tokens.
- [ ] Probar en el server (no hay `tsc` local); ajustar y mover a `done/`.
- [ ] (posterior) reestilar el resto de las páginas.
