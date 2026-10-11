import enum

from sqlalchemy import Boolean, Enum, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Deployment(str, enum.Enum):
    privilege_cloud = "privilege_cloud"
    self_hosted = "self_hosted"


class Tenant(Base, TimestampMixin):
    """
    Un tenant de CyberArk que PAM Web Console opera. v1 solo admite `privilege_cloud`
    (ver docs/PLAN.md, banner de alcance). El SECRETO del service account NO
    vive acá: `secret_ref` apunta a la entrada en el secret store.
    """

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # slug: "globex"
    name: Mapped[str] = mapped_column(String(200))
    client: Mapped[str] = mapped_column(String(200))
    deployment: Mapped[Deployment] = mapped_column(
        Enum(Deployment), default=Deployment.privilege_cloud
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    pvwa_url: Mapped[str] = mapped_column(String(500))
    identity_tenant_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    connector_management_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sia_connector_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    secret_ref: Mapped[str] = mapped_column(String(200))

    # umbral (días) para Compliance: cuentas con gestión automática
    # deshabilitada y sin rotar hace más de esto cuentan como no-compliant
    # (app.analysis.analyze_accounts/compliance_breakdown). Ajustable por
    # tenant vía PUT /tenants/{id}/compliance-settings — no vive en
    # seed/tenants.json a propósito (config editable en runtime, un
    # seed-tenants no debe pisarla).
    compliance_stale_days: Mapped[int] = mapped_column(Integer, default=30)

    # logo del cliente para el header del reporte ITR (junto al logo del equipo
    # — ver `app.itr.report_builder`, `client_logo_path`). Bytes en la DB, no
    # un archivo en disco: sobrevive a un rebuild del container sin volumen
    # propio, igual que cualquier otra fila. PUT/DELETE /tenants/{id}/logo,
    # sólo platform_admin (branding, no una operación).
    client_logo: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    client_logo_content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)

    verify_ssl: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # id de tenant de CyberArk Remote Access (Alero) -- no es secreto (es
    # el identificador público, va en el iss/sub del JWT de auth junto con
    # el service_account_id). Nulo = el cliente no usa Remote Access, gatea
    # el espacio de nav "Alero". Las credenciales del service account
    # (service_account_id, private_key, discovery_uri) viven en el secret
    # store bajo la clave "alero" del tenant -- ver app/alero/client.py.
    alero_tenant_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
