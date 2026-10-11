"""
Búsqueda cross-tenant. Abanica los tenants accesibles (Privilege Cloud,
habilitados, según RBAC) en paralelo y agrupa los resultados por tenant.
Read-only, auditado por tenant.

Aislamiento: cada tenant usa un CyberArkClient nuevo, creado y cerrado dentro
de su propia tarea. Las llamadas HTTP van en paralelo (`asyncio.gather`); los
`audit.record` se hacen después, en un loop secuencial sobre la única
`AsyncSession` del request, con un solo commit — SQLAlchemy async no es
segura para escrituras concurrentes.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import audit, rbac
from app.core.deps import current_operator
from app.core.secrets import SecretStoreError, get_secret
from app.cyberark import CyberArkClient, CyberArkError, build_client
from app.cyberark.auth import AuthError
from app.db import get_session
from app.models import Deployment, Operator, Tenant

router = APIRouter(prefix="/search", tags=["search"])

_PER_TENANT_CAP = 100

Resource = Literal["accounts", "safes", "platforms", "users", "groups"]

_ACTION = {
    "accounts": "account.search",
    "safes": "safe.search",
    "platforms": "platform.search",
    "users": "user.search",
    "groups": "group.search",
}


async def _fetch(client: CyberArkClient, resource: str, q: str) -> list:
    if resource == "accounts":
        return await client.list_accounts(search=q)
    if resource == "safes":
        return await client.list_safes()
    if resource == "platforms":
        return await client.list_platforms()
    if resource == "groups":
        return await client.list_user_groups()
    return await client.list_users()


def _matches(resource: str, item: dict, ql: str) -> bool:
    if resource == "safes":
        fields = [item.get("safeName"), item.get("description"), item.get("managingCPM")]
    elif resource == "platforms":
        g = item.get("general") or {}
        fields = [g.get("id"), g.get("name"), g.get("systemType")]
    elif resource == "users":
        pd = item.get("personalDetails") or {}
        fields = [
            item.get("username"),
            pd.get("firstName"),
            pd.get("lastName"),
            item.get("userType"),
        ]
    elif resource == "groups":
        fields = [
            item.get("groupName"),
            item.get("description"),
            item.get("directory"),
        ]
    else:
        return True
    return any(ql in str(f).lower() for f in fields if f)


async def _search_one(tenant: Tenant, resource: str, q: str) -> dict:
    """Busca en un tenant. Nunca lanza: {'tenant','rows'} o {'tenant','error'}."""
    max_conc = get_settings().cyberark_max_concurrency
    try:
        secret = await asyncio.to_thread(get_secret, tenant.secret_ref)
    except SecretStoreError as e:
        return {"tenant": tenant, "error": f"secret store: {e}"}

    client = build_client(
        tenant,
        secret,
        max_concurrency=max_conc,
        token_cache_seconds=get_settings().cyberark_token_cache_seconds,
    )
    try:
        await client.authenticate()
        rows = await _fetch(client, resource, q)
    except AuthError as e:
        return {"tenant": tenant, "error": f"auth: {e}"}
    except CyberArkError as e:
        return {"tenant": tenant, "error": str(e)}
    except Exception as e:  # timeout u otros — un tenant no debe romper la búsqueda
        return {"tenant": tenant, "error": f"{type(e).__name__}: {e}"}
    finally:
        await client.close()

    if resource != "accounts":
        ql = q.lower()
        rows = [r for r in rows if isinstance(r, dict) and _matches(resource, r, ql)]
    return {"tenant": tenant, "rows": rows}


@router.get("/{resource}")
async def search(
    resource: Resource,
    q: str = Query(min_length=2, description="término libre"),
    op: Operator = Depends(current_operator),
    session: AsyncSession = Depends(get_session),
) -> dict:
    allowed = await rbac.accessible_tenant_ids(op, session)
    tenants = [
        t
        for t in (
            await session.execute(
                select(Tenant)
                .where(
                    Tenant.enabled.is_(True),
                    Tenant.deployment == Deployment.privilege_cloud,
                )
                .order_by(Tenant.id)
            )
        )
        .scalars()
        .all()
        if allowed is None or t.id in allowed
    ]

    outcomes = await asyncio.gather(*(_search_one(t, resource, q) for t in tenants))
    action = _ACTION[resource]

    results: list[dict] = []
    errors: list[dict] = []
    for o in outcomes:
        t: Tenant = o["tenant"]
        if "error" in o:
            errors.append({"tenant": t.id, "tenant_name": t.name, "error": o["error"]})
            await audit.record(
                session,
                operator=op.username,
                role=op.role.value,
                action=action,
                result="error",
                tenant_id=t.id,
                detail={"q": q, "error": o["error"]},
            )
            continue

        rows = o["rows"]
        returned = rows[:_PER_TENANT_CAP]
        results.append(
            {
                "tenant": t.id,
                "tenant_name": t.name,
                "count": len(rows),
                "returned": len(returned),
                "items": returned,
            }
        )
        await audit.record(
            session,
            operator=op.username,
            role=op.role.value,
            action=action,
            result="ok",
            tenant_id=t.id,
            detail={"q": q, "count": len(rows)},
        )

    await session.commit()
    return {"query": q, "resource": resource, "results": results, "errors": errors}
