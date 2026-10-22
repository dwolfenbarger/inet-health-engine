from collectors.task_loop import run_async
from collectors.celery_app import app
from collectors.hijack_detector import run_once

@app.task(name="collectors.tasks.hijack.run_hijack_scan")
def run_hijack_scan():
    return run_async(run_once())

