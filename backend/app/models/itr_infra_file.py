from sqlalchemy import ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ItrInfraFile(Base, TimestampMixin):
    """
    Un `HealthCheck_<host>_<timestamp>.json` que deja el `.ps1` que Alex/el
    equipo corre a mano en cada servidor on-prem/IaaS del cliente (estado de
    servicios de CyberArk, CPU/RAM/disco, certificado RDP) — datos que la
    API de Privilege Cloud no expone porque viven en el sistema operativo
    del servidor, no en el tenant SaaS. Alimenta la pestaña Infraestructura
    del reporte ITR (`app.itr.infra_data.load_infra_data`, portado de ITR
    verbatim — sólo sabe leer una CARPETA, así que `app/api/itr.py` vuelca
    estas filas a un directorio temporal antes de llamarlo, mismo criterio
    que el logo del cliente con `client_logo_path`).

    Puede haber varios archivos por tenant (uno por servidor) — no hay
    límite de "uno solo" como el logo. `created_at` (TimestampMixin) hace de
    fecha de carga.
    """

    __tablename__ = "itr_infra_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))  # HealthCheck_<host>_<timestamp>.json
    content: Mapped[bytes] = mapped_column(LargeBinary)
    size_bytes: Mapped[int] = mapped_column(Integer)
    uploaded_by: Mapped[str] = mapped_column(String(120))  # operator.username
