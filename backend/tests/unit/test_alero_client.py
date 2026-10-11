"""Cliente Alero -- get_vendor_last_login (último login real de un vendor,
calculado desde activities/ApplicationVendorLogin, ver
docs/specs/done/alero-vendors.md)."""

from __future__ import annotations

from app.alero.client import AleroClient


def _client() -> AleroClient:
    return AleroClient(
        tenant_id="t-1",
        service_account_id="sa-1",
        private_key_pem="dummy-pem",
        discovery_uri="https://discovery.example",
    )


def _activity(username: str, time_ms: int) -> dict:
    return {"activityData": {"applicationLoginUsername": username, "time": time_ms}}


async def test_devuelve_el_login_mas_reciente_de_la_primera_ventana_con_match(monkeypatch):
    c = _client()
    calls: list[dict] = []

    async def fake_get(path, params=None):
        calls.append(params)
        # primera ventana (la más reciente): dos eventos del mismo vendor,
        # se queda con el más nuevo.
        return {
            "activities": [
                _activity("jperez@remote.access.cliente", 1000),
                _activity("jperez@remote.access.cliente", 2000),
                _activity("otro.vendor", 3000),
            ]
        }

    monkeypatch.setattr(c, "_get", fake_get)
    out = await c.get_vendor_last_login("jperez", lookback_years=1)
    assert out == 2000
    # se corta en la primera ventana con match -- no seguir troceando hacia atrás.
    assert len(calls) == 1


async def test_matchea_por_parte_local_del_username_sin_importar_dominio_o_mayusculas(
    monkeypatch,
):
    c = _client()

    async def fake_get(path, params=None):
        return {"activities": [_activity("JPerez@Remote.Access.Cliente", 5000)]}

    monkeypatch.setattr(c, "_get", fake_get)
    out = await c.get_vendor_last_login("jperez", lookback_years=1)
    assert out == 5000


async def test_sigue_a_la_ventana_anterior_si_la_mas_reciente_no_tiene_match(monkeypatch):
    c = _client()
    calls: list[dict] = []

    async def fake_get(path, params=None):
        calls.append(params)
        if len(calls) == 1:
            return {"activities": [_activity("otro.vendor", 9000)]}
        return {"activities": [_activity("jperez", 1500)]}

    monkeypatch.setattr(c, "_get", fake_get)
    out = await c.get_vendor_last_login("jperez", lookback_years=1)
    assert out == 1500
    assert len(calls) == 2


async def test_devuelve_none_si_no_hay_ningun_login_en_el_lookback(monkeypatch):
    c = _client()

    async def fake_get(path, params=None):
        return {"activities": []}

    monkeypatch.setattr(c, "_get", fake_get)
    out = await c.get_vendor_last_login("jperez", lookback_years=1)
    assert out is None


async def test_username_vacio_no_llama_a_la_api(monkeypatch):
    c = _client()
    called = False

    async def fake_get(path, params=None):
        nonlocal called
        called = True
        return {"activities": []}

    monkeypatch.setattr(c, "_get", fake_get)
    out = await c.get_vendor_last_login("", lookback_years=1)
    assert out is None
    assert called is False


async def test_get_all_vendor_last_logins_arma_el_maximo_por_vendor_sin_cortar(monkeypatch):
    c = _client()
    calls: list[dict] = []

    async def fake_get(path, params=None):
        calls.append(params)
        if len(calls) == 1:
            # ventana más reciente: sólo jperez.
            return {"activities": [_activity("jperez", 5000)]}
        if len(calls) == 2:
            # ventana anterior: otro vendor, y OTRO evento de jperez más viejo
            # (no debe pisar el valor más nuevo ya visto).
            return {
                "activities": [
                    _activity("mgomez", 4000),
                    _activity("jperez", 1000),
                ]
            }
        return {"activities": []}

    monkeypatch.setattr(c, "_get", fake_get)
    out = await c.get_all_vendor_last_logins(lookback_years=1)
    assert out["jperez"] == 5000
    assert out["mgomez"] == 4000
    # a diferencia de get_vendor_last_login, recorre TODAS las ventanas del
    # lookback (no corta apenas hay un match).
    assert len(calls) > 2
