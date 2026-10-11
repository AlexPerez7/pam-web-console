from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RefreshToken(Base):
    """
    Refresh token emitido a un operador (uno por rotación). De un solo uso:
    `POST /auth/refresh` marca `used_at` y emite el siguiente de la misma
    `family_id` (una familia = una sesión de login). Presentar uno ya usado
    fuera de la ventana de gracia es señal de robo: se revoca la familia
    completa. Ver docs/specs/done/session-hardening.md.

    Sólo guarda el `jti` (id aleatorio del JWT), nunca el token en sí.
    """

    __tablename__ = "refresh_tokens"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    operator_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("operators.id", ondelete="CASCADE"), index=True
    )
    family_id: Mapped[str] = mapped_column(String(64), index=True)
    # inicio de la sesión (login) -- tope absoluto de duración, no se renueva
    family_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
