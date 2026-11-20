"""Run monitoring: history, details (steps + logs), retry, cancel, and the global dashboard."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require
from app.db.session import get_db
from app.models import Pipeline, User
from app.schemas.identity import Page
from app.schemas.pipelines import RunDetail, RunOut, RunStatus
from app.services import monitoring
from app.services import pipelines as svc

router = APIRouter(tags=["runs"])


@router.get("/runs", response_model=Page, summary="Pipeline runs, newest first")
def list_runs(status: RunStatus | None = None, pipeline_id: str | None = None, q: str | None = None,
              limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
              db: Session = Depends(get_db), _: User = Depends(require("runs.read"))):
    items, total = monitoring.list_runs(db, status=status, pipeline_id=pipeline_id, q=q, limit=limit, offset=offset)
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/runs/stats", summary="Run counters over the last N days")
def stats(days: int = Query(7, ge=1, le=90), db: Session = Depends(get_db), _: User = Depends(require("runs.read"))):
    return {**monitoring.run_stats(db, days), "daily": monitoring.daily_runs(db, min(days, 30)),
            "attention": monitoring.attention(db)}


@router.get("/runs/{run_id}", response_model=RunDetail, summary="Run details: step status, logs, inputs and outputs")
def get_run(run_id: str, db: Session = Depends(get_db), _: User = Depends(require("runs.read"))):
    run = svc.get_run(db, run_id)
    p = db.get(Pipeline, run.pipeline_id)
    return RunDetail.model_validate({**RunOut.model_validate(run).model_dump(), "pipeline_name": p.name if p else None,
                                     "graph_snapshot": run.graph_snapshot, "node_status": run.node_status, "logs": run.logs})


@router.post("/runs/{run_id}/retry", response_model=RunOut, status_code=202, summary="Run the same pipeline version again")
def retry(run_id: str, db: Session = Depends(get_db), user: User = Depends(require("pipelines.run"))):
    return svc.retry(db, user, svc.get_run(db, run_id))


@router.post("/runs/{run_id}/cancel", response_model=RunOut, summary="Stop a queued or running run")
def cancel(run_id: str, db: Session = Depends(get_db), user: User = Depends(require("pipelines.run"))):
    return svc.cancel(db, user, svc.get_run(db, run_id))


@router.get("/dashboard", summary="Global dashboard: lakehouse layers, runs, quality, items needing attention")
def dashboard(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return monitoring.dashboard(db)
