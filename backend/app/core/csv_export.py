"""Genera CSV a partir de una lista de objetos crudos de la API de CyberArk.

`to_csv(rows, columns)` — `columns` es una lista de `(cabecera, extractor)`.
El extractor recibe la fila (dict) y devuelve el valor de la celda; los
helpers `dotted` / `epoch_iso` / `flag` cubren los casos comunes.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

Extractor = Callable[[dict], Any]
Column = tuple[str, Extractor]


def dotted(path: str) -> Extractor:
    """Extractor por ruta con puntos: `dotted("general.id")`."""
    parts = path.split(".")

    def _get(row: dict) -> Any:
        cur: Any = row
        for p in parts:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(p)
        return cur

    return _get


def epoch_iso(value: Any) -> str:
    """Epoch en segundos -> ISO 8601 UTC. Vacío si es nulo, 0 o inválido."""
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(int(value), tz=UTC).isoformat()
    except (ValueError, OSError, OverflowError, TypeError):
        return ""


def flag(value: Any) -> str:
    """Booleano -> 'true'/'false'; None -> vacío."""
    if value is None:
        return ""
    return "true" if value else "false"


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def to_csv(rows: list[dict], columns: list[Column]) -> str:
    """Cabecera + una fila por objeto. Line terminator CRLF (Excel-friendly)."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow([header for header, _ in columns])
    for row in rows:
        src = row if isinstance(row, dict) else {}
        writer.writerow([_cell(extract(src)) for _, extract in columns])
    return buf.getvalue()
