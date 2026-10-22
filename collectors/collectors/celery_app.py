"""
collectors/celery_app.py
Celery application + beat schedule.
Phase 1 + Phase 2 tasks.
"""

from celery import Celery
from celery.signals import worker_process_shutdown
from collectors.task_loop import close_task_loop

from collectors.config import settings

worker_process_shutdown.connect(close_task_loop, weak=False)

app = Celery(
    "inet-health-engine",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "collectors.tasks.bgp",
        "collectors.tasks.traffic",
        "collectors.tasks.community",
        "collectors.tasks.baseline",
        "collectors.tasks.ris",
        "collectors.tasks.atlas",
        "collectors.tasks.rpki",
        "collectors.tasks.topology",
    ],
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=3600,

    beat_schedule={
        # ── Phase 1 ──────────────────────────────────
        # Live RIPE RIS stream (replaces stub BGP cycle)
        "hijack-scan": {
            "task":     "collectors.tasks.hijack.run_hijack_scan",
            "schedule": 300,
            "options":  {"queue": "bgp"},
        },
        "ris-collect": {
            "task":     "collectors.tasks.ris.run_ris_cycle",
            "schedule": 90,    # every 90s — live stream window
            "options":  {"queue": "bgp"},
        },
        # Legacy stub/PyBGPStream cycle kept but infrequent
        "bgp-collect": {
            "task":     "collectors.tasks.bgp.run_bgp_cycle",
            "schedule": 1800,
            "options":  {"queue": "bgp"},
        },
        "traffic-collect": {
            "task":     "collectors.tasks.traffic.run_traffic_cycle",
            "schedule": 300,
            "options":  {"queue": "traffic"},
        },
        "community-collect": {
            "task":     "collectors.tasks.community.run_community_cycle",
            "schedule": 300,
            "options":  {"queue": "community"},
        },
        # ── Phase 2 ──────────────────────────────────
        "atlas-collect": {
            "task":     "collectors.tasks.atlas.run_atlas_cycle",
            "schedule": 600,   # Every 10 minutes
            "options":  {"queue": "traffic"},
        },
        "rpki-collect": {
            "task":     "collectors.tasks.rpki.run_rpki_cycle",
            "schedule": 600,   # Every 10 minutes
            "options":  {"queue": "traffic"},
        },
        "topology-build": {
            "task":     "collectors.tasks.topology.run_topology_cycle",
            "schedule": 1800,  # Every 30 minutes
            "options":  {"queue": "bgp"},
        },
    },

    task_queues={
        "bgp":       {"exchange": "bgp",       "routing_key": "bgp"},
        "traffic":   {"exchange": "traffic",   "routing_key": "traffic"},
        "community": {"exchange": "community", "routing_key": "community"},
    },

    task_default_queue="bgp",
)
