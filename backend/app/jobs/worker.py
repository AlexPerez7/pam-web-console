"""Worker ARQ de PAM Web Console -- mismo código/imagen que `api`, corre con
`command: ["arq", "app.jobs.worker.WorkerSettings"]` (ver
docker-compose.yml, servicio `worker`). Ver docs/specs/jobs-programados.md.
"""

from __future__ import annotations

from typing import Any, ClassVar

from arq import cron, func
from arq.connections import RedisSettings
from arq.cron import CronJob
from arq.typing import WorkerCoroutine
from arq.worker import Function

from app.config import get_settings
from app.core.logging import configure_logging
from app.jobs.alerts import alerts_heartbeat, check_alert_rules
from app.jobs.inventory import sync_all_tenants_accounts
from app.jobs.reports import run_heavy_report


async def on_startup(ctx: dict[str, Any]) -> None:
    configure_logging()


class WorkerSettings:
    # atributos leídos por arq directo de la clase -- no instancia, no
    # tocar la convención aunque ruff (RUF012) prefiera ClassVar/__init__.
    functions: ClassVar[list[WorkerCoroutine | Function]] = [
        sync_all_tenants_accounts,
        check_alert_rules,
        alerts_heartbeat,
        # timeout default de arq (300s) no alcanza con ~2000 cuentas a
        # concurrencia 8 (Batch 4 de Reportes) -- 900s con margen bajo el
        # job_timeout que ya usa recording_transcode (540s) para otra cosa
        # pesada del proyecto.
        func(run_heavy_report, timeout=900),
    ]
    cron_jobs: ClassVar[list[CronJob]] = [
        cron(
            sync_all_tenants_accounts,
            hour=get_settings().inventory_sync_cron_hour,
            minute=0,
        ),
        # "cada N minutos" en arq se expresa como el set de minutos donde
        # dispara (ver docs/specs/done/alert-rules.md) -- PAM_ALERTS_CHECK_INTERVAL_MINUTES=15
        # (default) -> {0, 15, 30, 45}.
        cron(
            check_alert_rules,
            minute=set(range(0, 60, get_settings().alerts_check_interval_minutes)),
        ),
        # cada hora en punto; el job mismo decide si es la hora local del
        # heartbeat (la zona horaria de arq no es la de `alerts_timezone`)
        cron(alerts_heartbeat, minute=0),
    ]
    on_startup = staticmethod(on_startup)
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
