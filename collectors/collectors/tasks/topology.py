"""collectors/tasks/topology.py — Neo4j topology Celery task"""
import asyncio, time
from collectors.celery_app import app
import structlog
log = structlog.get_logger("task.topology")

@app.task(
    name="collectors.tasks.topology.run_topology_cycle",
    bind=True, max_retries=2, default_retry_delay=60,
    queue="bgp", time_limit=600, soft_time_limit=540,
)
def run_topology_cycle(self):
    from collectors.neo4j_topology import run_collection_cycle
    log.info("topology_task_start", task_id=self.request.id)
    start = time.time()
    try:
        asyncio.run(run_collection_cycle())
        return {"status": "ok", "elapsed_s": round(time.time() - start, 2)}
    except Exception as exc:
        log.error("topology_task_error", error=str(exc))
        raise self.retry(exc=exc)
