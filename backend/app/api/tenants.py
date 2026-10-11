"""Registro de tenants — lectura scopeada por RBAC + config (compliance,
logo). Sin feature-flags de escritura: el único gating de una operación es
el rol RBAC mínimo que exige su endpoint (`require_tenant_access`/
`require_role`)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit, rbac
from app.core.deps import current_operator, require_role, require_tenant_access
from app.db import get_session
from app.models import Operator, OperatorRole, Tenant

router = APIRouter(prefix="/tenants", tags=["tenants"])


def _to_dict(t: Tenant, my_role: str | None = None) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "client": t.client,
        "deployment": t.deployment.value,
        "enabled": t.enabled,
        "pvwa_url": t.pvwa_url,
        "identity_tenant_url": t.identity_tenant_url,
        "alero_tenant_id": t.alero_tenant_id,
        "compliance_stale_days": t.compliance_stale_days,
        "has_logo": t.client_logo is not None,
        # rol EFECTIVO del operador que consulta sobre este tenant — la UI lo
        # usa para mostrar/ocultar acciones de escritura.
        "my_role": my_role,
    }


@router.get("")
async def list_tenants(
    op: Operator = Depends(current_operator),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    allowed = await rbac.accessible_tenant_ids(op, session)
    rows = (await session.execute(select(Tenant).order_by(Tenant.id))).scalars().all()
    out = []
    for t in rows:
        if allowed is not None and t.id not in allowed:
            continue
        role = await rbac.effective_role(op, t.id, session)
        out.append(_to_dict(t, role.value if role else None))
    return out


@router.get("/{tenant_id}")
async def get_tenant(
    tenant_id: str,
    op: Operator = Depends(require_tenant_access()),
    session: AsyncSession = Depends(get_session),
) -> dict:
    t = (await session.execute(select(Tenant).where(Tenant.id == tenant_id))).scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="tenant no encontrado")
    role = await rbac.effective_role(op, tenant_id, session)
    return _to_dict(t, role.value if role else None)


class ComplianceSettingsIn(BaseModel):
    compliance_stale_days: int = Field(ge=1, le=3650)


@router.get("/{tenant_id}/compliance-settings")
async def get_compliance_settings(
    tenant_id: str,
    _: Operator = Depends(require_tenant_access(OperatorRole.tenant_admin)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    t = await session.get(Tenant, tenant_id)
    if t is None:
        raise HTTPException(status_code=404, detail="tenant no encontrado")
    return {"tenant": tenant_id, "compliance_stale_days": t.compliance_stale_days}


@router.put("/{tenant_id}/compliance-settings")
async def put_compliance_settings(
    tenant_id: str,
    body: ComplianceSettingsIn,
    request: Request,
    op: Operator = Depends(require_role(OperatorRole.platform_admin)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Umbral (días) para Compliance: cuentas con la gestión automática
    deshabilitada y sin rotar hace más de esto cuentan como no-compliant
    (ver app.analysis.analyze_accounts). Default 30 (migración 0010) —
    ajustable por cliente si necesita otro. Solo platform_admin, igual que
    /features (es config del tenant, no una operación); auditado."""
    t = await session.get(Tenant, tenant_id)
    if t is None:
        raise HTTPException(status_code=404, detail="tenant no encontrado")

    before = t.compliance_stale_days
    t.compliance_stale_days = body.compliance_stale_days
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="tenant.compliance_settings.update",
        result="ok",
        tenant_id=tenant_id,
        target=tenant_id,
        source_ip=request.client.host if request.client else None,
        detail={"before": before, "after": body.compliance_stale_days},
    )
    await session.commit()
    return {"tenant": tenant_id, "compliance_stale_days": t.compliance_stale_days}


_LOGO_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg"}
_MAX_LOGO_BYTES = 2 * 1024 * 1024  # 2 MB — de sobra para un logo, no un archivo cualquiera


@router.get("/{tenant_id}/logo")
async def get_tenant_logo(
    tenant_id: str,
    _: Operator = Depends(require_tenant_access()),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Sirve el logo del cliente tal cual se subió — usado por la vista
    previa de `pages/Tenants.tsx` y embebido como data URI en el header del
    reporte ITR (`app.itr.report_builder`, `client_logo_path`). Sólo
    branding, sin datos sensibles — RBAC `viewer+` (igual que el resto de
    lectura de metadata del tenant)."""
    t = await session.get(Tenant, tenant_id)
    if t is None or t.client_logo is None:
        raise HTTPException(status_code=404, detail="este tenant no tiene un logo cargado")
    return Response(content=t.client_logo, media_type=t.client_logo_content_type or "image/png")


@router.post("/{tenant_id}/logo")
async def put_tenant_logo(
    tenant_id: str,
    request: Request,
    file: UploadFile = File(...),
    op: Operator = Depends(require_role(OperatorRole.platform_admin)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Sube (o reemplaza) el logo del cliente — aparece junto al de
    el MSP en el header del reporte ITR (`docs/specs/done/itr-report.md`).
    Se guarda como bytes en la fila del tenant, no como archivo en disco
    (sobrevive a un rebuild del container sin volumen propio). Sólo
    `platform_admin` — es branding/config, no una operación sobre
    CyberArk."""
    t = await session.get(Tenant, tenant_id)
    if t is None:
        raise HTTPException(status_code=404, detail="tenant no encontrado")

    content_type = (file.content_type or "").lower()
    if content_type not in _LOGO_CONTENT_TYPES:
        raise HTTPException(
            status_code=422, detail="el logo debe ser PNG o JPEG (image/png, image/jpeg)"
        )
    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="el archivo está vacío")
    if len(content) > _MAX_LOGO_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"el logo supera el máximo permitido ({_MAX_LOGO_BYTES // (1024 * 1024)} MB)",
        )

    t.client_logo = content
    t.client_logo_content_type = content_type
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="tenant.logo.update",
        result="ok",
        tenant_id=tenant_id,
        target=tenant_id,
        source_ip=request.client.host if request.client else None,
        detail={"content_type": content_type, "size_bytes": len(content)},
    )
    await session.commit()
    return {"tenant": tenant_id, "has_logo": True}


@router.delete("/{tenant_id}/logo")
async def delete_tenant_logo(
    tenant_id: str,
    request: Request,
    op: Operator = Depends(require_role(OperatorRole.platform_admin)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    t = await session.get(Tenant, tenant_id)
    if t is None:
        raise HTTPException(status_code=404, detail="tenant no encontrado")

    t.client_logo = None
    t.client_logo_content_type = None
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="tenant.logo.delete",
        result="ok",
        tenant_id=tenant_id,
        target=tenant_id,
        source_ip=request.client.host if request.client else None,
        detail={},
    )
    await session.commit()
    return {"tenant": tenant_id, "has_logo": False}
