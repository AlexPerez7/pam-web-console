"""Contexto por request (ContextVar). Hoy: el request-id para correlacionar
las lineas de log con las filas del audit_log."""

from __future__ import annotations

from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def current_request_id() -> str | None:
    return request_id_var.get()
