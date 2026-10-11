import { useQuery } from "@tanstack/react-query";
import { Bell } from "lucide-react";
import { Link } from "react-router-dom";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { fmtDateTime } from "@/lib/format";
import type { VendorStatusNotice, VendorStatusSummary } from "@/lib/types";

const MAX_ITEMS = 8;

function NoticeRow({ notice }: { notice: VendorStatusNotice }) {
  return (
    <DropdownMenuItem asChild className="flex-col items-start gap-0.5 whitespace-normal py-2">
      <a href={notice.url} target="_blank" rel="noreferrer">
        <div className="flex w-full items-center gap-2">
          <span
            className={
              "shrink-0 rounded-sm px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide " +
              (notice.type === "incident"
                ? "bg-[#fbe2e2] text-danger-ink"
                : "bg-sky-100 text-navy-900")
            }
          >
            {notice.type === "incident" ? "Incidente" : "Mantenimiento"}
          </span>
          <span className="min-w-0 flex-1 truncate font-medium text-navy-900">
            {notice.subject}
          </span>
        </div>
        <p className="text-xs text-muted-foreground">
          {notice.begins_at
            ? `Programado: ${fmtDateTime(notice.begins_at)}`
            : notice.began_at
              ? `Desde ${fmtDateTime(notice.began_at)}`
              : null}
        </p>
      </a>
    </DropdownMenuItem>
  );
}

/** Campanita de notificaciones del topbar — inspirada en "Messages" del
 *  PVWA (icono de campana -> panel con novedades). v1: sólo eventos de
 *  status.cyberark.com (incidentes/mantenimientos del FABRICANTE, ver
 *  `pages/CyberArkStatus.tsx`) — comparte la misma queryKey, así que no
 *  duplica el request. Pensado para sumar más tipos de evento de PAM Web Console más
 *  adelante (hoy "Aprobaciones" ya tiene su propio badge en el nav). */
export function NotificationsBell() {
  const { operator } = useAuth();
  const q = useQuery({
    queryKey: ["vendor-status", "cyberark"],
    queryFn: () => api<VendorStatusSummary>("/vendor-status/cyberark"),
    enabled: !!operator,
    staleTime: 60_000,
    refetchInterval: 5 * 60_000,
  });

  const active = q.data?.active_notices ?? [];
  const upcoming = q.data?.upcoming_notices ?? [];
  const count = active.length + upcoming.length;
  const items = [...active, ...upcoming].slice(0, MAX_ITEMS);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="relative inline-flex size-9 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        aria-label={count > 0 ? `Notificaciones, ${count} sin revisar` : "Notificaciones"}
      >
        <Bell className="size-4" aria-hidden="true" />
        {count > 0 && (
          <span className="absolute right-1 top-1 flex min-w-3.5 items-center justify-center rounded-full bg-line-cyberdefense px-1 text-[10px] font-semibold leading-4 text-white">
            {count > 9 ? "9+" : count}
          </span>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80 p-0">
        <div className="flex items-center justify-between px-3 py-2">
          <span className="text-sm font-semibold text-navy-900">Notificaciones</span>
          <Link to="/cyberark-status" className="text-xs text-sky-600 hover:underline">
            Ver todo
          </Link>
        </div>
        <DropdownMenuSeparator className="my-0" />
        <div className="max-h-[70vh] overflow-y-auto p-1">
          {q.isLoading && (
            <p className="px-2 py-3 text-sm text-muted-foreground">Cargando…</p>
          )}
          {q.isError && (
            <p className="px-2 py-3 text-sm text-muted-foreground">
              No se pudo consultar el estado de CyberArk.
            </p>
          )}
          {q.data && items.length === 0 && (
            <p className="px-2 py-3 text-sm text-muted-foreground">
              Sin incidencias ni mantenimientos programados en CyberArk.
            </p>
          )}
          {items.map((n) => (
            <NoticeRow key={n.id} notice={n} />
          ))}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
