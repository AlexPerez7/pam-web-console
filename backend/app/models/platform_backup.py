from sqlalchemy import ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PlatformBackup(Base, TimestampMixin):
    """
    Copia del paquete de exportación de una plataforma borrada
    (Fase 3, `platform.delete`): el `.zip` que devuelve "Export Platform" de
    CyberArk, guardado ANTES de borrar. A diferencia de `AccountBackup`, NO
    se cifra — el paquete es política/config del CPM, no un secreto de
    cliente. Ver docs/specs/platform-lifecycle.md.
    """

    __tablename__ = "platform_backups"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    platform_id: Mapped[str] = mapped_column(String(100))  # PlatformID string (ej. WinDomain)
    numeric_id: Mapped[str] = mapped_column(String(32))  # ID numérico interno de CyberArk
    package: Mapped[bytes] = mapped_column(LargeBinary)
    size_bytes: Mapped[int] = mapped_column(Integer)
    deleted_by: Mapped[str] = mapped_column(String(120))  # operator.username
