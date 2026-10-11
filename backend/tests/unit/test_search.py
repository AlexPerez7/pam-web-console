"""Filtro client-side de la busqueda cross-tenant (app.api.search._matches)."""

from __future__ import annotations

from app.api.search import _matches


def test_matches_groups_por_nombre_descripcion_directorio():
    g = {
        "groupName": "PAM-Admins",
        "description": "Operadores del equipo PAM",
        "directory": "corp.local",
    }
    assert _matches("groups", g, "pam-admins") is True
    assert _matches("groups", g, "equipo pam") is True
    assert _matches("groups", g, "corp.local") is True
    assert _matches("groups", g, "acme") is False


def test_matches_groups_campos_faltantes():
    assert _matches("groups", {"groupName": "Vault Admins"}, "vault") is True
    assert _matches("groups", {}, "algo") is False


def test_matches_accounts_siempre_true():
    # accounts se filtra server-side (search=q), _matches no recorta
    assert _matches("accounts", {}, "loquesea") is True
