from collectors.celery_app import app
from collectors.hijack_detector import run_once
import asyncio

@app.task(name="collectors.tasks.hijack.run_hijack_scan")
def run_hijack_scan():
    return asyncio.run(run_once())

