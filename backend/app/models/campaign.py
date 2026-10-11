from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Campaign(Base, TimestampMixin):
    """
    Campaña de rotación/remediación (Fase 4) — un lote nombrado de cuentas
    (típicamente armado desde Troubleshooting) con seguimiento de avance.
    Primera tabla de PAM Web Console que no es audit/backup/config: guarda un
    snapshot de qué cuentas entran, el estado (resuelta/pendiente) se
    calcula en vivo contra CyberArk, no se persiste acá. Ver
    docs/specs/done/rotation-campaigns.md.
    """

    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(120))  # operator.username

    accounts: Mapped[list["CampaignAccount"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan", lazy="selectin"
    )


class CampaignAccount(Base):
    """Un miembro (snapshot) de una campaña — sin FK a nada de CyberArk,
    esa tabla no existe. El estado (resuelta/pendiente/no encontrada) se
    calcula en el momento contra `list_accounts()`."""

    __tablename__ = "campaign_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[str] = mapped_column(String(64))  # id en CyberArk
    account_name: Mapped[str] = mapped_column(String(300))
    safe_name: Mapped[str] = mapped_column(String(200))
    platform_id: Mapped[str] = mapped_column(String(100))

    campaign: Mapped[Campaign] = relationship(back_populates="accounts")
