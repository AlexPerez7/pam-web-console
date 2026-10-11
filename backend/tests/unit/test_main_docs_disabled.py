"""/docs, /redoc y /openapi.json exponen sin auth el mapa completo de la API
-- deben quedar deshabilitados fuera de `environment=dev` (ver
docs/security.md). No usa la fixture `session` real: sólo construye la app
para inspeccionar sus rutas, sin tocar la DB."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


def _build_app(monkeypatch: pytest.MonkeyPatch, environment: str):
    monkeypatch.setenv("PAM_ENVIRONMENT", environment)
    get_settings.cache_clear()
    return create_app()


def test_docs_deshabilitados_fuera_de_dev(monkeypatch):
    app = _build_app(monkeypatch, "prod")
    client = TestClient(app)
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_docs_habilitados_en_dev(monkeypatch):
    app = _build_app(monkeypatch, "dev")
    client = TestClient(app)
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
