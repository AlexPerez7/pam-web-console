"""Tests de app.core.csv_export y de la config de columnas de exports."""

from __future__ import annotations

from app.api.exports import _COLUMNS, _FETCH
from app.core.csv_export import dotted, epoch_iso, flag, to_csv


def test_dotted_navega_y_tolera_faltantes():
    assert dotted("general.id")({"general": {"id": "WinDomain"}}) == "WinDomain"
    assert dotted("general.id")({"general": None}) is None
    assert dotted("a.b.c")({}) is None


def test_epoch_iso():
    assert epoch_iso(0) == ""
    assert epoch_iso(None) == ""
    assert epoch_iso("no") == ""
    assert epoch_iso(1704067200) == "2024-01-01T00:00:00+00:00"


def test_flag():
    assert flag(True) == "true"
    assert flag(False) == "false"
    assert flag(None) == ""


def test_to_csv_cabecera_filas_y_escape():
    rows = [
        {"name": "web01", "note": 'tiene, coma y "comillas"'},
        {"name": "db01"},  # falta note
    ]
    cols = [("name", dotted("name")), ("note", dotted("note"))]
    out = to_csv(rows, cols)
    lines = out.split("\r\n")
    assert lines[0] == "name,note"
    assert lines[1] == 'web01,"tiene, coma y ""comillas"""'
    assert lines[2] == "db01,"  # note vacío
    assert out.endswith("\r\n")


def test_to_csv_booleanos_y_none():
    out = to_csv([{"x": True, "y": None}], [("x", dotted("x")), ("y", dotted("y"))])
    assert out.split("\r\n")[1] == "true,"


def test_columns_y_fetch_cubren_los_mismos_recursos():
    assert set(_COLUMNS) == set(_FETCH) == {"accounts", "safes", "platforms", "users", "groups"}
    for cols in _COLUMNS.values():
        assert cols and all(callable(fn) for _, fn in cols)
