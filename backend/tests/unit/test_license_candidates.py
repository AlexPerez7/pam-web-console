"""compute_license_candidates -- columnas Origen y Grupos (Vault), portadas
de License_Candidates_Report.ps1 (no estaban en el port original)."""

from __future__ import annotations

from app.reports.license_candidates import compute_license_candidates


def _vault_user(username: str, source: str = "CyberArk", groups: list[str] | None = None) -> dict:
    return {
        "username": username,
        "userType": "EPVUser",
        "source": source,
        "groupsMembership": [{"groupName": g} for g in (groups or [])],
    }


def _identity_user(username: str, **extra) -> dict:
    return {"Username": username, "UserType": "EPVUser", "Status": "Active", **extra}


def test_origen_prioriza_sourcedslocalized_de_identity():
    vault = [_vault_user("jperez")]
    identity = [_identity_user("jperez", SourceDsLocalized="Active Directory")]
    rows, _ = compute_license_candidates(vault, identity, inactive_days=30)
    assert rows[0]["origen"] == "Active Directory"


def test_origen_mapea_codigos_conocidos_de_sourcedsds():
    vault = [_vault_user("jperez")]
    identity = [_identity_user("jperez", SourceDsLocalized="Unknown", SourceDs="AAD")]
    rows, _ = compute_license_candidates(vault, identity, inactive_days=30)
    assert rows[0]["origen"] == "Microsoft Entra ID"


def test_origen_cae_al_source_del_vault_si_no_hay_registro_en_identity():
    vault = [_vault_user("jperez", source="CyberArk")]
    rows, _ = compute_license_candidates(vault, [], inactive_days=30)
    assert rows[0]["origen"] == "CyberArk"


def test_origen_desconocido_si_no_hay_ninguna_fuente():
    vault = [{"username": "jperez", "userType": "EPVUser"}]
    rows, _ = compute_license_candidates(vault, [], inactive_days=30)
    assert rows[0]["origen"] == "Desconocido"


def test_grupos_vault_junta_los_nombres_de_groupsmembership():
    vault = [_vault_user("jperez", groups=["PAM_Admins", "Safe_Owners"])]
    rows, _ = compute_license_candidates(vault, [], inactive_days=30)
    assert rows[0]["gruposVault"] == "PAM_Admins, Safe_Owners"


def test_grupos_vault_none_si_no_tiene_grupos_o_no_esta_en_el_vault():
    vault = [_vault_user("jperez", groups=[])]
    identity = [_identity_user("solo.identity")]
    rows, _ = compute_license_candidates(vault, identity, inactive_days=30)
    by_user = {r["username"]: r for r in rows}
    assert by_user["jperez"]["gruposVault"] is None
    assert by_user["solo.identity"]["gruposVault"] is None
