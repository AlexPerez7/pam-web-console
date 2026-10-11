from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AccountSnapshot(Base):
    """
    Copia local de metadata de cuentas de un tenant, para poder filtrar por
    columnas que la API Gen2 de CyberArk no filtra server-side (sólo
    soporta `safeName eq` / `modificationTime gt`, ver
    `app.cyberark.client.accounts_filter`). Sólo lectura para la grilla de
    Cuentas — ninguna escritura (`account.update`/`delete`/etc.) lee de
    acá, todas van en vivo contra CyberArk vía `writeop.run_write`. Nunca
    guarda secretos, sólo lo que ya es visible en el listado de cuentas.
    Ver docs/specs/inventory-sync-postgres.md.
    """

    __tablename__ = "account_snapshots"

    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    account_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    platform_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    user_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    safe_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    modification_time: Mapped[int | None] = mapped_column(nullable=True)

    raw: Mapped[dict] = mapped_column(JSON)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_account_snapshots_tenant_platform", "tenant_id", "platform_id"),
        Index("ix_account_snapshots_tenant_user", "tenant_id", "user_name"),
        Index("ix_account_snapshots_tenant_address", "tenant_id", "address"),
    )
