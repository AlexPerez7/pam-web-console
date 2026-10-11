"""Base de conocimiento de errores (causa/solución) — ver
docs/specs/done/knowledge-base.md. Recurso global (sin tenant_id): RBAC
`require_any_tenant_role`, no `require_tenant_access`.
"""

from __future__ import annotations

import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.knowledge_base import router as kb_router
from app.core import security
from app.core.deps import current_operator
from app.db import get_session
from app.domain.knowledge_base import match_article
from app.models import Deployment, Operator, OperatorRole, RoleAssignment, Tenant

# --- dominio (puro) ------------------------------------------------------ #


def test_match_article_encuentra_por_substring_case_insensitive():
    articles = [
        {"id": 1, "error_pattern": "Access is denied"},
        {"id": 2, "error_pattern": "timeout"},
    ]
    match = match_article("CPM-winRc-5 ACCESS IS DENIED (winRc=5)", articles)
    assert match is not None
    assert match["id"] == 1


def test_match_article_sin_match():
    articles = [{"id": 1, "error_pattern": "Access is denied"}]
    assert match_article("connection refused", articles) is None


def test_match_article_texto_vacio():
    assert match_article("", [{"id": 1, "error_pattern": "x"}]) is None


# --- endpoints ------------------------------------------------------------ #


@pytest_asyncio.fixture
async def env(session):
    session.add(
        Tenant(
            id="lab",
            name="Lab",
            client="A",
            deployment=Deployment.privilege_cloud,
            pvwa_url="https://x",
            secret_ref="lab",
        )
    )
    op = Operator(
        username="op", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    viewer_only = Operator(
        username="v", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    no_tenant = Operator(
        username="notenant", password_hash=security.hash_password("x"), role=OperatorRole.viewer
    )
    session.add_all([op, viewer_only, no_tenant])
    await session.flush()
    session.add_all(
        [
            # "op" tiene operator+ en "lab" -> puede escribir en la KB
            RoleAssignment(operator_id=op.id, tenant_id="lab", role=OperatorRole.operator),
            # "v" solo viewer en "lab" -> puede leer, no escribir
            RoleAssignment(operator_id=viewer_only.id, tenant_id="lab", role=OperatorRole.viewer),
            # "notenant" no tiene ningún RoleAssignment -> sin acceso ni para leer
        ]
    )
    await session.flush()

    app = FastAPI()
    app.include_router(kb_router)

    async def _get_session():
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[current_operator] = lambda: op
    return {
        "app": app,
        "client": TestClient(app),
        "viewer_only": viewer_only,
        "no_tenant": no_tenant,
    }


ARTICLE = {
    "category": "rotation",
    "title": "CPM-winRc-5 Access is denied",
    "error_pattern": "Access is denied",
    "cause": "El service account del CPM no tiene permisos sobre el target.",
    "solution": "Verificar que el usuario de la cuenta tenga los permisos "
    "necesarios en el target Windows.",
    "source_url": "https://community.cyberark.com/s/article/CPM-winRc-5-Access-is-denied",
}


def test_crear_y_listar(env):
    r = env["client"].post("/knowledge-base", json=ARTICLE)
    assert r.status_code == 200
    assert r.json()["created_by"] == "op"

    r2 = env["client"].get("/knowledge-base")
    assert r2.status_code == 200
    assert len(r2.json()["articles"]) == 1


def test_editar(env):
    created = env["client"].post("/knowledge-base", json=ARTICLE).json()
    updated = dict(ARTICLE, title="Nuevo título")
    r = env["client"].put(f"/knowledge-base/{created['id']}", json=updated)
    assert r.status_code == 200
    assert r.json()["title"] == "Nuevo título"
    assert r.json()["updated_by"] == "op"


def test_borrar(env):
    created = env["client"].post("/knowledge-base", json=ARTICLE).json()
    r = env["client"].delete(f"/knowledge-base/{created['id']}")
    assert r.status_code == 200
    r2 = env["client"].get(f"/knowledge-base/{created['id']}")
    assert r2.status_code == 404


def test_rbac_viewer_puede_leer_no_escribir(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["viewer_only"]
    r = env["client"].get("/knowledge-base")
    assert r.status_code == 200
    r2 = env["client"].post("/knowledge-base", json=ARTICLE)
    assert r2.status_code == 403


def test_rbac_sin_ningun_tenant_no_puede_ni_leer(env):
    env["app"].dependency_overrides[current_operator] = lambda: env["no_tenant"]
    r = env["client"].get("/knowledge-base")
    assert r.status_code == 403
