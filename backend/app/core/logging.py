"""Configuracion de logging de la API.

`PAM_LOG_FORMAT`:
  - `json`    -> una linea JSON por evento (default fuera de dev).
  - `console` -> texto legible (default en dev).
  - `auto`    -> lo de arriba segun `PAM_ENVIRONMENT`.

Cada linea lleva el `request_id` del ContextVar (app.core.context) si hay uno,
lo que permite cruzar el log de acceso con la fila del `audit_log`.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.core.context import current_request_id

# campos "extra" que los formatters propagan si el record los trae
_EXTRA_FIELDS = ("method", "path", "status", "duration_ms", "operator", "tenant")


class RequestIdFilter(logging.Filter):
    """Inyecta el request-id actual en cada record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id() or "-"
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = getattr(record, "request_id", "-")
        if rid and rid != "-":
            payload["request_id"] = rid
        for field in _EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


_CONSOLE_FMT = "%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s"


def _resolve_format() -> str:
    s = get_settings()
    if s.log_format in ("json", "console"):
        return s.log_format
    return "console" if s.environment == "dev" else "json"


def configure_logging() -> None:
    """Instala un unico handler en el root logger y alinea uvicorn. Idempotente."""
    settings = get_settings()
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())
    if _resolve_format() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(_CONSOLE_FMT, defaults={"request_id": "-"}))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())

    # uvicorn: que propague a nuestro handler en vez de usar el suyo.
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    # el access log de uvicorn lo reemplaza nuestra linea de acceso (middleware).
    logging.getLogger("uvicorn.access").disabled = True
