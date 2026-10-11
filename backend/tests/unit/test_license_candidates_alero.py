"""Enriquecimiento del reporte license-candidates con datos de Alero
(fast-follow, ver docs/specs/done/alero-vendors.md)."""

from __future__ import annotations

from app.reports.license_candidates import _LICENSE_TYPES, enrich_ext_users_with_alero

_EXT = _LICENSE_TYPES["ExtUser"]
_STD = _LICENSE_TYPES["EPVUser"]


def _ext_row(username: str, in_identity: bool = True) -> dict:
    return {
        "username": username,
        "licenseType": _EXT,
        "inVault": True,
        "inIdentity": in_identity,
        "status": "Active",
        "lastLogin": 1_700_000_000,
        "daysSinceLogin": 5,
        "candidato": False,
    }


def _std_row(username: str) -> dict:
    return {
        "username": username,
        "licenseType": _STD,
        "inVault": True,
        "inIdentity": True,
        "status": "Active",
        "lastLogin": 1_700_000_000,
        "daysSinceLogin": 5,
        "candidato": False,
    }


def test_no_toca_filas_epvuser():
    rows = [_std_row("jperez")]
    enrich_ext_users_with_alero(rows, alero_vendors=[], alero_last_logins={}, inactive_days=30)
    assert rows[0]["status"] == "Active"
    assert rows[0]["lastLogin"] == 1_700_000_000


def test_reemplaza_status_y_login_de_extuser_con_datos_de_alero():
    rows = [_ext_row("jperez@remote.access.cliente")]
    vendors = [
        {
            "username": "jperez",
            "status": "Activated",
            "expirationStatus": "InTimeframe",
            "idaptiveRoles": "Role A,Role B",
            "groups": "Group X",
        }
    ]
    last_logins = {"jperez": 1_700_500_000_000}  # epoch ms

    enrich_ext_users_with_alero(rows, vendors, last_logins, inactive_days=30)

    row = rows[0]
    assert row["status"] == "Activated"
    assert row["lastLogin"] == 1_700_500_000_000 // 1000
    assert row["aleroExpiration"] == "InTimeframe"
    assert row["aleroRoles"] == "Role A, Role B"
    assert row["aleroGroups"] == "Group X"
    assert row["comentarios"] is None


def test_candidato_si_vendor_esta_deactivated():
    rows = [_ext_row("jperez")]
    vendors = [{"username": "jperez", "status": "Deactivated", "expirationStatus": "InTimeframe"}]
    enrich_ext_users_with_alero(rows, vendors, {"jperez": 1_700_500_000_000}, inactive_days=30)
    assert rows[0]["candidato"] is True


def test_candidato_si_vigencia_de_acceso_no_esta_en_ventana():
    rows = [_ext_row("jperez")]
    vendors = [{"username": "jperez", "status": "Activated", "expirationStatus": "Expired"}]
    enrich_ext_users_with_alero(rows, vendors, {"jperez": 1_700_500_000_000}, inactive_days=30)
    assert rows[0]["candidato"] is True


def test_candidato_si_nunca_hubo_login():
    rows = [_ext_row("jperez")]
    vendors = [{"username": "jperez", "status": "Activated", "expirationStatus": "InTimeframe"}]
    enrich_ext_users_with_alero(rows, vendors, alero_last_logins={}, inactive_days=30)
    assert rows[0]["candidato"] is True
    assert rows[0]["lastLogin"] is None


def test_cuenta_huerfana_sin_identity_ni_alero():
    rows = [_ext_row("borrado", in_identity=False)]
    enrich_ext_users_with_alero(rows, alero_vendors=[], alero_last_logins={}, inactive_days=30)
    assert "huérfana" in rows[0]["comentarios"]


def test_no_encontrado_en_alero_pero_si_en_identity():
    rows = [_ext_row("jperez", in_identity=True)]
    enrich_ext_users_with_alero(rows, alero_vendors=[], alero_last_logins={}, inactive_days=30)
    assert rows[0]["status"] == "No encontrado en Alero"
    assert "revisar manualmente" in rows[0]["comentarios"]


def test_matchea_por_parte_local_del_username_sin_importar_dominio_o_mayusculas():
    rows = [_ext_row("JPerez@Remote.Access.Cliente")]
    vendors = [{"username": "jperez", "status": "Activated", "expirationStatus": "InTimeframe"}]
    enrich_ext_users_with_alero(rows, vendors, {"jperez": 1_700_500_000_000}, inactive_days=30)
    assert rows[0]["status"] == "Activated"
