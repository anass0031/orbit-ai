"""File import: upload (CSV / Excel / JSON / Parquet) → automatic schema discovery → Bronze ingestion."""
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.config import get_settings
from app.core.errors import ValidationFailed
from app.db.session import get_db
from app.ingestion.parsers import ParseOptions
from app.lakehouse.catalog import suggest_name
from app.models import FileImport, User
from app.schemas.data import DatasetOut, DatasetVersionOut, DiscoverIn, ImportOut, ImportSummary, IngestIn, IngestOut
from app.schemas.identity import Page
from app.services import imports as svc

router = APIRouter(prefix="/imports", tags=["imports"])


def _out(imp: FileImport) -> ImportOut:
    out = ImportOut.model_validate(imp)
    out.suggested_dataset_name = suggest_name(imp.filename)
    return out


@router.post("", response_model=ImportOut, status_code=201,
             summary="Upload a file; it is stored as-is in object storage and its schema is discovered")
async def upload(
    file: UploadFile = File(..., description="CSV, Excel (.xlsx), JSON / JSON Lines or Parquet file"),
    delimiter: str | None = Form(None, max_length=2, description="CSV delimiter; empty = detect automatically"),
    has_header: bool = Form(True),
    encoding: str | None = Form(None, max_length=32),
    sheet_name: str | None = Form(None, max_length=120),
    db: Session = Depends(get_db),
    user: User = Depends(require("imports.write")),
):
    limit = get_settings().upload_max_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise ValidationFailed(f"The file is larger than the {get_settings().upload_max_mb} MB limit",
                               details=[{"field": "file", "message": "file too large"}])
    opts = ParseOptions(delimiter=delimiter or None, has_header=has_header, encoding=encoding or None,
                        sheet_name=sheet_name or None)
    return _out(svc.create_import(db, user, file.filename or "upload", data, opts))


@router.post("/batch", summary="Load several files into Bronze; a file that fails does not stop the others")
async def batch(
    files: list[UploadFile] = File(..., description="Files of any supported format"),
    dataset_name: str | None = Form(None, max_length=63, description="Combine every file into this dataset; empty = one dataset per file"),
    mode: Literal["replace", "append"] = Form("replace"),
    skip_duplicates: bool = Form(True),
    db: Session = Depends(get_db),
    user: User = Depends(require("imports.write")),
):
    if len(files) > 50:
        raise ValidationFailed("At most 50 files at once", details=[{"field": "files", "message": "too many files"}])
    limit = get_settings().upload_max_mb * 1024 * 1024
    loaded = [(f.filename or "upload", await f.read(limit + 1)) for f in files]
    return svc.ingest_batch(db, user, loaded, dataset_name=(dataset_name or "").strip() or None,
                            append=mode == "append" or bool((dataset_name or "").strip()), skip_duplicates=skip_duplicates)


@router.get("", response_model=Page, summary="Import history, newest first")
def list_imports(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), status: str | None = None,
                 db: Session = Depends(get_db), _: User = Depends(require("imports.read"))):
    q = select(FileImport)
    if status:
        q = q.where(FileImport.status == status)
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    rows = db.scalars(q.order_by(FileImport.created_at.desc()).limit(limit).offset(offset)).all()
    return Page(items=[ImportSummary.model_validate(r) for r in rows], total=total or 0, limit=limit, offset=offset)


@router.get("/{import_id}", response_model=ImportOut)
def get_import(import_id: str, db: Session = Depends(get_db), _: User = Depends(require("imports.read"))):
    return _out(svc.get_import(db, import_id))


@router.post("/{import_id}/discover", response_model=ImportOut,
             summary="Run schema discovery again with other options (delimiter, sheet, encoding, header)")
def rediscover(import_id: str, body: DiscoverIn, db: Session = Depends(get_db),
               user: User = Depends(require("imports.write"))):
    imp = svc.get_import(db, import_id)
    opts = ParseOptions(delimiter=body.delimiter or None, has_header=body.has_header,
                        encoding=body.encoding or None, sheet_name=body.sheet_name or None)
    return _out(svc.rediscover(db, user, imp, opts))


@router.post("/{import_id}/ingest", response_model=IngestOut, status_code=201,
             summary="Write the discovered data as a new version of a Bronze dataset (Parquet in MinIO)")
def ingest(import_id: str, body: IngestIn, db: Session = Depends(get_db),
           user: User = Depends(require("imports.write"))):
    imp = svc.get_import(db, import_id)
    ds, dv = svc.ingest(db, user, imp, body.dataset_name.strip(), body.description, append=body.mode == "append")
    return IngestOut(dataset=DatasetOut.model_validate(ds), version=DatasetVersionOut.model_validate(dv))
