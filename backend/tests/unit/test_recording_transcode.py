"""Timeout adaptativo del transcodeo de grabaciones (bug real, 2026-09-17:
una grabación de 20 minutos superaba el fijo de 120s). El resto de
`recording_transcode.to_mp4` shellea a scpr_decode/ffmpeg reales —
sin test unitario, se prueba a mano contra el server (ver
docs/specs/done/recording-player.md)."""

from __future__ import annotations

from app.core.recording_transcode import _transcode_timeout


def test_timeout_usa_el_piso_para_grabaciones_cortas():
    # 4 minutos a 10fps -> 2400 frames; con factor 3 pediría 720s, pero el
    # piso/cap no cambian nada acá salvo que la duración escalada sea menor
    assert _transcode_timeout(2_400, 10.0, base=120.0, factor=0.01, cap=540.0) == 120.0


def test_timeout_escala_con_la_duracion_real():
    # 20 minutos (1200s) a 10fps -> 12_000 frames; factor 3 -> 3600s, tope
    # en el cap (mismo caso real reportado: Globex, ~20 minutos)
    frame_count = 1200 * 10
    assert _transcode_timeout(frame_count, 10.0, base=120.0, factor=3.0, cap=540.0) == 540.0


def test_timeout_no_supera_el_cap_ni_con_una_grabacion_muy_larga():
    frame_count = 3600 * 10  # 1 hora
    assert _transcode_timeout(frame_count, 10.0, base=120.0, factor=3.0, cap=540.0) == 540.0


def test_timeout_fps_cero_no_rompe_devuelve_el_piso():
    assert _transcode_timeout(1000, 0.0, base=120.0, factor=3.0, cap=540.0) == 120.0
