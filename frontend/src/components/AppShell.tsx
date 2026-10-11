import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArchiveRestore,
  BadgeCheck,
  BookOpen,
  Briefcase,
  Cable,
  Building2,
  Check,
  ChevronRight,
  ChevronsUpDown,
  FileBarChart2,
  Film,
  Fingerprint,
  Globe,
  HeartPulse,
  KeyRound,
  LockKeyhole,
  Layers,
  LayoutDashboard,
  LayoutGrid,
  ListChecks,
  Loader2,
  LogOut,
  Menu,
  Network,
  MonitorPlay,
  MonitorSmartphone,
  PanelLeftClose,
  PanelLeftOpen,
  Radar,
  ScrollText,
  Search,
  Server,
  Settings,
  Settings2,
  ShieldEllipsis,
  ShieldCheck,
  Signal,
  SlidersHorizontal,
  Smartphone,
  UserCheck,
  UserCog,
  UserRound,
  Users,
  Vault,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { Fragment, Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import isotipo from "@/assets/brand/isotipo.svg";
import { IdleGuard } from "@/components/IdleGuard";
import { NotificationsBell } from "@/components/NotificationsBell";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { roleAtLeast, roleLabel } from "@/lib/roles";
import type { Operator, TenantSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

// Shell de la app (sidebar + barra superior). Diseño "La Sala de Control"
// (DESIGN.md): sidebar navy plano, sky sólo para "dónde estoy ahora".
// Espacios (Operación/Identity/...) -> grupos -> ítems. La sidebar se puede
// colapsar a sólo íconos; la preferencia se guarda por navegador.

type NavItem = { to: string; label: string; icon: LucideIcon; badge?: number };
type NavGroup = { title?: string; items: NavItem[] };
type Space = {
  id: string;
  label: string;
  description: string;
  icon: LucideIcon;
  groups: NavGroup[];
};

const COLLAPSED_KEY = "pam:sidebar-collapsed";

/** true si el operador es `tenant_admin+` en al menos un tenant (o
 *  platform_admin global). Gatea Backups/Audit en el nav. */
function useAnyTenantAdmin(): boolean {
  const { operator } = useAuth();
  const q = useQuery({
    queryKey: ["tenants"],
    queryFn: () => api<TenantSummary[]>("/tenants"),
    enabled: !!operator,
  });
  if (operator?.role === "platform_admin") return true;
  return (q.data ?? []).some((t) => roleAtLeast(t.my_role, "tenant_admin"));
}

/** Espacios del nav — agrupan las páginas igual que el PVWA nuevo agrupa en
 *  "Spaces". Puramente visual: el gating real por rol vive en cada página y
 *  en el backend. Un espacio (o grupo) sin ítems visibles no se muestra. */
function useSpaces(): Space[] {
  const { operator } = useAuth();
  const showTenantAdmin = useAnyTenantAdmin();
  const isPlatformAdmin = operator?.role === "platform_admin";
  // solicitudes que ESTE operador puede aprobar (docs/specs/dual-approval.md)
  const pendingApprovals = useQuery({
    queryKey: ["approvals", "count"],
    queryFn: () => api<{ pending: number }>("/approvals/count"),
    enabled: !!operator,
    refetchInterval: 60_000,
  }).data?.pending;

  const spaces: Space[] = [
    {
      id: "operacion",
      label: "Operación",
      description: "Inventario, sesiones y salud de los tenants",
      icon: LayoutGrid,
      groups: [
        {
          items: [
            { to: "/search", label: "Buscar", icon: Search },
            { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
          ],
        },
        {
          title: "Inventario",
          items: [
            { to: "/", label: "Cuentas", icon: KeyRound },
            { to: "/discovery", label: "Descubrimiento", icon: Radar },
            { to: "/safes", label: "Safes", icon: Vault },
            { to: "/platforms", label: "Plataformas", icon: Layers },
          ],
        },
        {
          title: "Sesiones",
          items: [
            { to: "/sessions", label: "Sesiones en vivo", icon: MonitorPlay },
            { to: "/recordings", label: "Grabaciones", icon: Film },
          ],
        },
        {
          title: "Salud y cumplimiento",
          items: [
            { to: "/compliance", label: "Compliance", icon: ShieldCheck },
            { to: "/troubleshooting", label: "Troubleshooting", icon: Wrench },
            { to: "/system-health", label: "System Health", icon: HeartPulse },
            { to: "/cyberark-status", label: "Estado de CyberArk", icon: Signal },
          ],
        },
        {
          title: "Gestión",
          items: [
            { to: "/campaigns", label: "Campañas", icon: ListChecks },
            { to: "/approvals", label: "Aprobaciones", icon: UserCheck, badge: pendingApprovals },
          ],
        },
      ],
    },
    {
      // Sólo lo que sale de la API de Identity (SSO) — nada del Vault.
      id: "identity",
      label: "Identity",
      description: "Usuarios y roles de CyberArk Identity",
      icon: Fingerprint,
      groups: [
        {
          items: [
            { to: "/sso-users", label: "Usuarios", icon: Users },
            { to: "/identity-roles", label: "Roles", icon: BadgeCheck },
          ],
        },
      ],
    },
    {
      // CyberArk Remote Access (Alero) — acceso de terceros, API propia.
      id: "alero",
      label: "Alero",
      description: "Acceso remoto de terceros y proveedores",
      icon: Globe,
      groups: [
        {
          items: [
            { to: "/alero-vendors", label: "Vendors", icon: Briefcase },
            { to: "/alero-users", label: "Usuarios", icon: Smartphone },
            { to: "/alero-roles", label: "Roles", icon: BadgeCheck },
            { to: "/alero-activities", label: "Activities", icon: Activity },
          ],
        },
      ],
    },
    {
      // Secure Infrastructure Access — acceso just-in-time a VMs y bases de
      // datos. Sólo lectura (docs/specs/done/sia-read-only.md).
      id: "sia",
      label: "SIA",
      description: "Acceso just-in-time a VMs y bases de datos",
      icon: ShieldEllipsis,
      groups: [
        {
          items: [
            { to: "/sia/policies", label: "Políticas", icon: ShieldCheck },
            { to: "/sia/sessions", label: "Sesiones", icon: Cable },
            { to: "/sia/strong-accounts", label: "Strong accounts", icon: LockKeyhole },
            { to: "/sia/settings", label: "Configuración", icon: SlidersHorizontal },
          ],
        },
      ],
    },
    {
      // Setup de CyberArk (Connector management) — sólo lectura por ahora.
      id: "setup",
      label: "Setup",
      description: "Conectores y pools de cada tenant",
      icon: Settings,
      groups: [
        {
          title: "Connector management",
          items: [
            { to: "/setup/connectors", label: "Conectores", icon: Server },
            { to: "/setup/connector-pools", label: "Pools", icon: Network },
          ],
        },
      ],
    },
    {
      // recurso global de PAM Web Console, no de un tenant puntual
      id: "conocimiento",
      label: "Base de conocimiento",
      description: "Causas y soluciones de errores conocidos",
      icon: BookOpen,
      groups: [{ items: [{ to: "/knowledge-base", label: "Artículos", icon: BookOpen }] }],
    },
    {
      id: "reportes",
      label: "Reportes",
      description: "Reportes, ITR y exportaciones",
      icon: FileBarChart2,
      groups: [{ items: [{ to: "/reports", label: "Reportes", icon: FileBarChart2 }] }],
    },
    {
      id: "administracion",
      label: "Administración",
      description: "Tenants, operadores, auditoría y backups",
      icon: Settings2,
      groups: [
        {
          title: "Seguridad",
          items: [
            ...(showTenantAdmin ? [{ to: "/audit", label: "Auditoría", icon: ScrollText }] : []),
            ...(showTenantAdmin
              ? [{ to: "/backups", label: "Backups de cuentas", icon: ArchiveRestore }]
              : []),
          ],
        },
        {
          title: "Configuración",
          items: isPlatformAdmin
            ? [
                { to: "/tenants", label: "Tenants", icon: Building2 },
                { to: "/operators", label: "Operadores", icon: UserCog },
              ]
            : [],
        },
      ],
    },
  ];

  return spaces
    .map((s) => ({ ...s, groups: s.groups.filter((g) => g.items.length > 0) }))
    .filter((s) => s.groups.length > 0);
}

const allItems = (s: Space) => s.groups.flatMap((g) => g.items);

/** Páginas fuera del menú que igual necesitan un nombre en la ubicación. */
const PAGE_TITLES: Record<string, string> = { "/account": "Mi cuenta" };

/** Espacio y página activos, derivados de la ruta (nunca se persisten). Una
 *  ruta de detalle (`/alero-vendors/:id`) marca activo a su listado. */
function useActive(spaces: Space[]): { space?: Space; item?: NavItem; title?: string } {
  const loc = useLocation();
  const path = loc.pathname;
  for (const exact of [true, false]) {
    for (const s of spaces) {
      const item = allItems(s).find((it) =>
        exact ? it.to === path : it.to !== "/" && path.startsWith(it.to + "/"),
      );
      if (item) return { space: s, item };
    }
  }
  return { space: spaces[0], title: PAGE_TITLES[path] };
}

function useCollapsed(): [boolean, () => void] {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(COLLAPSED_KEY) === "1";
    } catch {
      return false;
    }
  });
  const toggle = () =>
    setCollapsed((c) => {
      try {
        localStorage.setItem(COLLAPSED_KEY, c ? "0" : "1");
      } catch {
        /* sin storage: sólo dura la sesión */
      }
      return !c;
    });
  return [collapsed, toggle];
}

function initials(op: Operator | null): string {
  const base = (op?.display_name || op?.username || "?").trim();
  const parts = base.split(/[\s._-]+/).filter(Boolean);
  const chars = parts.length >= 2 ? parts[0][0] + parts[1][0] : base.slice(0, 2);
  return chars.toUpperCase();
}

function Avatar({ op, className }: { op: Operator | null; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "flex size-8 shrink-0 items-center justify-center rounded-full bg-navy-700 text-xs font-semibold text-white",
        className,
      )}
    >
      {initials(op)}
    </span>
  );
}

// ---------------------------------------------------------------- espacios

function SpaceMenuItems({ spaces, active }: { spaces: Space[]; active?: Space }) {
  const nav = useNavigate();
  const loc = useLocation();
  return (
    <>
      <div className="px-2 pb-1.5 pt-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Espacios
      </div>
      {spaces.map((s) => {
        const isActive = s.id === active?.id;
        return (
          <DropdownMenuItem
            key={s.id}
            onClick={() => nav({ pathname: allItems(s)[0].to, search: loc.search })}
            className="items-start gap-3 py-2"
          >
            <span
              className={cn(
                "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md",
                isActive ? "bg-sky-500 text-navy-900" : "bg-navy-50 text-navy-700",
              )}
              aria-hidden="true"
            >
              <s.icon className="size-4" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-semibold text-foreground">{s.label}</span>
              <span className="block text-xs text-muted-foreground">{s.description}</span>
            </span>
            {isActive && <Check className="mt-1 size-4 text-navy-700" aria-label="Espacio actual" />}
          </DropdownMenuItem>
        );
      })}
    </>
  );
}

function SpaceSwitcher({
  spaces,
  active,
  collapsed,
}: {
  spaces: Space[];
  active?: Space;
  collapsed: boolean;
}) {
  const Icon = active?.icon ?? LayoutGrid;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className={cn(
          "flex w-full items-center gap-2.5 rounded-md border border-navy-700 bg-navy-800 text-left transition-colors duration-150 hover:border-navy-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300",
          collapsed ? "justify-center p-2" : "px-2.5 py-2",
        )}
        aria-label={`Espacio actual: ${active?.label ?? ""}. Cambiar de espacio`}
        title={collapsed ? active?.label : undefined}
      >
        <span
          className="flex size-7 shrink-0 items-center justify-center rounded-md bg-sky-500 text-navy-900"
          aria-hidden="true"
        >
          <Icon className="size-4" />
        </span>
        {!collapsed && (
          <>
            <span className="min-w-0 flex-1">
              <span className="block text-[11px] uppercase tracking-wide text-sky-100">Espacio</span>
              <span className="block truncate text-sm font-semibold text-white">{active?.label}</span>
            </span>
            <ChevronsUpDown className="size-4 shrink-0 text-sky-100" aria-hidden="true" />
          </>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" side={collapsed ? "right" : "bottom"} className="w-72">
        <SpaceMenuItems spaces={spaces} active={active} />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ---------------------------------------------------------------- usuario

function UserMenuContent({ side, align }: { side: "top" | "bottom" | "right"; align: "start" | "end" }) {
  const { operator, logout } = useAuth();
  const nav = useNavigate();
  return (
    <DropdownMenuContent side={side} align={align} className="w-72">
      <div className="flex items-center gap-3 px-2 py-2">
        <Avatar op={operator} className="size-10 text-sm" />
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-foreground">
            {operator?.display_name || operator?.username}
          </p>
          <p className="truncate text-xs text-muted-foreground">
            {operator?.email || operator?.username}
          </p>
          <span className="mt-1 inline-flex rounded-sm bg-navy-50 px-1.5 py-0.5 text-[11px] font-medium text-navy-700">
            {roleLabel(operator?.role)}
          </span>
        </div>
      </div>
      <DropdownMenuSeparator />
      <DropdownMenuItem onClick={() => nav("/account")} className="gap-2">
        <UserRound className="size-4 text-muted-foreground" aria-hidden="true" />
        Mi cuenta
      </DropdownMenuItem>
      <DropdownMenuSeparator />
      <DropdownMenuItem onClick={() => void logout("current")} className="gap-2">
        <LogOut className="size-4 text-muted-foreground" aria-hidden="true" />
        Cerrar sesión
      </DropdownMenuItem>
      <DropdownMenuItem variant="destructive" onClick={() => void logout("all")} className="gap-2">
        <MonitorSmartphone className="size-4" aria-hidden="true" />
        Cerrar sesión en todos los equipos
      </DropdownMenuItem>
    </DropdownMenuContent>
  );
}

function SidebarUser({ collapsed }: { collapsed: boolean }) {
  const { operator } = useAuth();
  const name = operator?.display_name || operator?.username || "";
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className={cn(
          "flex w-full items-center gap-2.5 rounded-md text-left transition-colors duration-150 hover:bg-navy-800 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300",
          collapsed ? "justify-center p-1.5" : "px-2 py-2",
        )}
        aria-label={`Cuenta de ${name}, ${roleLabel(operator?.role)}. Abrir menú de usuario`}
        title={collapsed ? `${name} · ${roleLabel(operator?.role)}` : undefined}
      >
        <Avatar op={operator} />
        {!collapsed && (
          <>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-semibold text-white">{name}</span>
              <span className="block truncate text-[11px] text-sky-100">
                {roleLabel(operator?.role)}
              </span>
            </span>
            <ChevronsUpDown className="size-4 shrink-0 text-sky-100" aria-hidden="true" />
          </>
        )}
      </DropdownMenuTrigger>
      <UserMenuContent side={collapsed ? "right" : "top"} align={collapsed ? "end" : "start"} />
    </DropdownMenu>
  );
}

// ---------------------------------------------------------------- sidebar

function NavLinkButton({ item, active, collapsed }: { item: NavItem; active: boolean; collapsed: boolean }) {
  const nav = useNavigate();
  const loc = useLocation();
  return (
    <button
      onClick={() => nav({ pathname: item.to, search: loc.search })}
      aria-current={active ? "page" : undefined}
      title={collapsed ? item.label : undefined}
      className={cn(
        "relative flex w-full items-center gap-2.5 rounded-md text-left text-sm transition-colors duration-150",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300",
        collapsed ? "justify-center px-0 py-2" : "px-2.5 py-1.5",
        active ? "bg-sky-500 font-semibold text-navy-900" : "text-sky-100 hover:bg-navy-800 hover:text-white",
      )}
    >
      <item.icon
        className={cn("size-4 shrink-0", active ? "text-navy-900" : "text-sky-300")}
        aria-hidden="true"
      />
      {collapsed ? (
        <span className="sr-only">{item.label}</span>
      ) : (
        <span className="min-w-0 flex-1 truncate">{item.label}</span>
      )}
      {!!item.badge &&
        (collapsed ? (
          <span className="absolute right-2 top-1.5 size-2 rounded-full bg-line-cyberdefense">
            <span className="sr-only">, {item.badge} pendientes</span>
          </span>
        ) : (
          <span className="rounded-sm bg-line-cyberdefense px-1.5 text-[11px] font-semibold leading-5 text-white">
            {item.badge}
            <span className="sr-only"> pendientes</span>
          </span>
        ))}
    </button>
  );
}

function CollapseToggle({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const label = collapsed ? "Expandir barra lateral" : "Contraer barra lateral";
  return (
    <button
      onClick={onToggle}
      className={cn(
        "flex items-center justify-center rounded-md p-1.5 text-sky-100 transition-colors duration-150 hover:bg-navy-800 hover:text-white",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-300",
        collapsed && "py-2",
      )}
      aria-label={label}
      aria-expanded={!collapsed}
      title={label}
    >
      {collapsed ? (
        <PanelLeftOpen className="size-4" aria-hidden="true" />
      ) : (
        <PanelLeftClose className="size-4" aria-hidden="true" />
      )}
    </button>
  );
}

function Sidebar({
  spaces,
  active,
  activeItem,
  collapsed,
  onToggle,
}: {
  spaces: Space[];
  active?: Space;
  activeItem?: NavItem;
  collapsed: boolean;
  onToggle: () => void;
}) {
  // con muchas páginas en un espacio, la activa puede quedar fuera de la
  // vista: se lleva al área visible al navegar (sin animación).
  const navRef = useRef<HTMLElement>(null);
  useEffect(() => {
    navRef.current
      ?.querySelector('[aria-current="page"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [activeItem?.to, collapsed]);
  return (
    <aside
      className={cn(
        "sticky top-0 hidden h-screen shrink-0 flex-col bg-navy-900 text-white transition-[width] duration-150 ease-out md:flex print:!hidden",
        collapsed ? "w-16" : "w-60",
      )}
    >
      {/* marca (+ contraer, cuando está expandida) */}
      <div className={cn("flex items-center gap-2.5 pb-3 pt-4", collapsed ? "justify-center px-2" : "pl-4 pr-2")}>
        <img src={isotipo} alt="" className="h-7 w-auto shrink-0 object-contain" />
        {!collapsed && (
          <>
            <div className="min-w-0 flex-1 leading-tight">
              <div className="text-sm font-bold tracking-wide">PAM Web Console</div>
              <div className="truncate text-[11px] text-sky-100">Consola de operación PAM</div>
            </div>
            <CollapseToggle collapsed={collapsed} onToggle={onToggle} />
          </>
        )}
      </div>

      <div className={cn("pb-3", collapsed ? "px-2" : "px-3")}>
        <SpaceSwitcher spaces={spaces} active={active} collapsed={collapsed} />
      </div>

      <nav
        ref={navRef}
        aria-label={active?.label ?? "Navegación"}
        className={cn("sidebar-scroll flex-1 overflow-y-auto pb-3", collapsed ? "px-2" : "px-3")}
      >
        {(active?.groups ?? []).map((g, gi) => (
          <div key={g.title ?? gi} className={cn(gi > 0 && "mt-3")}>
            {g.title &&
              (collapsed ? (
                <div className="mx-2 mb-2 border-t border-navy-700" aria-hidden="true" />
              ) : (
                <div className="mb-1 px-2.5 text-[11px] font-semibold uppercase tracking-wide text-sky-300/80">
                  {g.title}
                </div>
              ))}
            <ul className="flex flex-col gap-0.5">
              {g.items.map((it) => (
                <li key={it.to}>
                  <NavLinkButton item={it} active={activeItem?.to === it.to} collapsed={collapsed} />
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      <div className={cn("flex flex-col gap-1 border-t border-navy-700 py-3", collapsed ? "px-2" : "px-3")}>
        {collapsed && <CollapseToggle collapsed={collapsed} onToggle={onToggle} />}
        <SidebarUser collapsed={collapsed} />
      </div>
    </aside>
  );
}

// ---------------------------------------------------------------- topbar

function MobileNav({ spaces }: { spaces: Space[] }) {
  const nav = useNavigate();
  const loc = useLocation();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="rounded-md p-1.5 hover:bg-accent md:hidden"
        aria-label="Abrir menú de navegación"
      >
        <Menu className="size-5" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-[80vh] w-64 overflow-y-auto">
        {spaces.map((s, i) => (
          <Fragment key={s.id}>
            {i > 0 && <DropdownMenuSeparator />}
            <div className="flex items-center gap-1.5 px-2 pb-1 pt-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              <s.icon className="size-3.5" aria-hidden="true" />
              {s.label}
            </div>
            {allItems(s).map((it) => (
              <DropdownMenuItem
                key={it.to}
                onClick={() => nav({ pathname: it.to, search: loc.search })}
                className="gap-2"
              >
                <it.icon className="size-4 text-muted-foreground" aria-hidden="true" />
                {it.label}
              </DropdownMenuItem>
            ))}
          </Fragment>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function Topbar({
  spaces,
  active,
  activeItem,
  title,
}: {
  spaces: Space[];
  active?: Space;
  activeItem?: NavItem;
  title?: string;
}) {
  const { operator } = useAuth();
  return (
    <header className="flex h-13 items-center justify-between gap-3 border-b border-border bg-card px-4 print:hidden">
      <div className="flex min-w-0 items-center gap-2">
        <MobileNav spaces={spaces} />
        {title ? (
          <h2 className="truncate text-sm font-semibold text-foreground">{title}</h2>
        ) : active && (
          <nav aria-label="Ubicación" className="min-w-0">
            <ol className="flex items-center gap-1.5 text-sm">
              <li className="flex items-center gap-1.5 text-muted-foreground">
                <active.icon className="size-4" aria-hidden="true" />
                {active.label}
              </li>
              {activeItem && (
                <>
                  <li aria-hidden="true">
                    <ChevronRight className="size-3.5 text-muted-foreground" />
                  </li>
                  <li aria-current="page" className="truncate font-semibold text-foreground">
                    {activeItem.label}
                  </li>
                </>
              )}
            </ol>
          </nav>
        )}
      </div>
      <div className="flex items-center gap-1">
        <NotificationsBell />
        {/* en pantallas chicas la sidebar no está: el usuario va arriba */}
        <div className="md:hidden">
          <DropdownMenu>
            <DropdownMenuTrigger
              className="rounded-full focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
              aria-label="Abrir menú de usuario"
            >
              <Avatar op={operator} />
            </DropdownMenuTrigger>
            <UserMenuContent side="bottom" align="end" />
          </DropdownMenu>
        </div>
      </div>
    </header>
  );
}

// ---------------------------------------------------------------- shell

export function Shell({ children }: { children: ReactNode }) {
  const spaces = useSpaces();
  const { space, item, title } = useActive(spaces);
  const [collapsed, toggle] = useCollapsed();

  return (
    <div className="flex min-h-screen">
      {/* primer foco con Tab: saltea el sidebar (teclado / lector de pantalla) */}
      <a
        href="#contenido"
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-[70] focus:rounded-md focus:bg-card focus:px-3 focus:py-2 focus:text-sm focus:font-semibold focus:shadow-lg"
      >
        Saltar al contenido
      </a>
      <Sidebar spaces={spaces} active={space} activeItem={item} collapsed={collapsed} onToggle={toggle} />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar spaces={spaces} active={space} activeItem={item} title={title} />
        {import.meta.env.VITE_DEMO === "true" && (
          <div role="note" className="border-b border-border bg-sky-50 px-6 py-2 text-xs text-navy-900">
            <strong>Demo con datos ficticios.</strong> Tenants, cuentas y usuarios son inventados y
            esta página es de solo lectura.{" "}
            <a
              className="font-semibold underline"
              href="https://github.com/AlexPerez7/pam-web-console"
              target="_blank"
              rel="noreferrer"
            >
              Ver el código en GitHub
            </a>
          </div>
        )}
        <main id="contenido" tabIndex={-1} className="flex-1 bg-background p-6 outline-none">
          <Suspense
            fallback={
              <div className="flex h-40 items-center justify-center">
                <Loader2 className="size-5 animate-spin text-muted-foreground" />
              </div>
            }
          >
            {children}
          </Suspense>
        </main>
        <IdleGuard />
      </div>
    </div>
  );
}
