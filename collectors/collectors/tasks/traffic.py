"""
collectors/tasks/traffic.py
Celery task wrapper for the traffic collector cycle.
"""

import asyncio
import time
from collectors.celery_app import app
import structlog

log = structlog.get_logger("task.traffic")


@app.task(
    name="collectors.tasks.traffic.run_traffic_cycle",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    queue="traffic",
    time_limit=600,
    soft_time_limit=540,
)
def run_traffic_cycle(self):
    """Celery task: one full traffic + outage collection cycle."""
    from collectors.traffic_collector import run_collection_cycle

    log.info("traffic_task_start", task_id=self.request.id)
    start = time.time()

    try:
        asyncio.run(run_collection_cycle())
        elapsed = round(time.time() - start, 2)
        log.info("traffic_task_complete", elapsed_s=elapsed)
        return {"status": "ok", "elapsed_s": elapsed}

    except Exception as exc:
        log.error("traffic_task_error", error=str(exc))
        raise self.retry(exc=exc)
