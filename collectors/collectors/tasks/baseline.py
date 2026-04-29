"""collectors/tasks/baseline.py — Celery task for baseline modeler."""

import asyncio
import time
from collectors.celery_app import app
import structlog

log = structlog.get_logger("task.baseline")


@app.task(
    name="collectors.tasks.baseline.run_baseline_cycle",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    queue="bgp",
    time_limit=120,
    soft_time_limit=100,
)
def run_baseline_cycle(self):
    """Celery task: compute BGP baseline + score current window."""
    from collectors.baseline_modeler import (
        compute_bgp_baseline, compute_anomaly_baseline,
        score_current_window, write_health_snapshot, publish_health_to_redis
    )
    from collectors.db import get_pg_pool

    log.info("baseline_task_start", task_id=self.request.id)
    start = time.time()

    async def _run():
        pool = await get_pg_pool()
        bgp_baseline  = await compute_bgp_baseline(pool)
        anom_baseline = await compute_anomaly_baseline(pool)
        baseline      = {**bgp_baseline, "anomalies": anom_baseline}
        score         = await score_current_window(pool, baseline)
        await write_health_snapshot(pool, score)
        await publish_health_to_redis(score)
        return score

    try:
        result = asyncio.run(_run())
        log.info("baseline_task_complete",
                 health_score=result.get("health_score"),
                 elapsed_s=round(time.time() - start, 2))
        return {"status": "ok", "health_score": result.get("health_score")}
    except Exception as exc:
        log.error("baseline_task_error", error=str(exc))
        raise self.retry(exc=exc)
