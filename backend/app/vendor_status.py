"""
Estado público de la plataforma CyberArk (status.cyberark.com) — NO es un
tenant de PAM Web Console ni requiere credenciales: es la página de estado del
FABRICANTE, útil para distinguir "esto anda mal en la config del cliente"
de "CyberArk tiene una incidencia real en su SaaS" antes de escalar un
ticket. Sin auth, sin secret store, sin relación con ningún `Tenant`.

**No es Statuspage.io** (asunción inicial descartada al confirmar que
`/api/v2/summary.json` devuelve 404) — corre sobre **SorryApp**
(sorryapp.com), con su propia API v1 pública, documentada en
`https://status.cyberark.com/api` (confirmado 2026-09-30):

- `GET /api/v1/status`                                   — estado general de la página
- `GET /api/v1/components?filter[parent_id_null]=true`   — productos de nivel raíz (13; el
  listado completo sin filtrar trae 349, con subcomponentes por región/
  instancia — demasiado detalle para un vistazo operativo)
- `GET /api/v1/notices?filter[timeline_state_eq]=current` — incidentes/mantenimientos EN CURSO
- `GET /api/v1/notices?filter[timeline_state_eq]=future`  — mantenimientos programados

`transport` es sólo para tests (`httpx.MockTransport`, sin dependencias
nuevas — no hay `respx` en el proyecto).
"""

from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger("pam.vendor_status")

BASE_URL = "https://status.cyberark.com/api/v1"
_TIMEOUT = 6.0


class VendorStatusError(Exception):
    """No se pudo consultar status.cyberark.com (red, timeout, respuesta inesperada)."""


def _notice_dict(n: dict) -> dict:
    latest = n.get("latest_update") or {}
    return {
        "id": n["id"],
        "type": n["type"],  # "incident" | "planned"
        "state": n["state"],
        "timeline_state": n["timeline_state"],
        "subject": n["subject"],
        "url": n["url"],
        "begins_at": n.get("begins_at"),
        "ends_at": n.get("ends_at"),
        "began_at": n.get("began_at"),
        "ended_at": n.get("ended_at"),
        "latest_update": latest.get("content"),
        "updated_at": n.get("updated_at"),
    }


async def fetch_summary(transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """Arma el resumen: estado general + productos raíz + notices en curso/futuras."""
    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as http:
        try:
            status_r, components_r, current_r, upcoming_r = await asyncio.gather(
                http.get(f"{BASE_URL}/status"),
                http.get(
                    f"{BASE_URL}/components",
                    params={"filter[parent_id_null]": "true", "per_page": 100},
                ),
                http.get(
                    f"{BASE_URL}/notices",
                    params={"filter[timeline_state_eq]": "current", "per_page": 50},
                ),
                http.get(
                    f"{BASE_URL}/notices",
                    params={"filter[timeline_state_eq]": "future", "per_page": 50},
                ),
            )
            for r in (status_r, components_r, current_r, upcoming_r):
                r.raise_for_status()
        except httpx.HTTPError as e:
            raise VendorStatusError(f"no se pudo consultar status.cyberark.com: {e}") from e

    page = status_r.json()["page"]
    components = [
        {"id": c["id"], "name": c["name"], "state": c["state"], "url": c.get("url")}
        for c in sorted(components_r.json()["components"], key=lambda c: c["position"])
    ]
    return {
        "page_state": page["state"],
        "page_state_text": page["state_text"],
        "page_url": page["url"],
        "updated_at": page["updated_at"],
        "components": components,
        "active_notices": [_notice_dict(n) for n in current_r.json()["notices"]],
        "upcoming_notices": [_notice_dict(n) for n in upcoming_r.json()["notices"]],
    }
