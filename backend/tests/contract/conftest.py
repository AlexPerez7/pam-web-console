"""Tests de contrato contra un tenant real de CyberArk (docs/PLAN.md 4.4).

Verifican que las operaciones del cliente siguen funcionando como se espera
tras cada release de CyberArk. **No corren en CI** ni en local salvo que
esten las credenciales del tenant de lab en el entorno:

    PAM_CONTRACT_PVWA_URL       https://<sub>.privilegecloud.cyberark.cloud
    PAM_CONTRACT_IDENTITY_URL   https://<tenant>.id.cyberark.cloud
    PAM_CONTRACT_CLIENT_ID      service user (client_credentials)
    PAM_CONTRACT_CLIENT_SECRET  ...

Todo test de esta carpeta lleva el marker `contract` automaticamente y se
skippea si falta alguna de esas variables. Correr solo estos:

    pytest -m contract
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from app.cyberark import CyberArkClient
from app.cyberark.auth import ISPSSAuth

_REQUIRED = (
    "PAM_CONTRACT_PVWA_URL",
    "PAM_CONTRACT_IDENTITY_URL",
    "PAM_CONTRACT_CLIENT_ID",
    "PAM_CONTRACT_CLIENT_SECRET",
)
_MISSING = [v for v in _REQUIRED if not os.getenv(v)]
_CONTRACT_DIR = Path(__file__).parent


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Marca `contract` (y skippea si faltan credenciales) solo a los tests de
    esta carpeta. El hook es global, de ahi el filtro por path."""
    skip = pytest.mark.skip(reason=f"contract: faltan {', '.join(_MISSING)}") if _MISSING else None
    for item in items:
        if _CONTRACT_DIR not in Path(str(item.fspath)).parents:
            continue
        item.add_marker(pytest.mark.contract)
        if skip is not None:
            item.add_marker(skip)


@pytest_asyncio.fixture
async def client() -> AsyncIterator[CyberArkClient]:
    auth = ISPSSAuth(
        identity_tenant_url=os.environ["PAM_CONTRACT_IDENTITY_URL"],
        client_id=os.environ["PAM_CONTRACT_CLIENT_ID"],
        client_secret=os.environ["PAM_CONTRACT_CLIENT_SECRET"],
    )
    c = CyberArkClient(os.environ["PAM_CONTRACT_PVWA_URL"], auth)
    await c.authenticate()
    try:
        yield c
    finally:
        await c.close()
