"""
Upsert idempotente del registro de tenants desde backend/seed/tenants.json.

El JSON solo lleva URLs y `secret_ref` (NO credenciales). Las credenciales
viven en el secret store (app.core.secrets). Correr con:
    docker compose exec api python manage.py seed-tenants
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Deployment, Tenant

_SEED_FILE = Path(__file__).resolve().parents[2] / "seed" / "tenants.json"

_FIELDS = (
    "name",
    "client",
    "pvwa_url",
    "identity_tenant_url",
    "connector_management_url",
    "sia_connector_url",
    "alero_tenant_id",
    "secret_ref",
    "verify_ssl",
    "enabled",
)


async def seed_tenants(session: AsyncSession, path: Path | None = None) -> list[str]:
    entries = json.loads((path or _SEED_FILE).read_text(encoding="utf-8"))
    touched: list[str] = []
    for e in entries:
        deployment = Deployment(e.get("deployment", "privilege_cloud"))
        if deployment is not Deployment.privilege_cloud:
            # v1 solo Privilege Cloud (banner de alcance en docs/PLAN.md)
            continue
        existing = (
            await session.execute(select(Tenant).where(Tenant.id == e["id"]))
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                Tenant(
                    id=e["id"],
                    deployment=deployment,
                    **{k: e.get(k) for k in _FIELDS},
                )
            )
            touched.append(f"+ {e['id']}")
        else:
            for k in _FIELDS:
                if k in e:
                    setattr(existing, k, e[k])
            existing.deployment = deployment
            touched.append(f"~ {e['id']}")
    await session.commit()
    return touched
