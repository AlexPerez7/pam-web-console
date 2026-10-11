"""Parser de contenedores AVI/RIFF, puro (sin dependencias externas más
allá de `struct`). Se usa para extraer los frames comprimidos SCPR de
una grabación PSM antes de decodificarlos — no decodifica los frames en
sí, sólo separa el contenedor. Ver `docs/specs/recording-player.md`.

Formato (referencia: "AVI RIFF File Reference" de Microsoft):
    RIFF <size> 'AVI '
      LIST <size> 'hdrl'
        'avih' <56 bytes>              AVIMAINHEADER (fps, tamaño, cant. frames)
        LIST <size> 'strl'             uno por stream (video, audio, ...)
          'strh' <56 bytes>            AVISTREAMHEADER (fccType, fccHandler)
          'strf' <N bytes>             BITMAPINFOHEADER si fccType == 'vids'
      LIST <size> 'movi'
        '00dc' <size> <bytes>          frame comprimido del stream 0
        LIST <size> 'rec '             frames intercalados (opcional), recursivo
          '00dc' ...
      'idx1' ...                       índice opcional, no hace falta — se
                                        recorre 'movi' directo en orden.

Todo chunk se alinea a 2 bytes (si `size` es impar, un byte de padding
después del payload).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

_RECURSE_LISTS = {"hdrl", "strl", "rec "}


class AviParseError(ValueError):
    """El archivo no es un AVI válido, o no trae un stream de video SCPR."""


@dataclass
class AviVideo:
    width: int
    height: int
    bits_per_pixel: int
    fourcc: str
    fps: float
    frame_count: int
    frames: list[bytes]


@dataclass
class _Parser:
    data: bytes
    width: int = 0
    height: int = 0
    bpp: int = 0
    micro_sec_per_frame: int = 0
    fourcc_handler: str = ""
    frame_count: int = 0
    frames: list[bytes] = field(default_factory=list)
    _awaiting_video_strf: bool = False

    def walk(self, start: int, end: int) -> None:
        pos = start
        while pos + 8 <= end:
            fourcc = self.data[pos : pos + 4].decode("ascii", errors="replace")
            (size,) = struct.unpack_from("<I", self.data, pos + 4)
            payload_pos = pos + 8
            payload_end = payload_pos + size
            if payload_end > end:
                break  # chunk truncado — se ignora el resto (archivo cortado)

            if fourcc == "LIST" and payload_end >= payload_pos + 4:
                list_type = self.data[payload_pos : payload_pos + 4].decode(
                    "ascii", errors="replace"
                )
                if list_type in _RECURSE_LISTS:
                    self.walk(payload_pos + 4, payload_end)
                elif list_type == "movi":
                    self._collect_movi(payload_pos + 4, payload_end)
            elif fourcc == "avih":
                self._parse_avih(payload_pos)
            elif fourcc == "strh":
                self._parse_strh(payload_pos)
            elif fourcc == "strf" and self._awaiting_video_strf:
                self._parse_strf(payload_pos)
                self._awaiting_video_strf = False

            pos = payload_end + (size % 2)  # alinear a 2 bytes

    def _parse_avih(self, pos: int) -> None:
        # AVIMAINHEADER: dwMicroSecPerFrame, dwMaxBytesPerSec,
        # dwPaddingGranularity, dwFlags, dwTotalFrames, ...
        if pos + 20 > len(self.data):
            return
        (self.micro_sec_per_frame,) = struct.unpack_from("<I", self.data, pos)
        (self.frame_count,) = struct.unpack_from("<I", self.data, pos + 16)

    def _parse_strh(self, pos: int) -> None:
        # AVISTREAMHEADER: fccType (4), fccHandler (4), ...
        if pos + 8 > len(self.data):
            return
        fcc_type = self.data[pos : pos + 4]
        self._awaiting_video_strf = fcc_type == b"vids"
        if self._awaiting_video_strf:
            self.fourcc_handler = self.data[pos + 4 : pos + 8].decode("ascii", errors="replace")

    def _parse_strf(self, pos: int) -> None:
        # BITMAPINFOHEADER: biSize, biWidth, biHeight, biPlanes, biBitCount, ...
        if pos + 16 > len(self.data):
            return
        (width,) = struct.unpack_from("<i", self.data, pos + 4)
        (height,) = struct.unpack_from("<i", self.data, pos + 8)
        (bit_count,) = struct.unpack_from("<H", self.data, pos + 14)
        self.width = width
        # biHeight negativo = top-down; guardamos el valor absoluto, la
        # orientación la maneja el decoder (mismo criterio que el codec
        # original, que siempre trabaja bottom-up salvo indicación).
        self.height = abs(height)
        self.bpp = bit_count

    def _collect_movi(self, start: int, end: int) -> None:
        pos = start
        while pos + 8 <= end:
            fourcc = self.data[pos : pos + 4]
            (size,) = struct.unpack_from("<I", self.data, pos + 4)
            payload_pos = pos + 8
            payload_end = payload_pos + size
            if payload_end > end:
                break

            if fourcc == b"LIST" and payload_end >= payload_pos + 4:
                list_type = self.data[payload_pos : payload_pos + 4]
                if list_type == b"rec ":
                    self._collect_movi(payload_pos + 4, payload_end)
            elif fourcc[:2] == b"00" and fourcc[2:4] in (b"dc", b"db"):
                # stream 0, chunk comprimido ('dc') o crudo ('db') — el
                # video de una grabación PSM es siempre el stream 0, sin
                # audio (ver docs/specs/recording-player.md, Alcance).
                self.frames.append(self.data[payload_pos:payload_end])

            pos = payload_end + (size % 2)


def parse(raw: bytes) -> AviVideo:
    """Extrae ancho/alto/bits por pixel/fps + los frames comprimidos del
    stream de video (stream 0) de un AVI. No decodifica los frames.
    Levanta `AviParseError` si no es un AVI válido, el video no es SCPR,
    o no hay frames."""
    if len(raw) < 12 or raw[0:4] != b"RIFF" or raw[8:12] != b"AVI ":
        raise AviParseError("no es un archivo RIFF/AVI válido")

    p = _Parser(data=raw)
    p.walk(12, len(raw))

    if p.fourcc_handler.upper() != "SCPR":
        raise AviParseError(f"el stream de video no es SCPR (fourcc='{p.fourcc_handler or '?'}')")
    if p.width <= 0 or p.height <= 0:
        raise AviParseError("no se encontró un header de video válido (avih/strf)")
    if not p.frames:
        raise AviParseError("el AVI no trae frames de video (movi vacío)")

    fps = 1_000_000 / p.micro_sec_per_frame if p.micro_sec_per_frame else 0.0
    return AviVideo(
        width=p.width,
        height=p.height,
        bits_per_pixel=p.bpp,
        fourcc=p.fourcc_handler,
        fps=fps,
        frame_count=p.frame_count or len(p.frames),
        frames=p.frames,
    )
