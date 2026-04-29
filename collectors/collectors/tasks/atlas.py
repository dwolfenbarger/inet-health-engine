"""collectors/tasks/atlas.py — RIPE Atlas Celery task"""
import asyncio, time
from collectors.celery_app import app
import structlog
log = structlog.get_logger("task.atlas")

@app.task(
    name="collectors.tasks.atlas.run_atlas_cycle",
    bind=True, max_retries=3, default_retry_delay=30,
    queue="traffic", time_limit=300, soft_time_limit=270,
)
def run_atlas_cycle(self):
    from collectors.ripe_atlas_collector import run_collection_cycle
    log.info("atlas_task_start", task_id=self.request.id)
    start = time.time()
    try:
        asyncio.run(run_collection_cycle())
        return {"status": "ok", "elapsed_s": round(time.time() - start, 2)}
    except Exception as exc:
        log.error("atlas_task_error", error=str(exc))
        raise self.retry(exc=exc)
