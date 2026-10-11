import { useQuery } from "@tanstack/react-query";
import { Download, Loader2, Play, ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { RecordingPlayer } from "@/components/RecordingPlayer";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { api, apiBlob } from "@/lib/api";
import { saveBlob } from "@/lib/download";
import { roleAtLeast } from "@/lib/roles";
import type { TenantSummary } from "@/lib/types";

const MIN_REASON = 8;

// Pedido de Alex (2026-09-16): el AVI original necesita un codec pack de
// terceros (SCPR/ScreenPressor) para reproducirse — mientras "Descargar
// video (.mp4)" cubra el caso de uso, el botón del AVI queda oculto (no
// borrado: es la única forma de bajar el archivo bit-exacto, útil como
// evidencia o si el operador ya tiene el codec real instalado).
const SHOW_AVI_DOWNLOAD = false;

/** Reproducir/descargar el video de una grabación — gateado por la
 *  feature `recording.play` (apagada por defecto) + rol `tenant_admin+`,
 *  el techo más alto de PAM Web Console (mismo criterio que "Ver contraseña"): el
 *  video puede mostrar contraseñas tipeadas en pantalla u otro contenido
 *  sensible. "Reproducir" transcodifica server-side a MP4
 *  (`.../play/mp4`, `app.core.recording_transcode`) y lo muestra
 *  embebido con un `<video>` nativo; "Descargar video (.mp4)" pega al
 *  mismo endpoint pero guarda el archivo en vez de mostrarlo — ninguna
 *  de las dos necesita instalar nada del lado del operador. "Descargar
 *  original (.avi)" trae el archivo tal cual de CyberArk (`.../play`,
 *  requiere el codec SCPR real) — oculto por ahora, ver
 *  `SHOW_AVI_DOWNLOAD`. Reemplaza `RecordingDownloadSection.tsx` (sólo
 *  descarga). Ver `docs/specs/recording-player.md`. */
export function RecordingPlayerSection({
  tenant,
  sessionId,
  filename,
}: {
  tenant: string;
  sessionId: string;
  filename?: string;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<"play" | "download" | "downloadMp4" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [downloaded, setDownloaded] = useState(false);
  const [playUrl, setPlayUrl] = useState<string | null>(null);

  const t = useQuery({
    queryKey: ["tenant", tenant],
    queryFn: () => api<TenantSummary>(`/tenants/${tenant}`),
  });

  // Object URL propio del Blob ya descargado — se revoca al reemplazarlo
  // o desmontar, nunca se reusa entre aperturas (Requisito 6 de la spec:
  // sin caché, cada apertura vuelve a pedir la razón y a transcodificar).
  useEffect(() => {
    return () => {
      if (playUrl) URL.revokeObjectURL(playUrl);
    };
  }, [playUrl]);

  const allowed = roleAtLeast(t.data?.my_role, "tenant_admin");
  if (!allowed) return null;

  async function play() {
    setBusy("play");
    setError(null);
    setDownloaded(false);
    try {
      const { blob } = await apiBlob(
        `/tenants/${tenant}/recordings/${encodeURIComponent(sessionId)}/play/mp4`,
        { method: "POST", body: { reason } },
      );
      setPlayUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return URL.createObjectURL(blob);
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function downloadMp4() {
    setBusy("downloadMp4");
    setError(null);
    setDownloaded(false);
    try {
      const name = (filename ?? `recording-${sessionId}.avi`).replace(/\.\w+$/, ".mp4");
      const { blob, filename: fname } = await apiBlob(
        `/tenants/${tenant}/recordings/${encodeURIComponent(sessionId)}/play/mp4?filename=${encodeURIComponent(name)}`,
        { method: "POST", body: { reason } },
      );
      saveBlob(blob, fname);
      setDownloaded(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function download() {
    setBusy("download");
    setError(null);
    setDownloaded(false);
    try {
      const qs = filename ? `?filename=${encodeURIComponent(filename)}` : "";
      const { blob, filename: fname } = await apiBlob(
        `/tenants/${tenant}/recordings/${encodeURIComponent(sessionId)}/play${qs}`,
        { method: "POST", body: { reason } },
      );
      saveBlob(blob, fname);
      setDownloaded(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3">
      <div className="flex items-start gap-2">
        <ShieldAlert className="mt-0.5 size-4 shrink-0 text-destructive" />
        <div>
          <p className="text-sm font-semibold">Grabación de la sesión</p>
          <p className="text-xs text-muted-foreground">
            Puede mostrar contraseñas tipeadas en pantalla u otro contenido sensible. Queda
            auditado, sin el archivo. "Reproducir" transcodifica el video en el momento — con
            grabaciones cortas tarda unos segundos, con grabaciones largas (20+ minutos) puede
            tardar varios minutos.
          </p>
        </div>
      </div>

      <label className="text-xs font-medium text-muted-foreground">
        Razón (mínimo {MIN_REASON} caracteres)
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={2}
          className="mt-1 w-full rounded-md border border-input bg-card px-2 py-1.5 text-sm"
          placeholder="ej. investigar incidente reportado por el cliente"
        />
      </label>

      {error && <Alert variant="destructive">{error}</Alert>}
      {downloaded && <Alert variant="success">Descargado.</Alert>}

      <div className="flex gap-2">
        <Button
          size="sm"
          onClick={play}
          disabled={reason.trim().length < MIN_REASON || busy !== null}
        >
          {busy === "play" ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Play className="size-3.5" />
          )}
          Reproducir
        </Button>
        <Button
          variant="secondary"
          size="sm"
          onClick={downloadMp4}
          disabled={reason.trim().length < MIN_REASON || busy !== null}
        >
          {busy === "downloadMp4" ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Download className="size-3.5" />
          )}
          Descargar video (.mp4)
        </Button>
        {SHOW_AVI_DOWNLOAD && (
          <Button
            variant="destructive"
            size="sm"
            onClick={download}
            disabled={reason.trim().length < MIN_REASON || busy !== null}
          >
            {busy === "download" ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Download className="size-3.5" />
            )}
            Descargar original (.avi)
          </Button>
        )}
      </div>

      {playUrl && (
        <RecordingPlayer
          src={playUrl}
          onError={() => setError("No se pudo reproducir el video transcodificado.")}
        />
      )}
    </div>
  );
}
