"""ETL pipelines: visual editor drafts, validation, preview, publishing, runs, automation and file-drop folders."""
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, File, Header, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, OrbitError, ValidationFailed
from app.db.base import utcnow
from app.db.session import get_db
from app.lakehouse.storage import get_storage
from app.models import Pipeline, PipelineRun, PipelineVersion, User
from app.processing.spec import NODE_TYPES
from app.schemas.pipelines import (
    GraphIn,
    PipelineCreate,
    PipelineDetail,
    PipelineOut,
    PipelineUpdate,
    PipelineVersionOut,
    PreviewIn,
    RunOut,
)
from app.services import automation
from app.services import pipelines as svc
from app.services.audit import audit

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


class Unauthorized(OrbitError):
    status_code = 401
    code = "unauthorized"


class RunBody(BaseModel):
    params: dict = Field(default_factory=dict)


class BackfillBody(BaseModel):
    start: date
    end: date
    step: str = "day"
    params: dict = Field(default_factory=dict)
    continue_on_failure: bool = False


@router.get("/calendar", summary="Past runs and upcoming scheduled runs between two dates (UTC, at most 62 days)")
def run_calendar(start: datetime | None = None, end: datetime | None = None, db: Session = Depends(get_db),
                 _: User = Depends(require("pipelines.read"))):
    start = start or utcnow() - timedelta(days=7)
    return automation.calendar(db, start, end or start + timedelta(days=14))


@router.post("/automation/tick", summary="Run the automation pass now (normally every 60 s): schedules, new data, retries, SLA")
def automation_tick(db: Session = Depends(get_db), _: User = Depends(require("pipelines.run"))):
    return automation.tick(db)


# ── file-drop folders (MinIO prefix dropzone/<folder>/) ──
def _prefix(folder: str) -> str:
    from app.processing.sources import folder_prefix

    try:
        return folder_prefix(folder)
    except ValueError as exc:
        raise ValidationFailed(str(exc), details=[{"field": "folder", "message": str(exc)}]) from exc


@router.get("/dropzone/{folder}", summary="Files waiting in a drop folder")
def dropzone_list(folder: str, _: User = Depends(require("pipelines.read"))):
    return [{"name": f["key"].rsplit("/", 1)[-1], "key": f["key"], "size": f["size"], "modified": f["modified"]}
            for f in get_storage().list_keys(_prefix(folder))]


@router.post("/dropzone/{folder}", status_code=201, summary="Drop a file into a folder (it is read by File-drop inputs)")
async def dropzone_upload(folder: str, file: UploadFile = File(...), db: Session = Depends(get_db),
                          user: User = Depends(require("pipelines.write"))):
    from app.services.imports import safe_filename

    data = await file.read()
    if len(data) > get_settings().upload_max_mb * 1024 * 1024:
        raise ValidationFailed(f"The file is larger than {get_settings().upload_max_mb} MB")
    name = safe_filename(file.filename or "file")
    key = _prefix(folder) + name
    get_storage().put_bytes(key, data)
    audit(db, actor=user, action="pipeline.file_dropped", resource_type="dropzone", resource_id=folder, details={"file": name})
    db.commit()
    return {"name": name, "key": key, "size": len(data)}


@router.delete("/dropzone/{folder}/{name}", status_code=204)
def dropzone_delete(folder: str, name: str, _: User = Depends(require("pipelines.write"))):
    key = _prefix(folder) + name
    if not get_storage().exists(key):
        raise NotFound("File not found")
    get_storage().delete(key)


@router.get("/runs/{run_id}/artifacts/{index}/download", summary="Download a file produced by an Export file node")
def artifact_download(run_id: str, index: int, db: Session = Depends(get_db), _: User = Depends(require("runs.read"))):
    from app.services.destinations import MIME

    run = svc.get_run(db, run_id)
    arts = run.artifacts or []
    if not 0 <= index < len(arts) or arts[index].get("type") != "export_file" or arts[index].get("status") != "succeeded":
        raise NotFound("No exported file at this position")
    d = arts[index]["details"]
    return Response(get_storage().get_bytes(d["key"]), media_type=MIME.get(d["format"], "application/octet-stream"),
                    headers={"Content-Disposition": f'attachment; filename="{d["filename"]}"'})


class BronzeToSilverBody(BaseModel):
    dataset_id: str
    keys: list[str] = Field(min_length=1)
    name: str | None = Field(default=None, max_length=160)
    silver_name: str | None = Field(default=None, max_length=63)


@router.post("/templates/bronze-to-silver", response_model=PipelineOut, status_code=201,
             summary="Create an editable pipeline: clean → deduplicate → fill missing values → quality check → Silver output")
def bronze_to_silver(body: BronzeToSilverBody, db: Session = Depends(get_db), user: User = Depends(require("pipelines.write"))):
    p = svc.bronze_to_silver(db, user, body.dataset_id, body.keys, body.name, body.silver_name)
    db.commit()
    return p


@router.get("/node-types", summary="Node catalogue of the visual editor")
def node_types(_: User = Depends(require("pipelines.read"))):
    return [{"type": t, "category": c, "min_inputs": mn, "max_inputs": mx, "description": d}
            for t, (c, mn, mx, d) in NODE_TYPES.items()]


@router.get("", response_model=list[PipelineOut])
def list_pipelines(db: Session = Depends(get_db), _: User = Depends(require("pipelines.read"))):
    return db.scalars(select(Pipeline).order_by(Pipeline.updated_at.desc())).all()


@router.post("", response_model=PipelineDetail, status_code=201)
def create(body: PipelineCreate, db: Session = Depends(get_db), user: User = Depends(require("pipelines.write"))):
    p = svc.create_pipeline(db, user, name=body.name.strip(), description=body.description,
                            graph=body.graph.model_dump() if body.graph else None)
    db.commit()
    return p


@router.get("/{pipeline_id}", response_model=PipelineDetail)
def get(pipeline_id: str, db: Session = Depends(get_db), _: User = Depends(require("pipelines.read"))):
    return svc.get_pipeline(db, pipeline_id)


@router.put("/{pipeline_id}", response_model=PipelineDetail, summary="Save the draft (name, description, graph)")
def update(pipeline_id: str, body: PipelineUpdate, db: Session = Depends(get_db),
           _: User = Depends(require("pipelines.write"))):
    p = svc.get_pipeline(db, pipeline_id)
    if body.name and body.name.strip().lower() != p.name.lower():
        if db.scalar(select(Pipeline).where(func.lower(Pipeline.name) == body.name.strip().lower())):
            raise Conflict("A pipeline with this name already exists", details=[{"field": "name", "message": "name already used"}])
        p.name = body.name.strip()
    if body.description is not None:
        p.description = body.description
    if body.graph is not None:
        svc.save_draft(db, p, body.graph.model_dump())
    db.commit()
    return p


@router.delete("/{pipeline_id}", status_code=204)
def delete(pipeline_id: str, db: Session = Depends(get_db), user: User = Depends(require("pipelines.write"))):
    p = svc.get_pipeline(db, pipeline_id)
    if db.scalar(select(PipelineRun).where(PipelineRun.pipeline_id == p.id, PipelineRun.status.in_(("queued", "running")))):
        raise Conflict("Cancel the running run before deleting this pipeline")
    audit(db, actor=user, action="pipeline.delete", resource_type="pipeline", resource_id=p.id, details={"name": p.name})
    db.delete(p)
    db.commit()


@router.post("/validate", summary="Check a graph (structure + dry run in DuckDB) and return each node's output schema")
def validate(body: GraphIn, pipeline_id: str | None = None, db: Session = Depends(get_db),
             user: User = Depends(require("pipelines.read"))):
    return svc.validate(db, body.model_dump(), user.role, db.get(Pipeline, pipeline_id) if pipeline_id else None)


@router.post("/preview", summary="Rows produced by one node, computed on a sample of each input")
def preview(body: PreviewIn, db: Session = Depends(get_db), user: User = Depends(require("pipelines.read"))):
    return svc.preview_node(db, body.graph.model_dump(), body.node_id, body.limit, user.role,
                            db.get(Pipeline, body.pipeline_id) if body.pipeline_id else None, body.params)


@router.post("/{pipeline_id}/publish", response_model=PipelineVersionOut, summary="Freeze the draft as a new immutable version")
def publish(pipeline_id: str, db: Session = Depends(get_db), user: User = Depends(require("pipelines.write"))):
    p = svc.get_pipeline(db, pipeline_id)
    v = svc.publish(db, user, p)
    db.commit()
    return v


@router.get("/{pipeline_id}/versions", response_model=list[PipelineVersionOut])
def versions(pipeline_id: str, db: Session = Depends(get_db), _: User = Depends(require("pipelines.read"))):
    svc.get_pipeline(db, pipeline_id)
    return db.scalars(select(PipelineVersion).where(PipelineVersion.pipeline_id == pipeline_id)
                      .order_by(PipelineVersion.version.desc())).all()


@router.post("/{pipeline_id}/run", response_model=RunOut, status_code=202,
             summary="Publish if needed, then run the pipeline in the background (optionally with parameter values)")
def run(pipeline_id: str, body: RunBody | None = None, db: Session = Depends(get_db), user: User = Depends(require("pipelines.run"))):
    return svc.run_now(db, user, svc.get_pipeline(db, pipeline_id), (body.params if body else None) or None)


# ── automation ──
@router.get("/{pipeline_id}/automation", summary="Schedule, triggers, conditions, retry, time limit, notifications, SLA, parameters")
def get_automation(pipeline_id: str, db: Session = Depends(get_db), _: User = Depends(require("pipelines.read"))):
    return automation.automation_out(db, svc.get_pipeline(db, pipeline_id))


@router.put("/{pipeline_id}/automation")
def put_automation(pipeline_id: str, body: dict, db: Session = Depends(get_db), user: User = Depends(require("pipelines.write"))):
    p = automation.save(db, user, svc.get_pipeline(db, pipeline_id), body)
    db.commit()
    return automation.automation_out(db, p)


@router.post("/{pipeline_id}/trigger-token", summary="Create (or replace) the API trigger token — shown only once")
def new_token(pipeline_id: str, db: Session = Depends(get_db), user: User = Depends(require("pipelines.write"))):
    p = svc.get_pipeline(db, pipeline_id)
    token = automation.token_new(db, user, p)
    db.commit()
    return {"token": token, "url": f"/api/v1/pipelines/{p.id}/trigger", "header": "X-Orbit-Trigger-Token"}


@router.delete("/{pipeline_id}/trigger-token", status_code=204)
def revoke_token(pipeline_id: str, db: Session = Depends(get_db), user: User = Depends(require("pipelines.write"))):
    automation.token_revoke(db, user, svc.get_pipeline(db, pipeline_id))
    db.commit()


@router.post("/{pipeline_id}/trigger", status_code=202, summary="Start a run from another tool: header X-Orbit-Trigger-Token (no login)")
def api_trigger(pipeline_id: str, body: RunBody | None = None, token: str | None = Query(None),
                x_orbit_trigger_token: str | None = Header(None), db: Session = Depends(get_db)):
    p = db.get(Pipeline, pipeline_id)
    if p is None or not automation.token_ok(p, x_orbit_trigger_token or token):
        raise Unauthorized("Invalid or disabled trigger token")
    run = automation.trigger(db, p, "api", params=(body.params if body else None) or None, force=True)
    db.commit()
    if run is None:
        events = (p.state or {}).get("events") or []
        return {"started": False, "reason": events[-1]["message"] if events else "not started"}
    return {"started": True, "run_id": run.id, "status": run.status}


@router.post("/{pipeline_id}/backfill", status_code=202, summary="Replay the pipeline over a past period, one run per day / week / month")
def start_backfill(pipeline_id: str, body: BackfillBody, db: Session = Depends(get_db), user: User = Depends(require("pipelines.run"))):
    p = svc.get_pipeline(db, pipeline_id)
    bf = automation.backfill(db, user, p, start=body.start, end=body.end, step=body.step, params=body.params,
                             continue_on_failure=body.continue_on_failure)
    db.commit()
    return {"backfill": bf, "automation": automation.automation_out(db, p)}


@router.delete("/{pipeline_id}/backfill", status_code=204)
def stop_backfill(pipeline_id: str, db: Session = Depends(get_db), user: User = Depends(require("pipelines.run"))):
    automation.cancel_backfill(db, user, svc.get_pipeline(db, pipeline_id))
    db.commit()
