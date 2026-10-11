from sqlalchemy import Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.operator import OperatorRole


class RoleAssignment(Base, TimestampMixin):
    """
    Rol efectivo de un operador sobre un tenant (RBAC scopeado, deny-by-default).
    `tenant_id` puede ser un id de tenant o el literal '*' (todos los tenants).
    `platform_admin` global ignora esta tabla (acceso total). Ver
    docs/specs/rbac-por-tenant.md.
    """

    __tablename__ = "role_assignments"
    __table_args__ = (UniqueConstraint("operator_id", "tenant_id", name="uq_role_assignment"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    operator_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("operators.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64))
    role: Mapped[OperatorRole] = mapped_column(Enum(OperatorRole))
