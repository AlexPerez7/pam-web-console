"""Rate-limit general de lecturas (GET) por operador -- protege al service
account COMPARTIDO de cada tenant de un cliente descontrolado (script, bug
de polling). Complementa, sin reemplazar, el rate-limit dedicado de
`/auth/login` (`app.api.auth`) y el de escrituras (`app.core.writeop`).

Ver docs/specs/done/api-read-rate-limit.md.
"""

from __future__ import annotations

import re

_TENANT_PATH_RE = re.compile(r"^/tenants/([^/]+)(?:/|$)")

# rutas que nunca deben pasar por este límite: /auth tiene el suyo propio
# (con otra semántica, lockout por fallos), /health lo pega el propio
# HEALTHCHECK de Docker cada 15s, /docs ya está deshabilitado fuera de dev.
EXEMPT_PATH_PREFIXES = ("/auth", "/health", "/docs", "/redoc", "/openapi.json")


def tenant_id_from_path(path: str) -> str | None:
    """`/tenants/{id}/...` -> `{id}`, o `None` si la ruta no es de un
    tenant puntual (cross-tenant o sin tenant)."""
    m = _TENANT_PATH_RE.match(path)
    return m.group(1) if m else None


def is_exempt(method: str, path: str) -> bool:
    """Sólo se limitan lecturas (`GET`); las escrituras ya tienen su propio
    rate-limit (`writeop.run_write`), y `/auth`/`/health`/`/docs` quedan
    afuera por su propia semántica o por no necesitarlo."""
    if method != "GET":
        return True
    return any(path.startswith(p) for p in EXEMPT_PATH_PREFIXES)


def rate_limit_key(operator_id: int, path: str) -> tuple[str, int]:
    """Arma la key de Redis y devuelve `(key, max_per_window)` según si la
    ruta es de un tenant puntual (límite más estricto, key compartida entre
    todos los endpoints de ESE tenant) o cross-tenant (límite más generoso,
    por operador solo)."""
    from app.config import get_settings

    s = get_settings()
    tenant_id = tenant_id_from_path(path)
    if tenant_id:
        return (
            f"pam:read:{operator_id}:{tenant_id}",
            s.read_rate_max_per_window,
        )
    return (f"pam:read:{operator_id}:_global", s.read_rate_global_max_per_window)
