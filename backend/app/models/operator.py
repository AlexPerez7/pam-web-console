import enum

from sqlalchemy import Boolean, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class OperatorRole(str, enum.Enum):
    viewer = "viewer"
    operator = "operator"
    approver = "approver"
    tenant_admin = "tenant_admin"
    platform_admin = "platform_admin"


class Operator(Base, TimestampMixin):
    """
    Usuario de PAM Web Console. Auth local: contraseña Argon2 + TOTP obligatorio. Sin
    AD/SSO externo (ver docs/PLAN.md §5.1). El scope por tenant llega después
    vía role_assignments; por ahora `role` es global.
    """

    __tablename__ = "operators"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(120), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)

    password_hash: Mapped[str] = mapped_column(String(255))
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_enrolled: Mapped[bool] = mapped_column(Boolean, default=False)
    # último time-step TOTP aceptado (unix_time // 30) -- un código ya usado
    # (o uno de una ventana anterior) se rechaza, aunque siga dentro de la
    # tolerancia de reloj de ±1 paso. Ver security.verify_totp_step.
    totp_last_step: Mapped[int | None] = mapped_column(Integer, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    role: Mapped[OperatorRole] = mapped_column(Enum(OperatorRole), default=OperatorRole.viewer)

    # cuando lo pone un platform_admin (alta o reset de contraseña), el
    # operador debe cambiar la contraseña en el próximo login antes de operar.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)

    # se incrementa al cambiar/resetear la contraseña o en /auth/logout --
    # invalida de inmediato todos los JWT emitidos antes (ver security.py,
    # claim "tv"), sin esperar su expiración natural.
    token_version: Mapped[int] = mapped_column(Integer, default=0)
