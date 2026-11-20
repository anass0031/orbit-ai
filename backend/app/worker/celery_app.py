"""Celery application.

Worker + scheduler: `celery -A app.worker.celery_app worker -B -l info` (add `--pool=solo` on Windows).
`-B` also runs Celery beat, which triggers the periodic task below.
"""
from celery import Celery

from app.core.config import check_secrets, get_settings

_s = get_settings()
check_secrets(_s)

celery_app = Celery("orbit", broker=_s.redis_url, backend=_s.redis_url, include=["app.worker.tasks"])
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_expires=3600,
    task_acks_late=True,  # a crashed worker leaves the message in Redis for another worker
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    timezone="UTC",
    beat_schedule={
        # pipeline automation: schedules, triggers, retries, SLA
        "scheduler-tick": {"task": "orbit.scheduler_tick", "schedule": 60.0},
    },
)
