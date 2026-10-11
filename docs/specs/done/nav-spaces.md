# nav-spaces — Reorganizar el nav de PAM Web Console en "Espacios"

- **Estado:** hecha. Probada en el server por Alex (2026-09-15).
- **Roadmap:** transversal (UX/IA de todo el frontend) — motivado por la
  sección Identity, pero no es parte de `identity-users.md`.
- **Fecha:** 2026-09-15
- **Base:** `docs/specs/done/rediseno-ui.md` (shell/sidebar navy
  actual), `docs/specs/identity-users.md` (spec hermana que agrega la
  sección Identity — pensada para vivir DENTRO del espacio nuevo que define
  esta spec, no como un ítem más del sidebar plano).

## Problema

El nav de PAM Web Console (`App.tsx` → `NAV`) es una lista plana de 5-8 ítems
(Inventario/Buscar/Dashboard/Troubleshooting/Reportes/Backups/Tenants/
Operadores) que crece con cada fase. El PVWA nuevo (rediseñado — ver captura del 2026-09-15) resuelve esto agrupando
todo en **Spaces** (Access / Manage / Detect and Respond / Audit and
Reports / Setup), cada uno con sus propios "Services". Alex quiere ese
mismo patrón en PAM Web Console — en particular, que **Identity** sea su propio
espacio (no un ítem más al final del sidebar), igual que en el PVWA.

## Alcance

**Entra:**
- Reagrupar el sidebar actual en **espacios** con un selector arriba (mismo
  lugar donde hoy está el logo/nombre "PAM Web Console").
- Un espacio nuevo **Identity** — hoy solo con la sección de
  `identity-users.md` (ver/bloquear usuarios); crece a futuro con roles si
  se retoma esa idea.
- Migrar TODOS los ítems existentes a algún espacio (ninguno queda huérfano
  fuera del nuevo esquema).

**Fuera:**
- Un panel de "Services" tipo grilla (como el recorte de la derecha en la
  captura del PVWA) — PAM Web Console no tiene tantas superficies como para
  justificarlo; el selector de espacio alcanza con un dropdown simple.
- Cambiar qué puede ver cada rol (RBAC) — los Espacios son sólo
  reorganización visual, el gating por rol/feature-flag de cada página no
  cambia.
- Rediseñar el contenido de cada página — sólo se mueve dónde vive en el
  nav.

## Propuesta de espacios

Mapeo de lo que existe hoy + lo que se viene, a espacios (nombres en
español, consistente con el resto de la UI — el PVWA los muestra en inglés
porque es un producto global, PAM Web Console no):

| Espacio | Contenido | Quién lo ve |
|---|---|---|
| **Operación** (default, el que abre hoy `/`) | Cuentas, Safes, Plataformas, Sesiones, Componentes (cada uno su propia ruta — ya no un "Inventario" contenedor), Buscar (cross-tenant), Dashboard, Troubleshooting | `viewer+` |
| **Identity** (nuevo) | **Sólo lo que sale de la API de Identity/SSO** — nada del Vault: Usuarios (ver estado, bloquear/desbloquear, `identity-users.md`) + Roles (lista de sólo lectura) | `viewer+` (leer) / `operator+` (bloquear) |
| **Reportes** | Reportes (catálogo + backup cifrado de credenciales) | `viewer+` |
| **Administración** | Backups (cuentas/plataformas borradas, `tenant_admin+`), Tenants (feature-flags, `platform_admin`), Operadores (`platform_admin`) | según cada ítem, igual que hoy |

4 espacios. "Detect and Respond" y "Setup" del PVWA no tienen equivalente
real en PAM Web Console todavía — no se inventan espacios vacíos.

**Extra a pedido de Alex (2026-09-15, revisado tres veces el mismo día):**
primer intento: los recursos (antes tabs horizontales de un `Inventory.tsx`
— Cuentas/Safes/Plataformas/...) pasaron a un sub-nav vertical **dentro**
de la página. Alex corrigió: quería los recursos **directamente en la
barra navy principal**, al mismo nivel que Buscar/Dashboard/
Troubleshooting — igual al "Accounts" del PVWA, que es un ítem más del
sidebar, no una pestaña dentro de otra página. Se deshizo `Inventory.tsx`
y se separó en 5 páginas propias (`pages/Accounts.tsx`, `Safes.tsx`,
`Platforms.tsx`, `Sessions.tsx`, `Components.tsx`), cada una con su propia
ruta (`/`, `/safes`, `/platforms`, `/sessions`, `/components`) y su ítem
en el espacio Operación. Mismo pedido aplicado después a Usuarios/Grupos
del Vault: se separaron en dos páginas/rutas propias (`pages/Users.tsx`
→ `/users`, `pages/Groups.tsx` → `/groups`), sin sub-nav interno.
**`components/SideTabNav.tsx` quedó sin ningún uso y se borró** — no hay,
por ahora, ninguna página con más de un recurso relacionado que lo
necesite; si aparece un caso así más adelante, se puede volver a traer del
historial de git en vez de reinventarlo.

**Cuarta corrección el mismo día — Identity es SOLO lo que sale de la API
de Identity/SSO.** Usuarios/Grupos del Vault habían quedado (por error)
dentro del espacio Identity — Alex corrigió: "ahí no vemos usuarios o
grupos del vault" (refiriéndose al PVWA, donde "Identity Administration"
es un servicio separado de "Users and Groups" del Vault, tal cual ya
estaba anotado en `docs/PLAN.md` desde el 2026-09-14). Se movieron
Usuarios (`/users`) y Grupos (`/groups`) al espacio Operación, junto a
Cuentas/Safes/Plataformas. Identity quedó con dos ítems, ambos vía la API
de Identity: Usuarios (`/sso-users`, de `identity-users.md` — el label
volvió a ser simplemente "Usuarios", sin el sufijo "(Identity)": ya no
hace falta desambiguar de nada dentro de este espacio) y **Roles**
(`/identity-roles`, nuevo — lista de solo lectura, `nombre`/
`descripción`, vía Redrock `SELECT ID, Name, Description FROM Role`, sin
miembros ni permisos administrativos, fuera de alcance a propósito).
`pages/Identity.tsx` (que mostraba Usuarios del Vault, nombre ya
confuso) se renombró a `pages/Users.tsx`.

**Quinta vuelta el mismo día — Usuarios/Grupos del Vault, fuera del nav
por completo.** Alex: "quita los usuarios y grupos de operación, solo
necesitamos lo de identity". Alcance confirmado explícitamente (para no
repetir el patrón de sobre-alcance de las vueltas anteriores): **sólo
nav** — se sacaron los dos ítems de `Operación` en `App.tsx`, pero
`pages/Users.tsx`/`pages/Groups.tsx`, sus rutas (`/users`, `/groups`) y el
CRUD del backend (`vault-users-groups-crud.md`, ya probado) **quedan
intactos**, sólo dejan de ser accesibles desde el sidebar (siguen
alcanzables por URL directa o desde "Ver en inventario" de Búsqueda). Si
hace falta reactivarlos, es agregar 2 líneas de vuelta al array `items` de
Operación — no hay que reconstruir nada.

**Compartir el tenant seleccionado entre rutas:** antes de separar en 5
páginas, el tenant elegido vivía en el estado de una sola página. Separado
en rutas distintas, cambiar de recurso perdía la selección. Se resolvió
con `Sidebar`/`Shell` (`App.tsx`) navegando con
`nav({ pathname: it.to, search: loc.search })` en vez de `nav(it.to)` —
conserva el query string actual (incluido `?tenant=`) al cambiar de
página. `components/TenantScope.tsx` (nuevo) centraliza el título +
selector de tenant + estados vacíos que antes estaban duplicados en cada
página (usa `lib/useTenantParam.ts`, nuevo). `components/QueryState.tsx`
(loading/error de una query) y `lib/gridHelpers.tsx`
(`useServerGrid`/`boolBadge`) también se extrajeron para no duplicar
entre las 5 páginas + Identity.

**Efecto secundario corregido:** `pages/Search.tsx` tenía un link fijo
"Ver en inventario" → `/` sin importar qué recurso se buscó (Safes,
Plataformas, Usuarios, Grupos). Con `/` ahora exclusivo de Cuentas, ese
link quedaba roto para el resto — se agregó `RESOURCE_PATH` (mapea cada
`SearchResource` a su ruta: `users`/`groups` → `/identity`) y el link usa
la ruta correcta según el recurso buscado.

## Diseño técnico

### Estructura de datos (`App.tsx`)

Reemplazar el `NAV` plano por espacios con ítems:

```ts
type NavItem = { to: string; label: string };
type Space = { id: string; label: string; items: NavItem[] };

const SPACES: Space[] = [
  { id: "operacion", label: "Operación", items: [
    { to: "/", label: "Inventario" },
    { to: "/search", label: "Buscar" },
    { to: "/dashboard", label: "Dashboard" },
    { to: "/troubleshooting", label: "Troubleshooting" },
  ]},
  { id: "identity", label: "Identity", items: [
    { to: "/identity", label: "Usuarios" },
  ]},
  { id: "reportes", label: "Reportes", items: [
    { to: "/reports", label: "Reportes" },
  ]},
  { id: "administracion", label: "Administración", items: [
    // + Backups / Tenants / Operadores, condicionales como hoy
  ]},
];
```

`useNavItems()` se reemplaza por `useSpaces()` — misma lógica de gating
(`useAnyTenantAdmin`, `operator?.role === "platform_admin"`) pero devuelve
espacios con sus ítems filtrados, no una lista plana.

### Espacio activo

- Se deriva de la URL actual: qué espacio contiene un `item.to` que matchea
  `loc.pathname` (fallback: "Operación" si no matchea ninguno — ej. `/account`,
  que no pertenece a ningún espacio, es una página "fuera" del esquema de
  espacios, igual que hoy no está en `NAV`).
- **No se persiste** el espacio elegido en localStorage/estado — siempre se
  deriva de la ruta, para que no quede desincronizado si se navega por URL
  directa o al volver atrás.

### Sidebar

- Donde hoy está el logo + "PAM Web Console / PAM Web Console"
  (`App.tsx:106-112`), un selector de espacio: mismo bloque visual pero
  clickeable (`DropdownMenu`, mismo primitivo que ya usa el menú de
  usuario) — muestra el espacio activo + chevron, al abrir lista los 4
  espacios.
- Debajo, la lista de ítems **del espacio activo únicamente** (antes eran
  todos los ítems juntos) — mismo estilo de botón activo/inactivo que hoy.
- Menú mobile (`DropdownMenuContent` del ícono `Menu` en el header,
  `App.tsx:150-156`): agrupar por espacio con un separador
  (`DropdownMenuSeparator`) entre cada uno, en vez de una lista plana.

### Rutas

- `/identity` nueva (de `identity-users.md`) — la `Page` de esa spec cuelga
  del espacio Identity en vez de agregarse a `NAV`.
- El resto de las rutas (`App.tsx` `<Routes>`) **no cambia** — los espacios
  son una capa de presentación sobre las rutas existentes, no una
  reestructuración de URLs. `/backups`, `/tenants`, `/operators` siguen
  siendo las mismas rutas, sólo cambia bajo qué grupo aparecen en el
  sidebar.

## Requisitos / escenarios

**R1 — cambiar de espacio:** Dado un operador en "Operación" viendo
Inventario, cuando abre el selector de espacio y elige "Reportes", entonces
el sidebar muestra sólo los ítems de Reportes y navega a `/reports`
(primer ítem del espacio).

**R2 — espacio activo por URL directa:** Dado un operador que entra
directo a `/troubleshooting` (bookmark), entonces el selector muestra
"Operación" activo (porque Troubleshooting vive ahí) sin que el operador
haya tocado el selector.

**R3 — gating no cambia:** Dado un operador `viewer` (no `platform_admin`),
el espacio "Administración" no muestra "Tenants"/"Operadores" (mismo
comportamiento que hoy), y si tiene `tenant_admin+` en algún tenant sí ve
"Backups" ahí — idéntico gating, sólo cambia la agrupación visual.

**R4 — mobile:** el dropdown del ícono `Menu` en mobile muestra todos los
espacios con sus ítems agrupados (no hace falta el selector ahí, ya está
todo junto con separadores).

## Tareas

- [x] `App.tsx`: `Space[]` (reemplaza `NAV`), `useSpaces()` (reemplaza
      `useNavItems()`), `useActiveSpace()` (espacio activo por ruta).
- [x] `Sidebar`: selector de espacio (`DropdownMenu`, con ícono) + lista de
      ítems del espacio activo.
- [x] `Shell` (menú mobile): agrupar por espacio con separador + encabezado
      (ícono + label) por grupo.
- [x] Icono por espacio (`LayoutGrid`/`FileBarChart2`/`Settings2`).
- [x] `npm run lint` + `npx tsc --noEmit` + `npm run build` verdes.
- [ ] Ruta `/identity` (de `identity-users.md`) — pendiente, se suma cuando
      se implemente esa spec.
- [ ] Verificar visualmente en el browser (bloqueado esta sesión: la
      extensión Claude en Chrome no conectó) — Alex confirma por el túnel
      SSH o reintentamos.
- [ ] Verificar en los 4 roles (viewer/operator/approver/tenant_admin/
      platform_admin) que el gating por espacio coincide con el gating
      plano de antes (ningún ítem aparece/desaparece de más).
- [ ] `CLAUDE.md` (sección `src/App.tsx` del árbol de arquitectura) —
      pendiente hasta cerrar la spec.
- [ ] Probar en el server / mover a `docs/specs/done/`.

## Decisiones abiertas

1. **¿"Identity" en español o se deja en inglés como en el PVWA?** El resto
   de PAM Web Console está en español neutro (`CLAUDE.md` § Convenciones). Propongo
   "Identity" tal cual igual — es un nombre propio del servicio de
   CyberArk/Palo Alto (como "Privilege Cloud"), no una palabra a traducir;
   pero el resto de los espacios si van en español ("Operación",
   "Reportes", "Administración"). Confirmar con Alex.
2. ~~¿Vale la pena un ícono por espacio?~~ **Resuelto (2026-09-15):** sí —
   `LayoutGrid` (Operación) / `FileBarChart2` (Reportes) / `Settings2`
   (Administración), `lucide-react` (ya es dependencia). Se ven en el
   trigger del selector, en cada ítem del dropdown de espacios y como
   encabezado de grupo en el menú mobile.
