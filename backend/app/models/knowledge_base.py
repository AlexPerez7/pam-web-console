from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class KbArticle(Base, TimestampMixin):
    """
    Artículo de la base de conocimiento de errores (causa/solución) — ver
    docs/specs/done/knowledge-base.md. **Global, sin `tenant_id`**: primera
    tabla verdaderamente global de PAM Web Console (todo lo demás, Campaign incluido,
    es por tenant) — el conocimiento de un error del CPM no es de un
    cliente puntual.
    """

    __tablename__ = "kb_articles"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(
        String(40)
    )  # rotation | connection | verification | other
    title: Mapped[str] = mapped_column(String(200))
    error_pattern: Mapped[str] = mapped_column(String(300))
    cause: Mapped[str] = mapped_column(Text)
    solution: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str] = mapped_column(String(120))
    updated_by: Mapped[str] = mapped_column(String(120))
