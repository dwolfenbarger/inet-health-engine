"""
collectors/tasks/community.py
Celery task wrapper for the community collector cycle.
"""

import asyncio
import time
from collectors.celery_app import app
import structlog

log = structlog.get_logger("task.community")


@app.task(
    name="collectors.tasks.community.run_community_cycle",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    queue="community",
    time_limit=480,
    soft_time_limit=420,
)
def run_community_cycle(self):
    """Celery task: one full community signal collection + correlation cycle."""
    from collectors.community_collector import run_collection_cycle

    log.info("community_task_start", task_id=self.request.id)
    start = time.time()

    try:
        asyncio.run(run_collection_cycle())
        elapsed = round(time.time() - start, 2)
        log.info("community_task_complete", elapsed_s=elapsed)
        return {"status": "ok", "elapsed_s": elapsed}

    except Exception as exc:
        log.error("community_task_error", error=str(exc))
        raise self.retry(exc=exc)
