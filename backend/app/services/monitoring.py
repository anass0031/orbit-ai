"""Run monitoring and the global dashboard, computed from stored metadata only (nothing is estimated)."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.base import utcnow
from app.models import Dataset, FileImport, Pipeline, PipelineRun, QualityIssue
from app.services.quality import dimension_scores
from app.services.runs import aware

STUCK_QUEUED_MIN = 5
STUCK_RUNNING_MIN = 30
LOW_SCORE = 80.0


def run_item(run: PipelineRun, name: str | None) -> dict:
    return {
        "id": run.id, "pipeline_id": run.pipeline_id, "pipeline_name": name or "(deleted)",
        "pipeline_version": run.pipeline_version, "status": run.status, "queued_at": aware(run.queued_at),
        "started_at": aware(run.started_at), "finished_at": aware(run.finished_at), "duration_ms": run.duration_ms,
        "records_in": run.records_in, "records_out": run.records_out, "error_class": run.error_class,
        "error_message": run.error_message, "attempt": run.attempt, "retry_of_id": run.retry_of_id,
        "outputs": run.outputs or [], "triggered_by_id": run.triggered_by_id, "trigger": run.trigger or "manual",
        "params": run.params or {}, "artifacts": len(run.artifacts or []),
    }


def list_runs(db: Session, *, status: str | None = None, pipeline_id: str | None = None, q: str | None = None,
              limit: int = 50, offset: int = 0) -> tuple[list[dict], int]:
    stmt = select(PipelineRun, Pipeline.name).join(Pipeline, Pipeline.id == PipelineRun.pipeline_id, isouter=True)
    if status:
        stmt = stmt.where(PipelineRun.status == status)
    if pipeline_id:
        stmt = stmt.where(PipelineRun.pipeline_id == pipeline_id)
    if q:
        stmt = stmt.where(func.lower(Pipeline.name).contains(q.lower()))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.execute(stmt.order_by(PipelineRun.queued_at.desc()).limit(limit).offset(offset)).all()
    return [run_item(r, n) for r, n in rows], total


def _rate(ok: int, failed: int) -> float | None:
    return round(100 * ok / (ok + failed), 1) if ok + failed else None


def failure_streak(db: Session, pipeline_id: str, scan: int = 10) -> int:
    """Consecutive failed runs at the head of the pipeline's history (cancelled runs are ignored)."""
    statuses = db.scalars(select(PipelineRun.status).where(PipelineRun.pipeline_id == pipeline_id,
                                                           PipelineRun.status.in_(("succeeded", "failed")))
                          .order_by(PipelineRun.queued_at.desc()).limit(scan)).all()
    streak = 0
    for st in statuses:
        if st != "failed":
            break
        streak += 1
    return streak


def run_stats(db: Session, days: int = 7) -> dict:
    since = utcnow() - timedelta(days=days)
    rows = db.execute(select(PipelineRun.status, PipelineRun.duration_ms, PipelineRun.records_out)
                      .where(PipelineRun.queued_at >= since)).all()
    counts = {s: 0 for s in ("succeeded", "failed", "cancelled", "running", "queued")}
    durations, records = [], 0
    for st, dur, rec in rows:
        counts[st] = counts.get(st, 0) + 1
        if st == "succeeded":
            records += rec or 0
            if dur is not None:
                durations.append(dur)
    durations.sort()
    return {
        "days": days, "counts": counts, "total": len(rows),
        "success_rate": _rate(counts["succeeded"], counts["failed"]),
        "avg_duration_ms": round(sum(durations) / len(durations)) if durations else None,
        "p95_duration_ms": durations[min(len(durations) - 1, round(0.95 * (len(durations) - 1)))] if durations else None,
        "records_written": records,
        "running_now": db.scalar(select(func.count()).where(PipelineRun.status == "running")) or 0,
        "queued_now": db.scalar(select(func.count()).where(PipelineRun.status == "queued")) or 0,
    }


def daily_runs(db: Session, days: int = 14) -> list[dict]:
    today = utcnow().date()
    start = today - timedelta(days=days - 1)
    series = {start + timedelta(days=i): {"succeeded": 0, "failed": 0, "cancelled": 0, "records": 0} for i in range(days)}
    since = datetime.combine(start, datetime.min.time()).replace(tzinfo=utcnow().tzinfo)
    for st, ts, rec in db.execute(select(PipelineRun.status, PipelineRun.queued_at, PipelineRun.records_out)
                                  .where(PipelineRun.queued_at >= since)).all():
        day = aware(ts).date()
        if day in series and st in ("succeeded", "failed", "cancelled"):
            series[day][st] += 1
            if st == "succeeded":
                series[day]["records"] += rec or 0
    return [{"date": d.isoformat(), **v} for d, v in series.items()]


def attention(db: Session) -> dict:
    """What someone should look at now: repeated failures, stuck runs, low-quality datasets."""
    now = utcnow()
    recurring = []
    for p in db.scalars(select(Pipeline)):
        streak = failure_streak(db, p.id)
        if streak >= 2:
            last = db.scalar(select(PipelineRun).where(PipelineRun.pipeline_id == p.id, PipelineRun.status == "failed")
                             .order_by(PipelineRun.queued_at.desc()).limit(1))
            recurring.append({"pipeline_id": p.id, "name": p.name, "streak": streak, "last_run_id": last.id,
                              "last_error": (last.error_message or "")[:300]})
    stuck = []
    for run, name in db.execute(select(PipelineRun, Pipeline.name).join(Pipeline, Pipeline.id == PipelineRun.pipeline_id)
                                .where(PipelineRun.status.in_(("queued", "running")))).all():
        if run.status == "queued" and now - aware(run.queued_at) > timedelta(minutes=STUCK_QUEUED_MIN):
            stuck.append({**run_item(run, name), "reason": f"queued for more than {STUCK_QUEUED_MIN} min — is the worker running?"})
        elif run.status == "running" and run.started_at and now - aware(run.started_at) > timedelta(minutes=STUCK_RUNNING_MIN):
            stuck.append({**run_item(run, name), "reason": f"running for more than {STUCK_RUNNING_MIN} min"})
    low = [{"id": d.id, "qualified_name": d.qualified_name, "quality_score": d.quality_score}
           for d in db.scalars(select(Dataset).where(Dataset.quality_score.is_not(None), Dataset.quality_score < LOW_SCORE)
                               .order_by(Dataset.quality_score))]
    return {"recurring_failures": recurring, "stuck_runs": stuck, "low_quality_datasets": low, "low_score_threshold": LOW_SCORE}


def dashboard(db: Session) -> dict:
    by_layer = {layer: {"datasets": 0, "rows": 0, "bytes": 0} for layer in ("bronze", "silver", "gold")}
    scores = []
    for d in db.scalars(select(Dataset).where(Dataset.current_version > 0)):
        e = by_layer.setdefault(d.layer, {"datasets": 0, "rows": 0, "bytes": 0})
        e["datasets"] += 1
        e["rows"] += d.row_count or 0
        e["bytes"] += d.size_bytes or 0
        if d.quality_score is not None:
            scores.append(d.quality_score)
    issues = db.execute(select(QualityIssue.severity, func.count()).where(QualityIssue.status.in_(("open", "acknowledged")))
                        .group_by(QualityIssue.severity)).all()
    open_by_sev = {s: c for s, c in issues}
    pipelines = db.scalars(select(Pipeline)).all()
    imports = dict(db.execute(select(FileImport.status, func.count()).group_by(FileImport.status)).all())
    recent_runs, _ = list_runs(db, limit=6)
    recent_issues = [{"id": i.id, "title": i.title, "severity": i.severity, "status": i.status, "dataset_id": i.dataset_id,
                      "detected_at": aware(i.detected_at)}
                     for i in db.scalars(select(QualityIssue).where(QualityIssue.status.in_(("open", "acknowledged")))
                                         .order_by(QualityIssue.last_seen_at.desc()).limit(6))]
    return {
        "layers": by_layer,
        "imports": {"total": sum(imports.values()), "by_status": imports},
        "pipelines": {"total": len(pipelines), "published": sum(1 for p in pipelines if p.current_version > 0),
                      "failing": sum(1 for p in pipelines if p.last_run_status == "failed")},
        "runs": run_stats(db, 7),
        "daily": daily_runs(db, 14),
        "quality": {
            "score": round(sum(scores) / len(scores), 1) if scores else None,
            "datasets_scored": len(scores),
            "dimensions": dimension_scores(db),
            "open_issues": sum(open_by_sev.values()),
            "open_by_severity": open_by_sev,
        },
        "recent_runs": recent_runs,
        "recent_issues": recent_issues,
        "automation": _automation(db),
        "attention": attention(db),
    }


def _automation(db: Session) -> dict:
    """Scheduled pipelines and the next runs."""
    from app.models import Pipeline
    from app.services.automation import describe, settings_of

    items = []
    for p in db.scalars(select(Pipeline)):
        a = settings_of(p)
        triggers = [k for k, on in (("schedule", a["schedule"]["enabled"]), ("new data", a["on_new_data"]["enabled"]),
                                    ("after pipeline", a["after_pipelines"]["enabled"]), ("API", a["api_trigger"]["enabled"])) if on]
        if triggers:
            items.append({"id": p.id, "name": p.name, "triggers": triggers, "schedule": describe(a["schedule"]),
                          "next_run_at": p.next_run_at, "last_run_status": p.last_run_status})
    items.sort(key=lambda x: (x["next_run_at"] is None, x["next_run_at"] or utcnow()))
    return {"automated": len(items), "scheduled": sum(1 for x in items if "schedule" in x["triggers"]), "next": items[:6]}
