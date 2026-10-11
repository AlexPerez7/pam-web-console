"""Fixtures compartidas de la suite.

Los tests unit no tocan Postgres: usan SQLite in-memory (aiosqlite). Lo que
dependa del trigger anti UPDATE/DELETE de la migracion 0002 (PG puro) va a
tests/contract, no aca.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import Base


@pytest.fixture(autouse=True)
def _fixed_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clave de firma estable para los tests de JWT."""
    monkeypatch.setenv("PAM_SECRET_KEY", "test-secret-key-not-for-prod-0123456789abcdef")
    # los tests de cada operación de Fase 3 prueban la operación en sí; la
    # aprobación de un segundo operador se prueba aparte
    # (test_dual_approval.py, que la vuelve a encender).
    monkeypatch.setenv("PAM_DUAL_APPROVAL_ENABLED", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clear_cyberark_token_cache() -> None:
    """La caché de tokens de ISPSS es de módulo: que no se filtre entre tests."""
    from app.cyberark.auth import clear_token_cache

    clear_token_cache()
    yield
    clear_token_cache()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """AsyncSession sobre SQLite in-memory con el esquema creado."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        yield s
    await engine.dispose()
