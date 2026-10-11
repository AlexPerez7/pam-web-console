/** `<video>` nativo del navegador — el backend ya transcodificó el SCPR
 *  propietario a MP4/H.264 server-side (`.../recordings/{id}/play/mp4`),
 *  así que acá no hace falta ningún reproductor/decoder propio. Ver
 *  `docs/specs/recording-player.md`. */
export function RecordingPlayer({ src, onError }: { src: string; onError?: () => void }) {
  return (
    <video
      controls
      autoPlay
      src={src}
      onError={onError}
      className="w-full rounded-lg border border-border bg-black"
    />
  );
}
