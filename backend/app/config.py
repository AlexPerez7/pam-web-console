from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# largo mínimo aceptable para la clave de firma de JWT (RFC 7518 §3.2 para HS256)
MIN_SECRET_KEY_LEN = 32
_INSECURE_SECRET_KEYS = {"", "dev-only-change-me"}


def secret_key_is_secure(key: str) -> bool:
    return key not in _INSECURE_SECRET_KEYS and len(key) >= MIN_SECRET_KEY_LEN


class Settings(BaseSettings):
    """
    Config de la API. Todas las variables llevan prefijo PAM_ salvo las que
    inyecta docker-compose. Los SECRETOS de tenants NO viven acá: se piden en
    runtime al secret store (sops/age -> OpenBao). Ver docs/PLAN.md §5.3.
    """

    model_config = SettingsConfigDict(env_prefix="PAM_", extra="ignore")

    db_url: str = "postgresql+asyncpg://pam:pam@db:5432/pam"
    redis_url: str = "redis://redis:6379/0"
    environment: str = "dev"

    # Modo demo (app/demo/): la API de CyberArk la responde un transport
    # mock con datos ficticios y no se lee el secret store. Para mostrar la
    # consola sin un tenant real; NUNCA activarlo contra tenants de verdad.
    demo_mode: bool = False

    # logging: nivel del root y formato de salida (auto|json|console).
    # auto -> console en dev, json fuera de dev.
    log_level: str = "INFO"
    log_format: str = "auto"

    # firma de sesiones / JWT de operadores. Override obligatorio en prod
    # (el validador de abajo lo exige si environment != "dev").
    secret_key: str = "dev-only-change-me"  # noqa: S105 — default de dev, bloqueado en prod
    # access token corto (vive sólo en memoria del navegador) + refresh token
    # rotativo de un solo uso en cookie HttpOnly. Ver
    # docs/specs/done/session-hardening.md.
    jwt_ttl_minutes: int = 15
    # inactividad: un refresh token sin usar durante este tiempo vence (la
    # sesión se corta aunque el navegador siga abierto). La UI además
    # desloguea a los 30 min sin actividad de teclado/mouse.
    session_idle_minutes: int = 60
    # duración máxima absoluta de una sesión (desde el login), aunque haya
    # actividad continua -- después hay que volver a entrar con TOTP.
    session_max_hours: int = 12
    # dos pestañas que renuevan a la vez presentan el mismo refresh token:
    # dentro de esta ventana no se trata como robo.
    refresh_reuse_grace_seconds: int = 30
    # cookie del refresh token. `Secure` se puede apagar sólo para tests;
    # los navegadores aceptan cookies Secure en http://localhost (túnel SSH).
    refresh_cookie_name: str = "pam_refresh"
    refresh_cookie_secure: bool = True
    # path como lo ve el NAVEGADOR (nginx publica la API bajo /api)
    refresh_cookie_path: str = "/api/auth"

    # anti-fuerza-bruta de /auth/login: N fallos por (usuario, IP) en la
    # ventana -> 429 hasta que expira. Contador en Redis; si Redis no está,
    # degrada abierto (ver docs/security.md).
    login_max_fails: int = 5
    login_lockout_minutes: int = 15

    # techo global por IP (todo intento, no solo fallos, contra cualquier
    # username) -- corta el escenario que el lockout por (usuario, IP) no
    # cubre: muchos usernames distintos probados desde la misma IP. Ver
    # docs/specs/done/login-hardening.md.
    login_ip_max_attempts: int = 20
    login_ip_window_seconds: int = 900

    # límites de escritura (Fase 2). Rate-limit por (operador, tenant) y
    # "circuit breaker" por cantidad de objetos que tocaría una operación.
    write_max_per_window: int = 30
    write_window_minutes: int = 5
    # 2026-09-29: subido de 25 a 50 a pedido de Alex — el circuit breaker
    # frenaba rotaciones en lote reales (29 cuentas) sin que exista todavía
    # un job async de Fase 5 que las procese por partes; 50 sigue acotado
    # (la concurrencia real a CyberArk la limita `cyberark_max_concurrency`,
    # no esto) y el rate-limit de escritura (`write_max_per_window`) sigue
    # aplicando por separado.
    write_object_cap: int = 50

    # rate-limit general de lecturas (GET), vía middleware -- protege al
    # service account COMPARTIDO de cada tenant de un cliente descontrolado
    # (script, bug de polling). Ver docs/specs/done/api-read-rate-limit.md.
    read_rate_max_per_window: int = 300
    read_rate_window_seconds: int = 60
    # rutas cross-tenant / sin tenant en el path (search, system-health,
    # knowledge-base, etc.) -- más generoso, no comparten un único service
    # account puntual.
    read_rate_global_max_per_window: int = 600

    # aprobación de un segundo operador para las ops de Fase 3 (las que
    # tienen confirmación reforzada). platform_admin ejecuta directo; el
    # resto deja una solicitud pendiente. Ver docs/specs/dual-approval.md.
    dual_approval_enabled: bool = True
    approval_ttl_hours: int = 24

    # concurrencia máxima de requests hacia un mismo tenant de CyberArk
    cyberark_max_concurrency: int = 8

    # tope (segundos) del caché en memoria del token OAuth2 de ISPSS por
    # tenant -- se usa el menor entre esto y el `expires_in` real menos 60 s.
    # 0 = login completo contra Identity en cada request (comportamiento
    # anterior). Ver docs/specs/cyberark-token-cache.md.
    cyberark_token_cache_seconds: int = 600

    # TTL (segundos) del caché en Redis del cálculo del dashboard por tenant
    dashboard_cache_ttl: int = 900

    # TTL (segundos) del caché en Redis del resultado de un reporte
    report_cache_ttl: int = 600

    # TTL (segundos) del caché en Redis de la lista completa de Safes
    # (alimenta /safe-names y /safes/defaults: filtro de Cuentas + wizards de
    # alta). Corto porque una Safe recién creada debe quedar visible pronto.
    safes_cache_ttl: int = 120

    # TTL (segundos) del caché en Redis de /platforms (payload pesado de
    # /API/Platforms; alimenta el tab Plataformas y el paso "Tipo de
    # sistema" del wizard de account.create). Cambian con menos frecuencia
    # que las Safes, TTL algo más largo.
    platforms_cache_ttl: int = 300

    # TTL (segundos) del caché en Redis de la sección Troubleshooting
    # (errores de rotación/verificación del CPM por cuenta). No cambia tan
    # rápido como para necesitar el TTL corto de plataformas/safes.
    troubleshooting_cache_ttl: int = 300

    # TTL (segundos) del caché en Redis de System Health (cross-tenant).
    # Caché propia, no comparte la del Dashboard aunque toquen datos
    # parecidos (mismo criterio que troubleshooting_cache_ttl).
    system_health_cache_ttl: int = 300

    # TTL (segundos) del caché en Redis del estado de status.cyberark.com
    # (app/vendor_status.py) — sitio del FABRICANTE, no de un tenant. Corto
    # a propósito: es información que cambia poco pero que un operador
    # puede querer ver fresca en medio de una incidencia real.
    vendor_status_cache_ttl: int = 300

    # TTL (segundos) del caché en Redis de los datos CRUDOS del reporte ITR
    # (accounts/platforms/safes/etc., antes de analyze_*) — opt-in vía
    # `ItrReportIn.use_cache`, ver app/api/itr.py. Portado de ITR (que cachea
    # a disco, `raw_cache/`, para no repegarle a CyberArk iterando sobre
    # cambios al template). Sin secretos (nunca `retrieve_password`), pero SÍ
    # metadata de cuentas — a diferencia del resto de este módulo, que sólo
    # cachea métricas agregadas. TTL corto y apagado por default a propósito.
    itr_cache_ttl: int = 1800

    # tope de filas de un CSV de onboarding masivo (Fase 4,
    # account.create.bulk) — v1 es síncrono (sin cola de jobs), este cap
    # evita una request eterna. Ver docs/specs/done/account-bulk-onboard.md.
    bulk_create_max_rows: int = 100

    # antigüedad máxima (segundos) de la copia local de cuentas
    # (account_snapshots) antes de disparar una sync completa on-demand.
    # Ver docs/specs/inventory-sync-postgres.md.
    accounts_sync_ttl: int = 300

    # hora local del servidor (0-23) en la que corre el cron nocturno de
    # `app.jobs.inventory.sync_all_tenants_accounts` (worker ARQ). Ver
    # docs/specs/jobs-programados.md.
    inventory_sync_cron_hour: int = 2

    # Reglas de alerta (Fase 5, docs/specs/done/alert-rules.md) — motor
    # genérico corrido por el worker ARQ cada `alerts_check_interval_minutes`.
    # Canal v1: webhook de Teams ("Flujos de trabajo"/Power Automate) — sin
    # `alerts_teams_webhook_url` configurado, el motor sigue evaluando/
    # auditando, sólo no manda el mensaje (ver app/core/notify.py). SMTP
    # queda como canal alternativo, sin usar por ahora (decisión de Alex:
    # no meter el M365 corporativo del MSP en esto).
    alerts_enabled: bool = True
    alerts_check_interval_minutes: int = 15
    # una ApprovalRequest pending hace más de esto sin decidirse -> alerta
    alerts_approval_stale_hours: int = 4
    # una alerta que sigue abierta recuerda cada tantas horas, no en cada
    # corrida del cron (evita spam por un problema que tarda en resolverse).
    # 24 desde el 2026-10-07 (antes 6: unas 44 tarjetas al día por alertas
    # crónicas); el heartbeat diario ya resume cuántas siguen activas.
    alerts_renotify_after_hours: int = 24
    # `component_down` no cuenta una instancia desconectada que PAM Web Console no ve
    # conectada (ni CyberArk registra un login) hace más de tantos días: se
    # asume dada de baja y queda sólo en System Health
    # (docs/specs/alert-delivery.md, req. 7).
    alerts_stale_instance_days: int = 7
    # zona horaria con la que se muestran las ventanas de mantenimiento en los avisos
    alerts_timezone: str = "America/Santiago"
    # hora local (de `alerts_timezone`) a la que se manda el "heartbeat" diario
    # de las alertas a Teams; -1 lo desactiva. Si deja de llegar, el flujo de
    # Teams o el worker están caídos (docs/specs/alert-delivery.md).
    alerts_heartbeat_hour: int = 9
    # Debounce de `component_down` (docs/specs/alert-delivery.md): se abre
    # recién tras verse en tantas corridas seguidas y se resuelve tras tantas
    # corridas limpias seguidas. 1 = sin debounce. Cada corrida suma
    # `alerts_check_interval_minutes` de demora a la alerta real.
    alerts_confirm_runs: int = 2
    alerts_resolve_runs: int = 2
    # URL base de la UI para armar links en los avisos (ej.
    # "https://192.168.10.10"); vacío = los avisos salen sin link a PAM Web Console
    alerts_ui_base_url: str = ""
    alerts_teams_webhook_url: str = ""
    alerts_smtp_host: str = ""
    alerts_smtp_port: int = 587
    alerts_smtp_user: str = ""
    alerts_smtp_password: str = ""
    alerts_smtp_from: str = ""
    alerts_smtp_use_tls: bool = True
    alerts_email_to: str = ""  # destinatarios separados por coma

    # Secret store (puente Fase 0-1: archivo JSON cifrado con age; luego OpenBao).
    # Ver docs/PLAN.md §5.3. Ambos se montan en el contenedor vía volumen.
    secrets_file: str = "/run/secrets/secrets.age"
    secrets_age_key: str = "/run/secrets/age.key"

    # Backups de cuentas borradas (Fase 3, account.delete). Keypair `age`
    # PROPIO, separado del secret store de credenciales de tenant — comprometer
    # uno no compromete el otro. La pública (recipient) no es secreta, puede
    # ir en `.env`/compose; la privada se monta igual que `secrets_age_key`.
    # Ver docs/specs/account-safe-delete.md.
    backups_age_key: str = "/run/secrets/backups-age.key"
    backups_age_recipient: str = ""

    # Reproducción embebida de grabaciones PSM (SCPR/ScreenPressor -> MP4).
    # `scpr_decode` se compila en la imagen (ver backend/tools/scpr_decode/),
    # `ffmpeg` es un paquete del sistema. Ver docs/specs/recording-player.md.
    scpr_decode_bin: str = "/usr/local/bin/scpr_decode"
    ffmpeg_bin: str = "ffmpeg"
    # Piso del timeout del transcodeo (segundos) — el timeout real usado
    # escala con la duración de la grabación (ver
    # recording_transcode.to_mp4), esto es el mínimo para grabaciones
    # cortas. Bug real (2026-09-17): una grabación de Globex de 20
    # minutos superaba el valor fijo de 120s que había antes (sólo se
    # había probado contra una de 4 minutos hasta ese momento).
    recording_transcode_timeout: float = 120.0
    # Segundos de timeout permitidos por cada segundo REAL de la
    # grabación — el transcodeo es CPU-bound (decode SCPR + encode
    # H.264), no en tiempo real, así que el tiempo que toma escala con la
    # duración de origen, no es un valor fijo.
    recording_transcode_timeout_factor: float = 3.0
    # Techo del timeout (segundos) — nginx (frontend/nginx.conf) corta la
    # conexión a los 600s (proxy_read_timeout) sin importar qué diga
    # PAM Web Console; este techo se queda con margen para que el error de PAM Web Console
    # ("transcodeo superó el timeout de Xs", con motivo real) llegue
    # ANTES que un 504 genérico de nginx sin contexto.
    recording_transcode_timeout_max: float = 540.0

    @model_validator(mode="after")
    def _guard_secret_key(self) -> "Settings":
        if self.environment != "dev" and not secret_key_is_secure(self.secret_key):
            raise ValueError(
                f"PAM_SECRET_KEY inseguro para environment={self.environment!r}: "
                f"defina uno de >= {MIN_SECRET_KEY_LEN} caracteres "
                "(p. ej. `openssl rand -hex 32`)."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
