"""Grabaciones de sesiones PSM YA TERMINADAS (distinto de `app.api.
inventory` `/sessions`, que sólo lista sesiones EN VIVO). Ver
`docs/specs/session-recordings.md`.

Listar + ver actividades: lectura, `viewer+`, sin feature-flag (metadata,
mismo criterio que "listar sesiones"). Descargar el archivo: RBAC
`tenant_admin+` (el techo más alto de PAM Web Console, igual que `account.reveal`) +
feature `recording.play` + `reason` obligatoria — el video puede mostrar
contraseñas tipeadas en pantalla u otro contenido sensible. No pasa por
`writeop.run_write`: no hay dry-run posible, mismo criterio que
`account_reveal` (`app.api.writes`).
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.inventory import _run
from app.core import audit, recording_transcode
from app.core.deps import require_tenant_access
from app.core.tenants import tenant_client
from app.core.writeop import MIN_REASON_LEN
from app.cyberark import Forbidden, NotFound
from app.db import get_session
from app.models import Operator, OperatorRole, Tenant

router = APIRouter(prefix="/tenants/{tenant_id}", tags=["recordings"])

_viewer = require_tenant_access(OperatorRole.viewer)
_tenant_admin = require_tenant_access(OperatorRole.tenant_admin)

# La API no documenta un default de rango para "Get recordings" (a
# diferencia de "Get active sessions") — PAM Web Console lo fija explícito acá en vez
# de confiar en que CyberArk lo asuma solo. Ver docs/specs/
# session-recordings.md.
_DEFAULT_WINDOW_HOURS = 48

_UNSAFE_FILENAME_CHARS = re.compile(r'[\r\n"]')


class RecordingPlayIn(BaseModel):
    reason: str | None = None


@router.get("/recordings")
async def recordings(
    tenant_id: str,
    from_time: int | None = Query(None, description="Unix time; default: hace 48 h"),
    to_time: int | None = Query(None, description="Unix time; default: ahora"),
    safe: str | None = Query(None),
    search: str | None = Query(None),
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    now = datetime.now(UTC)
    to_ts = to_time if to_time is not None else int(now.timestamp())
    from_ts = (
        from_time
        if from_time is not None
        else int((now - timedelta(hours=_DEFAULT_WINDOW_HOURS)).timestamp())
    )
    async with tenant_client(tenant_id, session) as (_, client):
        rows = await _run(
            session,
            op,
            tenant_id,
            "recording.list",
            client.list_recordings(from_ts, to_ts, safe=safe, search=search),
        )
    return {"tenant": tenant_id, "count": len(rows), "recordings": rows}


@router.get("/recordings/{session_id}/activities")
async def recording_activities(
    tenant_id: str,
    session_id: str,
    op: Operator = Depends(_viewer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    async with tenant_client(tenant_id, session) as (_, client):
        rows = await _run(
            session,
            op,
            tenant_id,
            "recording.activities",
            client.get_recording_activities(session_id),
            target=session_id,
        )
    return {"tenant": tenant_id, "count": len(rows), "activities": rows}


async def _gate_and_fetch_avi(
    *,
    tenant_id: str,
    session_id: str,
    body: RecordingPlayIn,
    request: Request,
    op: Operator,
    session: AsyncSession,
    action: str,
) -> bytes:
    """Gating compartido por `.../play` y `.../play/mp4`: feature
    `recording.play` + `reason` obligatoria + `client.play_recording`
    (trae el AVI original). `action` es el nombre de audit action del
    caller (`recording.play` para descargar, `recording.play.mp4` para
    reproducir embebido) — mismo nivel de exposición, acción distinta
    para que el audit trail distinga "vio" de "bajó" (Requisito 5 de
    `docs/specs/recording-player.md`). No pasa por `writeop.run_write`:
    no hay dry-run posible, mismo criterio que `account_reveal`
    (`app.api.writes`). La auditoría "ok" queda a cargo del caller,
    después de que su propio paso final (armar la Response) no falle."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"tenant '{tenant_id}' no existe")

    reason = (body.reason or "").strip()
    if len(reason) < MIN_REASON_LEN:
        raise HTTPException(
            status_code=422,
            detail=f"'reason' es obligatoria (mínimo {MIN_REASON_LEN} caracteres)",
        )

    async with tenant_client(tenant_id, session) as (_, client):
        try:
            return await client.play_recording(session_id)
        except (NotFound, Forbidden) as e:
            await audit.record(
                session,
                operator=op.username,
                role=op.role.value,
                action=action,
                result="error",
                tenant_id=tenant_id,
                target=session_id,
                source_ip=request.client.host if request.client else None,
                detail={"error": str(e)},
            )
            await session.commit()
            raise HTTPException(status_code=403 if isinstance(e, Forbidden) else 404, detail=str(e))


async def _audit_ok(
    session: AsyncSession,
    op: Operator,
    request: Request,
    tenant_id: str,
    session_id: str,
    action: str,
    reason: str,
) -> None:
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action=action,
        result="ok",
        tenant_id=tenant_id,
        target=session_id,
        source_ip=request.client.host if request.client else None,
        detail={"reason": reason},
    )
    await session.commit()


@router.post("/recordings/{session_id}/play")
async def recording_play(
    tenant_id: str,
    session_id: str,
    body: RecordingPlayIn,
    request: Request,
    filename: str | None = Query(None, description="Nombre sugerido (FileName de la fila)"),
    op: Operator = Depends(_tenant_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Descarga el archivo AVI original de la grabación. Gateado por la
    feature `recording.play` (opt-in, apagada por defecto) + RBAC
    `tenant_admin+` — el video puede mostrar contraseñas tipeadas en
    pantalla u otro contenido sensible, mismo nivel que `account.reveal`.
    No hay dry-run posible: descargar ES la acción, y no está auditado el
    contenido."""
    package = await _gate_and_fetch_avi(
        tenant_id=tenant_id,
        session_id=session_id,
        body=body,
        request=request,
        op=op,
        session=session,
        action="recording.play",
    )
    await _audit_ok(
        session, op, request, tenant_id, session_id, "recording.play", (body.reason or "").strip()
    )

    # `play_recording` siempre pide el componente de video (isVideo=true,
    # contenedor AVI confirmado contra Globex) — nombre por defecto con
    # extensión .avi si el frontend no mandó uno (saneado contra
    # inyección de header).
    safe_name = (
        _UNSAFE_FILENAME_CHARS.sub("", filename) if filename else f"recording-{session_id}.avi"
    )
    return Response(
        content=package,
        media_type="application/octet-stream",
        headers={"content-disposition": f'attachment; filename="{safe_name}"'},
    )


@router.post("/recordings/{session_id}/play/mp4")
async def recording_play_mp4(
    tenant_id: str,
    session_id: str,
    body: RecordingPlayIn,
    request: Request,
    filename: str | None = Query(None, description="Nombre sugerido para descargar el MP4"),
    op: Operator = Depends(_tenant_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Reproducción embebida: decodifica el video SCPR y lo transcodifica
    a MP4/H.264 server-side (`app.core.recording_transcode`), para que el
    frontend lo muestre en un `<video>` nativo sin instalar códecs de
    terceros. Mismo gating que `.../play` — ver
    `docs/specs/recording-player.md`. `filename` es opcional: sin `Content-
    Disposition`, el `<video>` embebido no lo necesita; el botón
    "Descargar video (.mp4)" del frontend sí lo manda, para que el
    navegador sugiera un nombre razonable en vez de "mp4" a secas."""
    avi_bytes = await _gate_and_fetch_avi(
        tenant_id=tenant_id,
        session_id=session_id,
        body=body,
        request=request,
        op=op,
        session=session,
        action="recording.play.mp4",
    )

    try:
        mp4_bytes = await asyncio.to_thread(recording_transcode.to_mp4, avi_bytes)
    except recording_transcode.TranscodeError as e:
        await audit.record(
            session,
            operator=op.username,
            role=op.role.value,
            action="recording.play.mp4",
            result="error",
            tenant_id=tenant_id,
            target=session_id,
            source_ip=request.client.host if request.client else None,
            detail={"error": str(e)},
        )
        await session.commit()
        raise HTTPException(status_code=502, detail=str(e))

    await _audit_ok(
        session,
        op,
        request,
        tenant_id,
        session_id,
        "recording.play.mp4",
        (body.reason or "").strip(),
    )
    headers = {}
    if filename:
        safe_name = _UNSAFE_FILENAME_CHARS.sub("", filename)
        headers["content-disposition"] = f'attachment; filename="{safe_name}"'
    return Response(content=mp4_bytes, media_type="video/mp4", headers=headers)
