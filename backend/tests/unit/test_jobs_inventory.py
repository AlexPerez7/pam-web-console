"""app.jobs.inventory — sync nocturno de `account_snapshots` para todos
los tenants Privilege Cloud (worker ARQ). Ver docs/specs/jobs-programados.md.
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.jobs.inventory as jobs_inventory
from app.core.secrets import SecretStoreError
from app.cyberark import CyberArkError
from app.cyberark.auth import AuthError
from app.models import AccountSnapshot, AuditLog, Base, Deployment, Tenant

ACCOUNTS = {
    "globex": [
        {
            "id": "1",
            "platformId": "WinDomain",
            "userName": "a",
            "address": "x",
            "safeName": "S1",
            "modificationTime": 1,
        }
    ],
    "acme": [
        {
            "id": "2",
            "platformId": "WinDomain",
            "userName": "b",
            "address": "y",
            "safeName": "S2",
            "modificationTime": 1,
        }
    ],
}

SECRETS = {"globex": {"client_id": "x"}, "acme": {"client_id": "x"}}


class FakeClient:
    def __init__(self, tenant_id: str, fails: dict) -> None:
        self.tenant_id = tenant_id
        self.fails = fails
        self.closed = False

    async def authenticate(self) -> None:
        if self.fails.get(self.tenant_id) == "auth":
            raise AuthError("credenciales inválidas")

    async def accounts_page(self, offset: int, limit: int):
        if self.fails.get(self.tenant_id) == "cyberark":
            raise CyberArkError("500")
        if self.fails.get(self.tenant_id) == "inesperado":
            # regresión del bug real del 2026-09-23: un error que no es
            # CyberArkError/AuthError/SecretStoreError (ver
            # app/cyberark/client.py accounts_page/safes_page, que hasta
            # esa fecha explotaban así con un body vacío).
            raise AttributeError("'NoneType' object has no attribute 'get'")
        rows = ACCOUNTS.get(self.tenant_id, []) if offset == 0 else []
        return rows, len(rows)

    async def close(self) -> None:
        self.closed = True


@pytest_asyncio.fixture
async def env(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(jobs_inventory, "SessionLocal", maker)

    fails: dict[str, str] = {}

    def fake_get_secret(ref: str) -> dict:
        if ref not in SECRETS:
            raise SecretStoreError(f"no hay secreto para '{ref}'")
        return SECRETS[ref]

    monkeypatch.setattr(jobs_inventory, "get_secret", fake_get_secret)
    monkeypatch.setattr(
        jobs_inventory, "build_client", lambda tenant, secret, **kw: FakeClient(tenant.id, fails)
    )

    async with maker() as session:
        session.add_all(
            [
                Tenant(
                    id="globex",
                    name="Globex",
                    client="K",
                    deployment=Deployment.privilege_cloud,
                    pvwa_url="https://x",
                    secret_ref="globex",
                ),
                Tenant(
                    id="acme",
                    name="Acme",
                    client="R",
                    deployment=Deployment.privilege_cloud,
                    pvwa_url="https://x",
                    secret_ref="acme",
                ),
                Tenant(
                    id="deshabilitado",
                    name="Deshabilitado",
                    client="D",
                    deployment=Deployment.privilege_cloud,
                    pvwa_url="https://x",
                    secret_ref="acme",
                    enabled=False,
                ),
            ]
        )
        await session.commit()

    return {"maker": maker, "fails": fails}


async def test_sync_todos_los_tenants_privilege_cloud_habilitados(env):
    result = await jobs_inventory.sync_all_tenants_accounts({})
    assert result["tenants"] == 2  # "deshabilitado" queda afuera

    async with env["maker"]() as session:
        snaps = (await session.execute(select(AccountSnapshot))).scalars().all()
        assert {s.tenant_id for s in snaps} == {"globex", "acme"}

        logs = (
            (await session.execute(select(AuditLog).where(AuditLog.action == "account.sync")))
            .scalars()
            .all()
        )
        assert len(logs) == 2
        assert all(log.operator == "system" and log.role == "system" for log in logs)
        assert all(log.result == "ok" for log in logs)


async def test_un_tenant_con_auth_caida_no_aborta_el_resto(env):
    env["fails"]["globex"] = "auth"
    result = await jobs_inventory.sync_all_tenants_accounts({})
    assert result["tenants"] == 2

    async with env["maker"]() as session:
        logs = {
            log.tenant_id: log for log in (await session.execute(select(AuditLog))).scalars().all()
        }
        assert logs["globex"].result == "error"
        assert "auth" in logs["globex"].detail["error"]
        assert logs["acme"].result == "ok"

        snaps = (await session.execute(select(AccountSnapshot))).scalars().all()
        assert {s.tenant_id for s in snaps} == {"acme"}


async def test_un_tenant_con_cyberark_caido_audita_error(env):
    env["fails"]["acme"] = "cyberark"
    await jobs_inventory.sync_all_tenants_accounts({})

    async with env["maker"]() as session:
        logs = {
            log.tenant_id: log for log in (await session.execute(select(AuditLog))).scalars().all()
        }
        assert logs["acme"].result == "error"
        assert logs["globex"].result == "ok"


async def test_un_error_no_previsto_en_un_tenant_no_aborta_el_resto(env):
    """Regresión del incidente real del 2026-09-23: un `AttributeError`
    (no una subclase de `CyberArkError`) en `globex` no debe frenar el
    procesamiento de `acme`."""
    env["fails"]["globex"] = "inesperado"
    result = await jobs_inventory.sync_all_tenants_accounts({})
    assert result["tenants"] == 2

    async with env["maker"]() as session:
        logs = {
            log.tenant_id: log for log in (await session.execute(select(AuditLog))).scalars().all()
        }
        assert logs["globex"].result == "error"
        assert "AttributeError" in logs["globex"].detail["error"]
        assert logs["acme"].result == "ok"

        snaps = (await session.execute(select(AccountSnapshot))).scalars().all()
        assert {s.tenant_id for s in snaps} == {"acme"}


async def test_secret_store_caido_audita_error_sin_abrir_cliente(env):
    # "acme-sin-secreto" apunta a un secret_ref que no existe en el
    # store fake -- get_secret explota antes de construir el cliente.
    async with env["maker"]() as session:
        session.add(
            Tenant(
                id="sin-secreto",
                name="Sin secreto",
                client="X",
                deployment=Deployment.privilege_cloud,
                pvwa_url="https://x",
                secret_ref="no-existe",
            )
        )
        await session.commit()

    await jobs_inventory.sync_all_tenants_accounts({})

    async with env["maker"]() as session:
        logs = {
            log.tenant_id: log for log in (await session.execute(select(AuditLog))).scalars().all()
        }
        assert logs["sin-secreto"].result == "error"
        assert "secret store" in logs["sin-secreto"].detail["error"]
