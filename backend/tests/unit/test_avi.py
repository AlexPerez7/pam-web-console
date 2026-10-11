"""Parser de contenedores AVI/RIFF (`app.domain.avi`). Construye un AVI
mínimo a mano (sin datos de video reales, sólo la estructura del
contenedor) para probar el parseo sin depender de una grabación real.
Ver docs/specs/recording-player.md."""

from __future__ import annotations

import struct

import pytest

from app.domain.avi import AviParseError, parse


def _chunk(fourcc: bytes, payload: bytes) -> bytes:
    out = fourcc + struct.pack("<I", len(payload)) + payload
    if len(payload) % 2 == 1:
        out += b"\x00"
    return out


def _list(list_type: bytes, payload: bytes) -> bytes:
    return _chunk(b"LIST", list_type + payload)


def _avih(width: int, height: int, micro_sec_per_frame: int, total_frames: int) -> bytes:
    return _chunk(
        b"avih",
        struct.pack(
            "<11I",
            micro_sec_per_frame,  # dwMicroSecPerFrame
            0,  # dwMaxBytesPerSec
            0,  # dwPaddingGranularity
            0,  # dwFlags
            total_frames,  # dwTotalFrames
            0,  # dwInitialFrames
            1,  # dwStreams
            0,  # dwSuggestedBufferSize
            width,  # dwWidth
            height,  # dwHeight
            0,  # dwReserved[0] (truncado, alcanza para el parser)
        ),
    )


def _strh(fcc_type: bytes, fcc_handler: bytes) -> bytes:
    # AVISTREAMHEADER real completo no hace falta — el parser sólo lee
    # los primeros 8 bytes (fccType + fccHandler).
    return _chunk(b"strh", fcc_type + fcc_handler + b"\x00" * 48)


def _strf(width: int, height: int, bit_count: int) -> bytes:
    return _chunk(
        b"strf",
        struct.pack(
            "<3I2H2I2i2I",
            40,  # biSize
            width,
            height,
            1,  # biPlanes
            bit_count,
            0,  # biCompression (no lo lee el parser)
            0,  # biSizeImage
            0,
            0,  # biXPelsPerMeter/biYPelsPerMeter
            0,
            0,  # biClrUsed/biClrImportant
        ),
    )


def build_avi(
    *,
    width: int = 320,
    height: int = 240,
    bit_count: int = 24,
    fcc_handler: bytes = b"SCPR",
    micro_sec_per_frame: int = 100_000,  # 10 fps
    frames: list[bytes] | None = None,
) -> bytes:
    frames = frames if frames is not None else [b"\x01\x02\x03", b"\x04\x05"]
    hdrl = _avih(width, height, micro_sec_per_frame, len(frames)) + _list(
        b"strl", _strh(b"vids", fcc_handler) + _strf(width, height, bit_count)
    )
    movi = b"".join(_chunk(b"00dc", f) for f in frames)
    body = b"AVI " + _list(b"hdrl", hdrl) + _list(b"movi", movi)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_parse_ok():
    v = parse(build_avi())
    assert v.width == 320
    assert v.height == 240
    assert v.bits_per_pixel == 24
    assert v.fourcc == "SCPR"
    assert v.fps == pytest.approx(10.0)
    assert v.frames == [b"\x01\x02\x03", b"\x04\x05"]


def test_parse_frames_intercalados_en_rec():
    # algunos AVI agrupan frames en sub-listas 'rec ' dentro de 'movi'
    # (interleaving) — el parser debe bajar a ese nivel también.
    width, height, bit_count = 100, 50, 24
    hdrl = _avih(width, height, 100_000, 2) + _list(
        b"strl", _strh(b"vids", b"SCPR") + _strf(width, height, bit_count)
    )
    rec = _list(b"rec ", _chunk(b"00dc", b"\xaa") + _chunk(b"00dc", b"\xbb"))
    body = b"AVI " + _list(b"hdrl", hdrl) + _list(b"movi", rec)
    raw = b"RIFF" + struct.pack("<I", len(body)) + body

    v = parse(raw)
    assert v.frames == [b"\xaa", b"\xbb"]


def test_parse_no_riff():
    with pytest.raises(AviParseError):
        parse(b"not an avi file at all")


def test_parse_codec_no_soportado():
    with pytest.raises(AviParseError, match="no es SCPR"):
        parse(build_avi(fcc_handler=b"XVID"))


def test_parse_sin_frames():
    with pytest.raises(AviParseError, match="movi vac"):
        parse(build_avi(frames=[]))
