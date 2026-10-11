"""Pool de conexión ARQ para encolar jobs on-demand desde la API (Batch 4
de Reportes, `app.jobs.reports.run_heavy_report`) -- distinto del pool que
usa el propio worker (arq lo arma solo a partir de
`WorkerSettings.redis_settings`). Mismo patrón lazy-singleton que
`app.core.cache`. Ver `docs/specs/done/jobs-programados.md`.
"""

from __future__ import annotations

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import get_settings

_pool: ArqRedis | None = None


async def get_arq_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    return _pool
