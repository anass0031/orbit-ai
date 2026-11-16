"""Lakehouse catalogue (Bronze layer this week): datasets, versions, schema and data preview."""
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.config import get_settings
from app.db.session import get_db
from app.lakehouse import catalog
from app.models import Dataset, DatasetVersion, User
from app.schemas.data import DatasetDetail, DatasetOut, DatasetVersionOut

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.get("", response_model=list[DatasetOut])
def list_datasets(layer: Literal["bronze", "silver", "gold"] | None = None, q: str | None = None,
                  db: Session = Depends(get_db), _: User = Depends(require("datasets.read"))):
    stmt = select(Dataset)
    if layer:
        stmt = stmt.where(Dataset.layer == layer)
    if q:
        stmt = stmt.where(Dataset.name.contains(q.lower()))
    return db.scalars(stmt.order_by(Dataset.layer, Dataset.name)).all()


@router.get("/{dataset_id}", response_model=DatasetDetail)
def get_dataset(dataset_id: str, db: Session = Depends(get_db), _: User = Depends(require("datasets.read"))):
    ds = catalog.get_dataset(db, dataset_id)
    versions = db.scalars(select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id)
                          .order_by(DatasetVersion.version.desc())).all()
    out = DatasetDetail.model_validate({**DatasetOut.model_validate(ds).model_dump(by_alias=True),
                                        "versions": [DatasetVersionOut.model_validate(v) for v in versions]})
    return out


@router.get("/{dataset_id}/preview", summary="First rows of a version, read from its Parquet file")
def preview(dataset_id: str, version: int | None = Query(None, ge=1), limit: int | None = Query(None, ge=1, le=500),
            db: Session = Depends(get_db), _: User = Depends(require("datasets.read"))):
    ds = catalog.get_dataset(db, dataset_id)
    dv = catalog.get_version(db, ds, version)
    return catalog.preview(dv, limit or get_settings().preview_rows)
