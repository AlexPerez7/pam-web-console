"""Lógica de dominio de la base de conocimiento de errores. Puro, sin
I/O. Ver docs/specs/done/knowledge-base.md.
"""

from __future__ import annotations

from typing import Any


def match_article(text: str, articles: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Primer artículo cuyo `error_pattern` aparece (substring simple,
    sin distinguir mayús/minús — no regex) dentro de `text`. `None` si
    ninguno matchea. El orden de `articles` decide cuál gana si hay
    varios matches — se asume ya vienen ordenados (ej. por id) desde la
    consulta."""
    haystack = (text or "").strip().lower()
    if not haystack:
        return None
    for article in articles:
        pattern = (article.get("error_pattern") or "").strip().lower()
        if pattern and pattern in haystack:
            return article
    return None
