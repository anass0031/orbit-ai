"""Scheduler (Celery beat → every 60 s): runs the pipeline automation pass."""
from __future__ import annotations

from app.db.base import utcnow
from app.db.session import SessionLocal


def tick() -> dict:
    # pipelines: schedules, new-data triggers, due retries, queued triggers, backfills, SLA checks
    from app.services import automation

    with SessionLocal() as db:
        pipelines = automation.tick(db, utcnow())
    return {"pipelines": pipelines}
