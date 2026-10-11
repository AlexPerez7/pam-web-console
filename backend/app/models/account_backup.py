from sqlalchemy import JSON, ForeignKey, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AccountBackup(Base, TimestampMixin):
    """
    Copia de seguridad de una cuenta borrada (Fase 3, `account.delete`):
    metadata completa + la contraseña vigente, cifrada con el keypair propio
    de backups (`app.core.backups`). Se guarda ANTES de borrar la cuenta en
    CyberArk, para poder recrearla (`restore`) si el borrado fue un error.
    Ver docs/specs/account-safe-delete.md.
    """

    __tablename__ = "account_backups"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[str] = mapped_column(String(64))  # id en CyberArk (ya no existe)
    account_name: Mapped[str] = mapped_column(String(300))
    safe_name: Mapped[str] = mapped_column(String(200))
    user_name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    platform_id: Mapped[str] = mapped_column(String(100))
    metadata_json: Mapped[dict] = mapped_column(JSON)  # snapshot completo de get_account
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    deleted_by: Mapped[str] = mapped_column(String(120))  # operator.username
    reason: Mapped[str] = mapped_column(Text)
