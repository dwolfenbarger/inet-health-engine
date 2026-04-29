"""collectors/tasks/ris.py — Celery task for RIPE RIS collector."""
import asyncio, time
from collectors.celery_app import app
import structlog

log = structlog.get_logger("task.ris")

@app.task(
    name="collectors.tasks.ris.run_ris_cycle",
    bind=True, max_retries=2, default_retry_delay=30,
    queue="bgp", time_limit=660, soft_time_limit=600,
)
def run_ris_cycle(self):
    from collectors.ripe_ris_collector import run_collection_cycle
    from collectors.config import settings
    log.info("ris_task_start", task_id=self.request.id)
    start = time.time()
    try:
        asyncio.run(run_collection_cycle(settings.bgp_window_seconds))
        elapsed = round(time.time()-start, 2)
        log.info("ris_task_complete", elapsed_s=elapsed)
        return {"status":"ok","elapsed_s":elapsed}
    except Exception as exc:
        log.error("ris_task_error", error=str(exc))
        raise self.retry(exc=exc)
