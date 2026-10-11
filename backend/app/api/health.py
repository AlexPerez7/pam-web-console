import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings, secret_key_is_secure
from app.core.secrets import available as secrets_available
from app.db import get_session

router = APIRouter()


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    db_ok = False
    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    # secrets_available() invoca el binario `age` (bloqueante) -> a un thread
    secrets_ok = await asyncio.to_thread(secrets_available)
    config_ok = secret_key_is_secure(get_settings().secret_key) and secrets_ok
    return {
        "status": "ok" if db_ok else "degraded",
        "db": db_ok,
        "secret_store": secrets_ok,
        "config_ok": config_ok,
    }


@router.get("/version")
async def version() -> dict:
    return {
        "name": "PAM Web Console",
        "component": "api",
        "phase": "0",
        "environment": get_settings().environment,
    }
