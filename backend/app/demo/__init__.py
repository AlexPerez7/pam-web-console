"""
Modo demo: la consola completa contra tenants de CyberArk FICTICIOS.

Con `PAM_DEMO_MODE=true` ninguna request sale a internet: el cliente de
CyberArk (y el de Alero) usan un `httpx.MockTransport` que responde la API
REST de Privilege Cloud con datos generados de forma determinista por
tenant (`app/demo/data.py`). El secret store tampoco se lee: cada tenant
recibe credenciales falsas. Todo lo demás — RBAC, audit log, dry-run, jobs,
reportes — corre igual que en producción, así que sirve para mostrar el
producto sin un tenant real.

Las escrituras se aplican sobre un estado en memoria del proceso (se
pierden al reiniciar el contenedor).
"""

from __future__ import annotations

from app.config import get_settings
from app.demo.transport import demo_transport


def is_demo() -> bool:
    return get_settings().demo_mode


def demo_secret() -> dict:
    """Credenciales falsas de un tenant demo (incluye el bloque de Alero)."""
    from app.demo.data import alero_private_key_pem

    return {
        "client_id": "demo-service-user@pam.demo",
        "client_secret": "demo",
        "alero": {
            "service_account_id": "demo-service-account",
            "private_key": alero_private_key_pem(),
            "discovery_uri": "https://auth.alero.io/.well-known/openid-configuration",
        },
    }


__all__ = ["demo_secret", "demo_transport", "is_demo"]
