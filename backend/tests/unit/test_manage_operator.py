"""manage.py — los comandos que cambian algo quedan a nombre del operador
de PAM Web Console que los corre (CLAUDE.md, principio 2), no de "system"."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import manage
from app.core import security
from app.models import Base, Operator, OperatorRole


@pytest.fixture
def db(tmp_path, monkeypatch):
    """SQLite en archivo (no in-memory): `_cli_operator` cierra el pool
    después de cada consulta y una base in-memory se perdería."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pam.db'}")
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _schema() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_schema())
    monkeypatch.setattr(manage, "engine", engine)
    monkeypatch.setattr(manage, "SessionLocal", maker)
    monkeypatch.setattr(manage, "_OPERATOR_FLAG", None)
    monkeypatch.delenv("PAM_OPERATOR", raising=False)

    def add(username: str, role: OperatorRole, *, active: bool = True) -> None:
        async def _add() -> None:
            async with maker() as s:
                s.add(
                    Operator(
                        username=username,
                        password_hash=security.hash_password("x" * 12),
                        role=role,
                        is_active=active,
                    )
                )
                await s.commit()
            await engine.dispose()

        asyncio.run(_add())

    return add


def test_atribuye_al_platform_admin_indicado(db, monkeypatch):
    db("aperez", OperatorRole.platform_admin)
    monkeypatch.setattr(manage, "_OPERATOR_FLAG", "aperez")
    assert manage._cli_operator() == ("aperez", "platform_admin")


def test_lo_pregunta_si_hay_terminal(db, monkeypatch):
    db("aperez", OperatorRole.platform_admin)
    monkeypatch.setattr(manage.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "aperez")
    assert manage._cli_operator() == ("aperez", "platform_admin")


def test_sin_terminal_ni_flag_se_niega(db, monkeypatch):
    db("aperez", OperatorRole.platform_admin)
    monkeypatch.setattr(manage.sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit):
        manage._cli_operator()


@pytest.mark.parametrize(
    ("role", "active"),
    [(OperatorRole.tenant_admin, True), (OperatorRole.platform_admin, False)],
)
def test_solo_platform_admin_activo(db, monkeypatch, role, active):
    db("otro", role, active=active)
    monkeypatch.setattr(manage, "_OPERATOR_FLAG", "otro")
    with pytest.raises(SystemExit):
        manage._cli_operator()


def test_operador_inexistente_se_niega(db, monkeypatch):
    db("aperez", OperatorRole.platform_admin)
    monkeypatch.setattr(manage, "_OPERATOR_FLAG", "nadie")
    with pytest.raises(SystemExit):
        manage._cli_operator()


def test_primer_arranque_sin_operadores(db):
    assert manage._cli_operator(bootstrap_ok=True) == ("primer-arranque", "platform_admin")


def test_flag_operador_se_saca_de_argv(monkeypatch):
    monkeypatch.setattr(
        manage.sys, "argv", ["manage.py", "set-active", "--operador", "aperez", "x", "on"]
    )
    assert manage._pop_operator_flag() == "aperez"
    assert manage.sys.argv == ["manage.py", "set-active", "x", "on"]
