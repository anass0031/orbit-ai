"""Lifecycle of a long-running run (pipeline runs).

Status transitions: queued → running → succeeded | failed | cancelled.
Log lines are appended to the run row and committed as they happen, so the UI can follow progress live.
"""
from __future__ import annotations

import logging
import time
import traceback
from datetime import datetime

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import get_settings
from app.core.errors import Conflict
from app.core.logging import get_correlation_id, log_event
from app.db.base import TERMINAL_STATUSES, utcnow

logger = logging.getLogger("orbit.runs")


class Cancelled(Exception):
    pass


class RunTracker:
    def __init__(self, db: Session, run, kind: str):
        self.db = db
        self.run = run
        self.kind = kind
        self._t0 = time.perf_counter()

    def log(self, msg: str, level: str = "info", **fields) -> None:
        entry = {"ts": utcnow().isoformat(timespec="seconds"), "level": level, "msg": msg, **fields}
        self.run.logs = [*(self.run.logs or []), entry]
        flag_modified(self.run, "logs")
        log_event(logger, logging.ERROR if level == "error" else logging.INFO, msg, kind=self.kind, run_id=self.run.id)
        self.db.commit()

    def start(self) -> None:
        self.db.refresh(self.run)
        if self.run.cancel_requested or self.run.status == "cancelled":
            raise Cancelled()
        self.run.status = "running"
        self.run.started_at = utcnow()
        self.run.correlation_id = self.run.correlation_id or get_correlation_id()
        self.db.commit()
        self.log(f"Run started (attempt {self.run.attempt})")

    def check_cancel(self) -> None:
        self.db.refresh(self.run, attribute_names=["cancel_requested"])
        if self.run.cancel_requested:
            raise Cancelled()

    def _finish(self, status: str) -> None:
        self.run.status = status
        self.run.finished_at = utcnow()
        self.run.duration_ms = int((time.perf_counter() - self._t0) * 1000)

    def succeed(self, records_in: int | None = None, records_out: int | None = None) -> None:
        self.run.records_in = records_in
        self.run.records_out = records_out
        self._finish("succeeded")
        self.log(f"Run succeeded in {self.run.duration_ms} ms")

    def cancelled(self) -> None:
        self.db.rollback()
        self._finish("cancelled")
        self.log("Run cancelled by a user", level="warning")

    def fail(self, exc: BaseException) -> None:
        self.db.rollback()
        self._finish("failed")
        self.run.error_class = getattr(exc, "code", None) or type(exc).__name__
        self.run.error_message = str(getattr(exc, "message", None) or exc)[:4000]
        self.run.retryable = bool(getattr(exc, "retryable", False))
        self.log(f"Run failed: {self.run.error_message}", level="error", error_class=self.run.error_class,
                 trace=traceback.format_exc(limit=4)[-1500:])


def ensure_retry_allowed(run) -> None:
    if run.status not in ("failed", "cancelled"):
        raise Conflict(f"Only failed or cancelled runs can be retried (this run is {run.status})")
    if run.attempt >= get_settings().max_run_attempts:
        raise Conflict(f"Retry limit reached ({run.attempt}/{get_settings().max_run_attempts} attempts)")


def request_cancel(run) -> None:
    if run.status in TERMINAL_STATUSES:
        raise Conflict(f"This run is already {run.status}")
    run.cancel_requested = True
    if run.status == "queued":  # not picked up yet: cancel immediately
        run.status = "cancelled"
        run.finished_at = utcnow()


def aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; everything in the metadata database is UTC."""
    from datetime import UTC

    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
