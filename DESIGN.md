---
name: PAM Web Console — PAM Web Console
description: Consola PAM interna sobria y densa en datos, navy + sky sobre shadcn/Tailwind v4
colors:
  navy-vigilante: "#002142"
  sky-activo: "#14baed"
  neutral-bg: "#f7f9fa"
  neutral-surface: "#ffffff"
  neutral-surface-muted: "#eef1f3"
  neutral-border: "#c3ccd1"
  neutral-text: "#0b1620"
  neutral-muted: "#4a5b68"
  success-identity: "#97b82c"
  danger-cyberdefense: "#d50000"
  warning-consulting: "#deb214"
  line-cloud: "#007ec8"
  line-data: "#c500c5"
  line-transformation: "#007f57"
  line-awareness: "#f75817"
typography:
  display:
    fontFamily: "Noto Sans, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif"
    fontSize: "22px"
    fontWeight: 700
    lineHeight: "normal"
    letterSpacing: "normal"
  body:
    fontFamily: "Noto Sans, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: "20px"
    letterSpacing: "normal"
  label:
    fontFamily: "Noto Sans, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: "16px"
    letterSpacing: "0.025em"
rounded:
  sm: "4px"
  md: "6px"
  lg: "8px"
  xl: "12px"
  pill: "9999px"
components:
  button-primary:
    backgroundColor: "{colors.navy-vigilante}"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "0 16px"
    height: "36px"
  button-primary-hover:
    backgroundColor: "rgba(0, 33, 66, 0.9)"
  button-secondary:
    backgroundColor: "{colors.neutral-surface-muted}"
    textColor: "{colors.neutral-muted}"
    rounded: "{rounded.md}"
    padding: "0 16px"
    height: "36px"
  badge-neutral:
    backgroundColor: "{colors.neutral-surface-muted}"
    textColor: "{colors.neutral-muted}"
    rounded: "{rounded.sm}"
    padding: "2px 8px"
  badge-success:
    backgroundColor: "#eaf4de"
    textColor: "{colors.success-identity}"
    rounded: "{rounded.sm}"
    padding: "2px 8px"
  badge-danger:
    backgroundColor: "#fbe2e2"
    textColor: "{colors.danger-cyberdefense}"
    rounded: "{rounded.sm}"
    padding: "2px 8px"
  input-default:
    backgroundColor: "{colors.neutral-surface}"
    textColor: "{colors.neutral-text}"
    rounded: "{rounded.md}"
    padding: "4px 12px"
    height: "36px"
  card:
    backgroundColor: "{colors.neutral-surface}"
    textColor: "{colors.neutral-text}"
    rounded: "{rounded.lg}"
    padding: "16px"
---

# Design System: PAM Web Console — PAM Web Console

## Overview

**Creative North Star: "La Sala de Control"**

PAM Web Console es la herramienta de trabajo diaria del equipo PAM para operar CyberArk sobre los tenants de sus clientes — cada pantalla puede exponer o gestionar acceso privilegiado real. La Sala de Control no es una metáfora decorativa: es literalmente lo que el producto reemplaza (el PVWA nativo) hecho mejor — más navegable, más denso en información, sin ceder claridad por estética. El navy es la superficie constante (la sala, siempre presente — sidebar, textos primarios); el sky es la única señal de "esto está activo/seleccionado ahora mismo" — se usa con moderación, nunca como color de fondo general. El color fuera de navy/sky/grises se reserva casi por completo para comunicar estado real (compliant vs. no compliant, activo vs. inactivo), nunca decoración.

No hay gradientes, no hay ilustraciones, no hay modo oscuro (el DS es light-only, deliberado). La tipografía es Noto Sans self-hosted, sin looks "premium" ni serif — es una consola operativa, no un producto de consumo.

**Key Characteristics:**
- Sobria y seria: paleta acotada (navy/sky + grises + acentos semánticos puntuales), sin gradientes ni ilustraciones.
- Densa en datos: `text-sm` es el tamaño de texto por defecto en toda la app (no `text-base`); tablas virtualizadas, KPIs compactos, grillas con resize/filtro.
- Precisa y sin fricción: transiciones cortas (150ms), sin rebote/elasticidad, feedback táctil discreto (`scale-[0.97]` al presionar), nunca una animación que demore completar la tarea.
- Light-only, sin excepciones.

## Colors

Paleta acotada a propósito: navy + sky para estructura e interacción, grises neutros para el 90% de la superficie, y un puñado de acentos semánticos que **sólo** aparecen para señalar estado real — nunca como decoración de UI general.

### Primary
- **Navy Vigilante** (#002142): el color de fondo de la barra lateral y el texto primario (`--foreground` deriva de una variante más oscura, `#0b1620`). Es la superficie constante — la "sala" que siempre está ahí. Botones primarios, `CardTitle`, headings.

### Secondary
- **Sky Activo** (#14baed): la única señal de "esto está seleccionado/activo ahora". Fondo del ítem de nav activo (con texto navy encima, no blanco), `focus-visible:ring`, enlaces (`text-sky-600`). Nunca se usa como fondo de superficies grandes.

### Neutral
- **Fondo de página** (#f7f9fa): `--background`, el gris más claro de la escala.
- **Superficie** (#ffffff): tarjetas, popovers, inputs, filas de tabla en reposo.
- **Superficie secundaria** (#eef1f3): botones secundarios, badges neutros, fondos `muted`.
- **Borde** (#c3ccd1): el único color de borde en toda la app (`* { border-color: var(--border) }` global en `@layer base`).
- **Texto primario** (#0b1620): `--foreground`.
- **Texto secundario / muted** (#4a5b68): labels, metadata, texto de apoyo.

### Acentos semánticos (business-line, "de estado" — no de marca)
Heredados de la paleta de business-lines del MSP, pero en PAM Web Console se usan **exclusivamente como señal de estado**, nunca para branding de UI:
- **Verde Identity** (#97b82c): compliant / activo / OK (`ComplianceCard`, badges de estado).
- **Rojo Cyberdefense** (#d50000): no compliant / error / destructivo (mismo color que `--destructive`).
- **Ámbar Consulting** (#deb214): advertencia.
- **Cloud** (#007ec8), **Data** (#c500c5), **Transformation** (#007f57), **Awareness** (#f75817): sin rol semántico fijo — se usan sólo como paleta cíclica determinística para avatares de iniciales (vendors/usuarios de Alero, `AleroBadges.tsx`), nunca como fondo o texto de UI general.

### Named Rules
**La Regla del Acento Único.** Sky es la única señal de "seleccionado/activo" en toda la app. No se introduce un segundo color para el mismo propósito en ningún componente nuevo.

**La Regla del Color con Propósito.** Fuera de navy/sky/grises, ningún color aparece sin comunicar un estado real (compliant/no compliant, activo/inactivo, éxito/error). Si un color no está señalando algo, no debería estar ahí.

## Typography

**Display/Body Font:** Noto Sans (self-hosted, subset latin — sin request a fonts.gstatic.com), con fallback `-apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif`.

**Character:** Una sola familia para todo el sistema — sin serif, sin mono decorativo. Neutral y funcional, pensada para densidad de datos, no para personalidad de marca.

### Hierarchy
- **Display** (700, 22px): título de página/panel de detalle (`AccountDetailPanel`, `AleroVendorDetail`, `KnowledgeBaseArticle`) — el único tamaño grande de la app, usado con moderación.
- **Title** (700, 14px/`text-sm font-bold`): `CardTitle` — encabezado de tarjeta.
- **Body** (400, 14px/`text-sm`): tamaño por defecto de **toda** la app — tablas, formularios, texto de contenido. PAM Web Console nunca usa `text-base` (16px) como default; la densidad de datos prima sobre el "aire" tipográfico.
- **Label** (600, 12px/`text-xs`, uppercase, `tracking-wide`, color `neutral-muted`): eyebrows de sección (`text-xs font-semibold uppercase tracking-wide text-muted-foreground` — patrón idéntico en 13+ lugares del código).
- **Micro** (11px/`text-[11px]`): metadata secundaria — sublabel del espacio activo en el sidebar, contadores "+N más" en `BarList`.
- **KPI Number** (700, 24px/`text-2xl`): exclusivo de las tarjetas de KPI del Dashboard/Troubleshooting — no se usa en ningún otro contexto.

### Named Rules
**La Regla del Cuerpo Chico.** `text-sm` (14px) es el tamaño base de todo el contenido de la app. Subir a `text-base` en una pantalla nueva rompe la densidad esperada.

## Layout

Sin grid propio ni tokens de spacing custom — el proyecto usa la escala de espaciado por defecto de Tailwind (4px) directamente en el markup (`px-3`, `gap-2.5`, `p-4`), sin redefinir `--spacing-*`. Contenedor de página sin `max-width` fijo (la app corre en pantallas de escritorio de operador, no un layout de marketing responsive-first). Sidebar fija de `w-56` (224px) en desktop, colapsa por completo en mobile (`hidden md:flex` — PAM Web Console no tiene un modo mobile real, es una herramienta de escritorio). Las grillas de datos (`DataTable`) son virtualizadas y paginan server-side cuando el volumen lo exige; el resto del layout es flujo vertical simple (`flex flex-col gap-4/6`) dentro de tarjetas.

## Elevation & Depth

Sistema **mayormente plano, sin tonal layering**: las filas de tabla no llevan sombra en absoluto (sólo `border-b`), y el único elemento en reposo con sombra es `Card` (`shadow-sm`, casi imperceptible — más un borde con volumen que una sombra real). La elevación real se reserva por completo para lo que flota sobre el contenido: diálogos y sheets (`shadow-lg`), menús desplegables y popovers (`shadow-md`). La jerarquía es: reposo = plano (con o sin `shadow-sm` según el componente) → overlay = elevado, sin pasos intermedios.

### Shadow Vocabulary
- **Sin sombra**: filas de `Table` — el divisor es `border-b`, no elevación.
- **Reposo con volumen mínimo** (`shadow-sm`): `Card`, el único componente en reposo con sombra.
- **Popover** (`shadow-md`): `DropdownMenuContent`, `SelectContent`.
- **Modal** (`shadow-lg`): `DialogContent`, `SheetContent`.

### Named Rules
**La Regla del Piso Plano.** Nada que esté "en la página" (cards, filas, botones) lleva más que `shadow-sm`. Sombra fuerte es exclusiva de lo que se superpone al contenido (modal/popover/sheet).

## Shapes

Radio base de 8px (`--radius: 0.5rem`), con una escala derivada de 4 pasos (`sm` 4px, `md` 6px, `lg` 8px, `xl` 12px) más un caso pill explícito. Sin bordes decorativos: el único color de borde de toda la app es `neutral-border` (#c3ccd1), aplicado globalmente vía `* { border-color: var(--border) }`.

- **Botones / inputs / popovers**: `rounded-md` (6px).
- **Cards / diálogos / sheets**: `rounded-lg` (8px) — el radio "base" del sistema.
- **Badges / bar-tracks**: `rounded-sm` (4px) — el elemento más chico, el radio más chico.
- **Switch**: pill completo (`rounded-full`) — único componente con esquinas totalmente redondeadas.

### Named Rules
**La Regla del Radio Proporcional al Tamaño.** El radio crece con el tamaño del elemento: badges (chicos) = `sm`, botones/inputs (medianos) = `md`, cards/modales (grandes) = `lg`. Nunca al revés.

## Components

### Buttons
- **Shape:** `rounded-md` (6px), altura fija `h-9` (36px) en el tamaño default.
- **Primary:** fondo Navy Vigilante, texto blanco, `hover:bg-primary/90`.
- **Secondary:** fondo `neutral-surface-muted`, texto `neutral-muted`, `hover:bg-secondary/80`.
- **Outline / Ghost:** transparente o con borde, `hover:bg-accent` (sky-100) + `hover:text-accent-foreground` (navy).
- **Destructive:** fondo Rojo Cyberdefense, texto blanco — reservado a operaciones irreversibles (borrar cuenta/safe).
- **Feedback táctil:** `active:scale-[0.97]` (respetando `motion-safe`), transición 150ms `ease-out`. Sin rebote/elasticidad en ningún estado.

### Badges
- **Shape:** `rounded-sm` (4px), `px-2 py-0.5`, `text-xs font-medium`.
- **Variantes:** `neutral` (gris), `info` (sky-100/navy), `success` (verde Identity), `danger` (rojo Cyberdefense), `warning` (ámbar Consulting) — un color por estado real, nunca decorativo.

### Cards / Containers
- **Corner Style:** `rounded-lg` (8px).
- **Background:** superficie blanca sobre fondo `neutral-bg`.
- **Shadow Strategy:** `shadow-sm` en reposo (ver Elevation).
- **Border:** `border border-border` siempre presente — las cards nunca flotan sin borde.
- **Internal Padding:** header/content en `p-4`.

### Inputs / Fields
- **Style:** borde `neutral-border`, fondo blanco, `rounded-md`, altura `h-9`.
- **Focus:** `ring-2 ring-ring` (sky) + `ring-offset-1`, sin cambio de borde.
- **Disabled:** opacidad 50%, cursor `not-allowed`.

### Navigation
- **Sidebar:** fondo Navy Vigilante fijo, `w-56`, texto blanco/`sky-100`.
- **Item activo:** fondo Sky Activo sólido, texto navy, `font-semibold` — el único lugar donde sky cubre un área grande, deliberado (es literalmente "dónde estás parado ahora").
- **Item inactivo:** texto `sky-100`, `hover:bg-navy-800` (un paso más claro que el fondo).
- **Sin estado mobile real** — la sidebar se oculta por completo bajo `md` (`hidden md:flex`).

### Data Table (componente distintivo)
El componente que más se repite en la app (`DataTable.tsx`, usado en Cuentas/Safes/Plataformas/Usuarios/Audit/Reportes/etc.). Filas separadas sólo por `border-b border-border` — sin zebra striping. `hover:bg-muted/50` en la fila, `bg-muted` sólido en la fila seleccionada. Headers en `text-xs font-semibold text-muted-foreground`, altura fija `h-9`. Virtualizado + resize + orden + filtro por columna, con paginación server-side cuando el volumen lo exige.

### Bar List (componente distintivo)
Barra horizontal sin librería de charting (`BarList.tsx`): track `h-2 rounded-sm bg-muted`, fill `rounded-sm` del color pasado (default Sky Activo), transición de `width` 500ms `ease-out`. Es el único lugar de la app donde una animación dura más de 150-200ms, justificado porque comunica una magnitud real (no es decorativa).

## Do's and Don'ts

### Do:
- **Do** usar `text-sm` como tamaño de texto por defecto; reservar `text-[22px] font-bold` sólo para el título de página/panel.
- **Do** usar Sky Activo exclusivamente para señalar "seleccionado/activo ahora" (nav, focus ring, links) — nunca como fondo decorativo.
- **Do** usar los colores semánticos (verde Identity / rojo Cyberdefense / ámbar Consulting) sólo cuando comunican un estado real y verificable, nunca por preferencia estética.
- **Do** mantener `shadow-sm` (o ninguna sombra) en todo lo que esté "en la página"; subir a `shadow-md`/`lg` sólo en overlays (modal/popover/sheet).
- **Do** usar `rounded-sm`/`md`/`lg` según el tamaño del elemento (badges/botones/cards).

### Don't:
- **Don't** introducir gradientes, ilustraciones decorativas o un segundo modo de color (dark mode) — el DS es light-only y plano, deliberado.
- **Don't** usar los colores de business-line (cloud/data/transformation/awareness) como color de marca o fondo de UI — están reservados a la paleta cíclica de avatares de Alero.
- **Don't** agregar animaciones de más de ~200ms en interacciones comunes (botones, hover, focus) — 150ms `ease-out` es el estándar; la única excepción confirmada es `BarList`, que anima una magnitud real.
- **Don't** usar `rounded-full` fuera del `Switch` — es el único componente pill del sistema.
- **Don't** mezclar el radio de dos escalas distintas en el mismo componente sin una regla documentada (ver "La Regla del Radio Proporcional al Tamaño").
