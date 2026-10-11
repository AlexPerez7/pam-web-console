"""Modo demo (app/demo/): el cliente de CyberArk real, contra el transport mock."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.core.secrets import get_secret
from app.cyberark import NotFound, build_client
from app.demo.data import tenant_data


@pytest.fixture
def demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAM_DEMO_MODE", "true")
    get_settings.cache_clear()


def _tenant(tid: str = "globex") -> SimpleNamespace:
    return SimpleNamespace(
        id=tid,
        deployment="privilege_cloud",
        pvwa_url=f"https://{tid}.privilegecloud.cyberark.cloud",
        identity_tenant_url="https://aaa1001.id.cyberark.cloud",
        connector_management_url=f"https://{tid}.connectormanagement.cyberark.cloud",
        sia_connector_url=f"https://{tid}.dpa.cyberark.cloud",
        verify_ssl=True,
    )


def test_secret_store_no_se_lee_en_modo_demo(demo):
    secret = get_secret("cualquiera")
    assert secret["client_id"].endswith("@pam.demo")
    assert "BEGIN RSA PRIVATE KEY" in secret["alero"]["private_key"]


def test_datos_deterministas_por_tenant():
    a = tenant_data("globex")
    assert len(a.accounts) == 180
    assert tenant_data("globex") is a  # mismo estado en memoria
    assert set(tenant_data("acme").accounts) != set(a.accounts)


async def test_cliente_real_contra_el_transport_demo(demo):
    async with build_client(_tenant(), get_secret("globex")) as client:
        rows, total = await client.accounts_page(limit=5)
        assert total == 180 and len(rows) == 5
        assert all(not k.startswith("_") for k in rows[0])  # campos internos no salen

        acc = await client.get_account(rows[0]["id"])
        assert acc["id"] == rows[0]["id"]
        assert await client.account_activities(acc["id"])

        failures = await client.saved_filter_accounts("PolicyFailures")
        assert failures and all(a["secretManagement"]["status"] == "failure" for a in failures)

        safes, _ = await client.safes_page()
        assert {"WIN-Prod", "BREAK-GLASS"} <= {s["safeName"] for s in safes}
        assert await client.safe_members("WIN-Prod")
        assert await client.list_platforms()
        assert await client.component_details("CPM")
        assert await client.redrock_query("SELECT ID, Name FROM Role")

        with pytest.raises(NotFound):
            await client.get_account("no-existe")


async def test_escrituras_se_aplican_al_estado_en_memoria(demo):
    async with build_client(_tenant("initech"), get_secret("initech")) as client:
        rows, _ = await client.accounts_page(limit=1)
        acc_id = rows[0]["id"]
        await client.update_account(
            acc_id, [{"op": "replace", "path": "/address", "value": "nuevo.host"}]
        )
        assert (await client.get_account(acc_id))["address"] == "nuevo.host"

        await client.delete_account(acc_id)
        with pytest.raises(NotFound):
            await client.get_account(acc_id)


async def test_export_de_plataforma_es_un_zip_parseable(demo):
    from app.domain.platform_config import parse_platform_package

    async with build_client(_tenant(), get_secret("globex")) as client:
        package = await client.export_platform("WinDomain")
    parsed = parse_platform_package(package)
    assert parsed  # .ini + .xml reconocidos
