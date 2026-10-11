"""Transcodeo server-side de grabaciones PSM (AVI+SCPR) a MP4/H.264, para
reproducirlas embebidas en el navegador con un `<video>` nativo — sin
instalar códecs de terceros en la máquina del operador. Ver
`docs/specs/recording-player.md` y
`backend/tools/scpr_decode/THIRD_PARTY_NOTICE.md`.

Pipeline: AVI (bytes de CyberArk) -> `app.domain.avi.parse` (en memoria)
-> stdin de `scpr_decode` (frames comprimidos) -> su stdout (RGB/BGR
crudo, sin padding de alineación) -> stdin de `ffmpeg` (encode a MP4) ->
stdout de ffmpeg = el MP4 final. Los dos subprocesos van encadenados por
pipe real (`Popen` con `stdout` de uno como `stdin` del otro) — el video
crudo sin comprimir de una grabación larga en alta resolución puede pesar
varios GB, no se bufferiza completo en memoria de Python en ningún punto.

Sincrónico — pensado para correr en un hilo aparte (`asyncio.to_thread`)
desde un handler async, mismo criterio que `app.core.secrets` con `age`.
"""

from __future__ import annotations

import contextlib
import signal
import struct
import subprocess
import threading

from app.config import get_settings
from app.domain.avi import AviParseError, AviVideo, parse

# BGR/BGRA: convención típica de Windows (BITMAPINFOHEADER/DIB) para
# imágenes de 24/32 bpp — el códec original nombra los bytes "r,g,b" en
# ese orden en su código, pero era sólo el nombre de la variable, no el
# canal real. Confirmado sin problema visible contra una grabación real
# de Globex (2026-09-16, ver docs/specs/done/recording-player.md).
_PIX_FMT = {16: "rgb565le", 24: "bgr24", 32: "bgra"}


class TranscodeError(Exception):
    """El AVI no es válido, o `scpr_decode`/`ffmpeg` fallaron."""


def _build_decoder_stdin(video: AviVideo) -> bytes:
    """Arma el stream de entrada de `scpr_decode`: header (ancho, alto,
    bits por pixel) + por cada frame, su tamaño (uint32) + los bytes
    comprimidos. Ver el protocolo documentado en
    `backend/tools/scpr_decode/main.cpp`."""
    header = struct.pack("<IIH", video.width, video.height, video.bits_per_pixel)
    parts = [header]
    for f in video.frames:
        parts.append(struct.pack("<I", len(f)))
        parts.append(f)
    return b"".join(parts)


def _transcode_timeout(
    frame_count: int, fps: float, *, base: float, factor: float, cap: float
) -> float:
    """Timeout (segundos) del transcodeo, escalado según la duración REAL
    de la grabación — bug real (2026-09-17): un valor fijo de 120s
    alcanzaba para una grabación de 4 minutos pero no para una de 20;
    transcodear es CPU-bound, no en tiempo real. `cap` lo deja con margen
    bajo el `proxy_read_timeout` de nginx (600s), para que este error
    explique el motivo real en vez de un 504 genérico."""
    duration_s = frame_count / fps if fps > 0 else 0.0
    return min(max(base, duration_s * factor), cap)


def to_mp4(raw_avi: bytes) -> bytes:
    """Decodifica y transcodifica una grabación PSM (AVI+SCPR) a MP4.
    Levanta `TranscodeError` si el AVI no es válido, la resolución de
    color no está soportada, o `scpr_decode`/`ffmpeg` fallan."""
    try:
        video = parse(raw_avi)
    except AviParseError as e:
        raise TranscodeError(f"no se pudo leer el AVI: {e}") from e

    pix_fmt = _PIX_FMT.get(video.bits_per_pixel)
    if pix_fmt is None:
        raise TranscodeError(f"resolución de color no soportada: {video.bits_per_pixel} bpp")

    settings = get_settings()
    stdin_data = _build_decoder_stdin(video)
    timeout = _transcode_timeout(
        len(video.frames),
        video.fps,
        base=settings.recording_transcode_timeout,
        factor=settings.recording_transcode_timeout_factor,
        cap=settings.recording_transcode_timeout_max,
    )

    decode_proc = subprocess.Popen(  # noqa: S603
        [settings.scpr_decode_bin],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if decode_proc.stdin is None or decode_proc.stdout is None:
        raise TranscodeError("no se pudo abrir el pipe con scpr_decode")  # no debería pasar
    decoder_stdin = decode_proc.stdin  # narrowed: no-Optional para el closure de abajo

    ffmpeg_proc = subprocess.Popen(  # noqa: S603
        [
            settings.ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            pix_fmt,
            "-s",
            f"{video.width}x{video.height}",
            "-r",
            f"{video.fps:.6f}",
            "-i",
            "-",
            # vflip: confirmado contra una grabación real de Globex — el
            # códec original guarda las filas bottom-up (como
            # BITMAPINFOHEADER/DIB de Windows), scpr_decode las vuelca tal
            # cual a stdout sin reordenarlas. pad: yuv420p submuestrea color
            # 2x2 -> libx264 exige ancho/alto pares; una grabación real vino
            # en 1600x731 (alto impar) y libx264 rechazó abrir el encoder —
            # se redondea hacia arriba con una franja negra de 1px en vez de
            # recortar contenido real.
            "-vf",
            "vflip,pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            # Sin esto, el MP4 queda con color_range/colorspace/primaries/
            # transfer "unknown" (confirmado con ffprobe) -- cada reproductor
            # adivina por su cuenta cómo interpretarlo. Visto real: colores
            # raros en VLC, perfecto en el reproductor nativo de Windows,
            # mismo archivo. bt709 + tv (limited) es lo que libx264 ya asume
            # al convertir de RGB sin flags adicionales -- esto sólo lo
            # declara en el stream en vez de dejarlo implícito. Las flags
            # genéricas (-color_primaries/-color_trc) alcanzan para
            # colorspace/color_range pero no siempre le llegan a los VUI
            # que libx264 escribe él mismo (confirmado con ffprobe) -- de
            # ahí el -x264-params explícito, que sí los fuerza.
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            "-color_range",
            "tv",
            "-x264-params",
            "colorprim=bt709:transfer=bt709:colormatrix=bt709:fullrange=off",
            "-movflags",
            "frag_keyframe+empty_moov",
            "-f",
            "mp4",
            "-",
        ],
        stdin=decode_proc.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    decode_proc.stdout.close()  # el fd queda en manos de ffmpeg; ver docs de Popen sobre pipes

    # El write de stdin_data NO puede ser síncrono acá: con una grabación real
    # (varios cientos de KB/MB de frames comprimidos, más que el buffer de un
    # pipe de Linux) este write se bloquea esperando que scpr_decode lea, que
    # a su vez se bloquea escribiendo a ffmpeg, que a su vez se bloquea
    # escribiendo SU salida — que nadie drena todavía porque
    # ffmpeg_proc.communicate() ni arrancó. Deadlock real, sin timeout que lo
    # salve (visto contra una grabación de Globex de 4 minutos: quedaba
    # "cargando" para siempre). Se escribe en un hilo aparte para que los tres
    # procesos corran en paralelo de verdad.
    def _feed_decoder_stdin() -> None:
        try:
            decoder_stdin.write(stdin_data)
        except (BrokenPipeError, OSError):
            pass  # scpr_decode puede haber fallado y cerrado su stdin antes de terminar el write
        finally:
            with contextlib.suppress(OSError):
                decoder_stdin.close()

    feeder = threading.Thread(target=_feed_decoder_stdin, daemon=True)
    feeder.start()

    try:
        mp4_bytes, ffmpeg_err = ffmpeg_proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as e:
        decode_proc.kill()
        ffmpeg_proc.kill()
        feeder.join(timeout=5)
        raise TranscodeError(f"transcodeo superó el timeout de {timeout:.0f}s") from e

    feeder.join(timeout=timeout)
    decode_returncode = decode_proc.wait(timeout=timeout)

    # ffmpeg primero: si cortó antes de tiempo (parámetros -s/-r/-pix_fmt que no
    # calzan con lo que realmente manda scpr_decode, stream rawvideo mal
    # formado, etc.), cierra su stdin -> el siguiente write de scpr_decode a su
    # propio stdout recibe SIGPIPE y muere sin nada en su stderr. Ese SIGPIPE
    # es un síntoma, no la causa: el error real está del lado de ffmpeg.
    if ffmpeg_proc.returncode != 0 or not mp4_bytes:
        raise TranscodeError(
            f"ffmpeg terminó con error: {ffmpeg_err.decode(errors='replace')[:500]}"
        )
    if decode_returncode != 0:
        decode_stderr = (
            decode_proc.stderr.read().decode(errors="replace") if decode_proc.stderr else ""
        )
        # returncode negativo = terminado por señal (crash, no un `return N` del
        # propio scpr_decode) — sin esto, un segfault/abort queda indistinguible
        # de un error silencioso: stderr vacío en ambos casos.
        if decode_returncode < 0:
            try:
                sig_name = signal.Signals(-decode_returncode).name
            except ValueError:
                sig_name = f"desconocida ({-decode_returncode})"
            detail = f"terminado por señal {sig_name}" + (
                f" — stderr: {decode_stderr[:500]}" if decode_stderr else " (sin salida en stderr)"
            )
        else:
            detail = f"código de salida {decode_returncode}: {decode_stderr[:500]}"
        raise TranscodeError(f"scpr_decode {detail}")

    return mp4_bytes
