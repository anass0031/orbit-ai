"""Dispatch long-running work. The run row lives in PostgreSQL; Celery only carries (kind, run_id).

- ORBIT_TASKS_EAGER=false (default): the job is sent to the Celery worker through Redis.
- ORBIT_TASKS_EAGER=true: the job runs immediately inside the API process (tests, or no worker available).
"""
import importlib
import logging

from app.core.config import get_settings
from app.core.errors import ServiceUnavailable
from app.core.logging import correlation_id_var, get_correlation_id, log_event

logger = logging.getLogger("orbit.dispatch")

# kind -> "module:function" taking (run_id: str)
HANDLERS: dict[str, str] = {
    "pipeline": "app.services.pipelines:execute_pipeline_run",
}


def run_handler(kind: str, run_id: str, correlation_id: str | None = None) -> None:
    token = correlation_id_var.set(correlation_id) if correlation_id else None
    try:
        module, func = HANDLERS[kind].split(":")
        getattr(importlib.import_module(module), func)(run_id)
    finally:
        if token:
            correlation_id_var.reset(token)


def dispatch(kind: str, run_id: str) -> None:
    if kind not in HANDLERS:
        raise ValueError(f"Unknown run kind {kind}")
    cid = get_correlation_id()
    if get_settings().tasks_eager:
        run_handler(kind, run_id, cid)
        return
    from app.worker.celery_app import celery_app

    try:
        celery_app.send_task("orbit.execute", args=[kind, run_id, cid])
    except Exception as exc:  # noqa: BLE001 - broker down
        raise ServiceUnavailable("The job queue (Redis) is unreachable — the run stays queued. Is Redis running?") from exc
    log_event(logger, logging.INFO, "run.dispatched", kind=kind, run_id=run_id)
