"""
collectors/tasks/bgp.py
Celery task wrapper for the BGP collector cycle.
Bridges the async collector into Celery's sync task model.
"""

import asyncio
import time
from collectors.celery_app import app
from collectors.config import settings
import structlog

log = structlog.get_logger("task.bgp")


@app.task(
    name="collectors.tasks.bgp.run_bgp_cycle",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    queue="bgp",
    time_limit=600,
    soft_time_limit=540,
)
def run_bgp_cycle(self):
    """
    Celery task: one full BGP collection cycle.
    Runs the async collector in a fresh event loop.
    """
    from collectors.bgp_collector import run_collection_cycle

    log.info("bgp_task_start", task_id=self.request.id)
    start = time.time()

    try:
        now          = int(start)
        window_start = now - settings.bgp_window_seconds
        window_end   = now

        asyncio.run(run_collection_cycle(window_start, window_end))

        elapsed = round(time.time() - start, 2)
        log.info("bgp_task_complete", elapsed_s=elapsed, task_id=self.request.id)
        return {"status": "ok", "elapsed_s": elapsed}

    except Exception as exc:
        log.error("bgp_task_error", error=str(exc), task_id=self.request.id)
        raise self.retry(exc=exc)
