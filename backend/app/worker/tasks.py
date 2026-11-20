"""Celery tasks: thin wrappers around service functions (the run state itself lives in PostgreSQL)."""
from app.core.logging import configure_logging
from app.worker.celery_app import celery_app
from app.worker.dispatch import run_handler

configure_logging()


@celery_app.task(name="orbit.execute")
def execute(kind: str, run_id: str, correlation_id: str | None = None) -> str:
    run_handler(kind, run_id, correlation_id)
    return run_id


@celery_app.task(name="orbit.scheduler_tick")
def scheduler_tick() -> dict:
    """Run the pipeline automation pass (schedules, triggers, retries, SLA)."""
    from app.services.scheduler import tick

    return tick()
