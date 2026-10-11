"""Base de conocimiento de errores (causa/solución) — ver
docs/specs/done/knowledge-base.md. Primer recurso verdaderamente **global** de
PAM Web Console (sin `tenant_id`): usa `require_any_tenant_role` en vez de
`require_tenant_access`. Sin feature-flag — no toca CyberArk, es
documentación propia de PAM Web Console.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.deps import require_any_tenant_role
from app.db import get_session
from app.models import KbArticle, Operator, OperatorRole

router = APIRouter(prefix="/knowledge-base", tags=["knowledge-base"])

_reader = require_any_tenant_role(OperatorRole.viewer)
_writer = require_any_tenant_role(OperatorRole.operator)


def _article_dict(a: KbArticle) -> dict:
    return {
        "id": a.id,
        "category": a.category,
        "title": a.title,
        "error_pattern": a.error_pattern,
        "cause": a.cause,
        "solution": a.solution,
        "source_url": a.source_url,
        "created_by": a.created_by,
        "updated_by": a.updated_by,
        "created_at": a.created_at,
        "updated_at": a.updated_at,
    }


class KbArticleIn(BaseModel):
    category: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=200)
    error_pattern: str = Field(min_length=1, max_length=300)
    cause: str = Field(min_length=1)
    solution: str = Field(min_length=1)
    source_url: str | None = None


@router.get("")
async def list_articles(
    op: Operator = Depends(_reader),
    session: AsyncSession = Depends(get_session),
) -> dict:
    result = await session.execute(select(KbArticle).order_by(KbArticle.id))
    articles = result.scalars().all()
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="kb.list",
        result="ok",
        detail={"count": len(articles)},
    )
    await session.commit()
    return {"articles": [_article_dict(a) for a in articles]}


@router.post("")
async def create_article(
    body: KbArticleIn,
    op: Operator = Depends(_writer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    article = KbArticle(**body.model_dump(), created_by=op.username, updated_by=op.username)
    session.add(article)
    await session.flush()
    article_id = article.id
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="kb.create",
        result="ok",
        target=str(article_id),
        detail={"title": body.title, "category": body.category},
    )
    await session.commit()
    return _article_dict(article)


@router.get("/{article_id}")
async def get_article(
    article_id: int,
    op: Operator = Depends(_reader),
    session: AsyncSession = Depends(get_session),
) -> dict:
    article = await session.get(KbArticle, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="artículo no encontrado")
    return _article_dict(article)


@router.put("/{article_id}")
async def update_article(
    article_id: int,
    body: KbArticleIn,
    op: Operator = Depends(_writer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    article = await session.get(KbArticle, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="artículo no encontrado")
    for field, value in body.model_dump().items():
        setattr(article, field, value)
    article.updated_by = op.username
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="kb.update",
        result="ok",
        target=str(article_id),
        detail={"title": body.title, "category": body.category},
    )
    await session.commit()
    await session.refresh(article)  # updated_at (onupdate=func.now()) queda expirado tras el UPDATE
    return _article_dict(article)


@router.delete("/{article_id}")
async def delete_article(
    article_id: int,
    op: Operator = Depends(_writer),
    session: AsyncSession = Depends(get_session),
) -> dict:
    article = await session.get(KbArticle, article_id)
    if article is None:
        raise HTTPException(status_code=404, detail="artículo no encontrado")
    title = article.title
    await session.delete(article)
    await audit.record(
        session,
        operator=op.username,
        role=op.role.value,
        action="kb.delete",
        result="ok",
        target=str(article_id),
        detail={"title": title},
    )
    await session.commit()
    return {"result": "ok"}
