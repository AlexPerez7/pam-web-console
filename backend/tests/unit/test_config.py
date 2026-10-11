"""Guard del PAM_SECRET_KEY según environment (R5 de hardening-pre-escritura)."""

from __future__ import annotations

import pytest

from app.config import Settings, secret_key_is_secure


def test_secret_key_is_secure():
    assert secret_key_is_secure("x" * 32) is True
    assert secret_key_is_secure("x" * 31) is False
    assert secret_key_is_secure("dev-only-change-me") is False
    assert secret_key_is_secure("") is False


def test_dev_permite_key_debil(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PAM_ENVIRONMENT", "dev")
    monkeypatch.setenv("PAM_SECRET_KEY", "dev-only-change-me")
    Settings()  # no lanza


def test_prod_rechaza_key_por_defecto(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PAM_ENVIRONMENT", "prod")
    monkeypatch.setenv("PAM_SECRET_KEY", "dev-only-change-me")
    with pytest.raises(ValueError, match="PAM_SECRET_KEY inseguro"):
        Settings()


def test_prod_rechaza_key_corta(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PAM_ENVIRONMENT", "prod")
    monkeypatch.setenv("PAM_SECRET_KEY", "corta")
    with pytest.raises(ValueError):
        Settings()


def test_prod_acepta_key_fuerte(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PAM_ENVIRONMENT", "prod")
    monkeypatch.setenv("PAM_SECRET_KEY", "a" * 48)
    Settings()  # no lanza
