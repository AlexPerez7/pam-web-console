import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { Card, Chip } from "@/components/AccountDetailPanel";
import { RecordingPlayerSection } from "@/components/RecordingPlayerSection";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { api } from "@/lib/api";
import { dash, fmtDateTime } from "@/lib/format";
import type { Recording, RecordingActivitiesResponse } from "@/lib/types";

function riskBadge(severity: unknown) {
  if (!severity) return null;
  const s = String(severity).toLowerCase();
  const variant =
    s.includes("critical") || s.includes("high")
      ? "danger"
      : s.includes("medium")
        ? "warning"
        : "neutral";
  return <Badge variant={variant}>{String(severity)}</Badge>;
}

/** Detalle de una grabación de sesión PSM — reusa la fila que ya trajo la
 *  lista (mismo campo que "Get recording details"/"properties" de
 *  CyberArk, sin pedirla de nuevo) + línea de tiempo de actividades +
 *  descarga. Ver `docs/specs/session-recordings.md`. */
export function RecordingDetailPanel({
  tenant,
  recording,
  onClose,
}: {
  tenant: string;
  recording: Recording | null;
  onClose: () => void;
}) {
  const sessionId = recording?.SessionID ? String(recording.SessionID) : undefined;

  const activities = useQuery({
    queryKey: ["recording-activities", tenant, sessionId],
    queryFn: () =>
      api<RecordingActivitiesResponse>(
        `/tenants/${tenant}/recordings/${encodeURIComponent(sessionId ?? "")}/activities`,
      ),
    enabled: !!recording && !!sessionId,
  });

  if (!recording || !sessionId) {
    return <Sheet open={false} onOpenChange={() => onClose()} />;
  }

  const heading =
    recording.User && recording.RemoteMachine
      ? `${recording.User}@${recording.RemoteMachine}`
      : sessionId;
  // NO usar RecordingFiles[0].FileName: cuando una grabación tiene varios
  // componentes (video + texto/keystrokes), ese primer archivo suele ser
  // el de texto, no el video que se descarga (`isVideo=true`, ver
  // client.play_recording). Nombre fijo con extensión .avi — confirmado
  // contra Globex que el contenedor real es AVI (códec propietario
  // "SCPR" de CyberArk, requiere el PSM Universal Recorder Player).
  const filenameHint = `${sessionId}.avi`;

  return (
    <Sheet open={!!recording} onOpenChange={(o) => !o && onClose()}>
      <SheetContent>
        <SheetTitle>{heading}</SheetTitle>

        <div className="flex flex-wrap items-center gap-2">
          {recording.SafeName && <Chip tone="safe">{String(recording.SafeName)}</Chip>}
          {recording.Protocol && <Chip tone="platform">{String(recording.Protocol)}</Chip>}
          {riskBadge(recording.Severity)}
        </div>

        <Card title="Detalle">
          <div className="grid grid-cols-[140px_1fr] gap-x-3 gap-y-1.5 p-4 text-xs">
            <span className="text-muted-foreground">Inicio</span>
            <span>{fmtDateTime(recording.Start)}</span>
            <span className="text-muted-foreground">Fin</span>
            <span>{fmtDateTime(recording.End)}</span>
            <span className="text-muted-foreground">Cliente</span>
            <span>{dash(recording.Client)}</span>
            <span className="text-muted-foreground">Usuario de la cuenta</span>
            <span>{dash(recording.AccountUsername)}</span>
            <span className="text-muted-foreground">Plataforma</span>
            <span>{dash(recording.AccountPlatformID)}</span>
            <span className="text-muted-foreground">Address</span>
            <span>{dash(recording.AccountAddress)}</span>
            <span className="text-muted-foreground">Ticket</span>
            <span>{dash(recording.TicketID)}</span>
          </div>
        </Card>

        <Card title="Actividad">
          <div className="p-4">
            {activities.isLoading && (
              <Loader2 className="size-4 animate-spin text-muted-foreground" />
            )}
            {activities.isError && (
              <Alert variant="destructive">{(activities.error as Error).message}</Alert>
            )}
            {activities.data &&
              (activities.data.activities.length === 0 ? (
                <p className="text-sm text-muted-foreground">Sin actividades registradas.</p>
              ) : (
                <ul className="flex flex-col gap-1.5 text-sm">
                  {activities.data.activities.map((a, i) => (
                    <li
                      key={a.ActivityId ?? i}
                      className="border-b border-border pb-1.5 last:border-0"
                    >
                      {a.ActivityText ?? "—"}
                    </li>
                  ))}
                </ul>
              ))}
          </div>
        </Card>

        <RecordingPlayerSection tenant={tenant} sessionId={sessionId} filename={filenameHint} />
      </SheetContent>
    </Sheet>
  );
}
