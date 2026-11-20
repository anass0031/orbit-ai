"""Lakehouse catalogue: datasets, versions, schema, data preview and documentation."""
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.config import get_settings
from app.db.session import get_db
from app.lakehouse import catalog
from app.models import Dataset, DatasetVersion, User
from app.schemas.data import DatasetDetail, DatasetOut, DatasetVersionOut

router = APIRouter(prefix="/datasets", tags=["datasets"])


def _readable(db: Session, user: User, dataset_id: str) -> Dataset:
    return catalog.get_dataset(db, dataset_id)


@router.get("", response_model=list[DatasetOut])
def list_datasets(layer: Literal["bronze", "silver", "gold"] | None = None, q: str | None = None, tag: str | None = None,
                  db: Session = Depends(get_db), user: User = Depends(require("datasets.read"))):
    stmt = select(Dataset)
    if layer:
        stmt = stmt.where(Dataset.layer == layer)
    if q:
        stmt = stmt.where(Dataset.name.contains(q.lower()))
    if tag:
        return [d for d in db.scalars(stmt.order_by(Dataset.layer, Dataset.name)) if tag in (d.tags or [])]
    return db.scalars(stmt.order_by(Dataset.layer, Dataset.name)).all()


@router.get("/{dataset_id}", response_model=DatasetDetail)
def get_dataset(dataset_id: str, db: Session = Depends(get_db), user: User = Depends(require("datasets.read"))):
    ds = _readable(db, user, dataset_id)
    versions = db.scalars(select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id)
                          .order_by(DatasetVersion.version.desc())).all()
    return DatasetDetail.model_validate({**DatasetOut.model_validate(ds).model_dump(by_alias=True),
                                         "versions": [DatasetVersionOut.model_validate(v) for v in versions]})


@router.get("/{dataset_id}/preview", summary="First rows of a version, read from its Parquet file")
def preview(dataset_id: str, version: int | None = Query(None, ge=1), limit: int | None = Query(None, ge=1, le=500),
            view: Literal["raw", "business"] = "raw", db: Session = Depends(get_db), user: User = Depends(require("datasets.read"))):
    ds = _readable(db, user, dataset_id)
    dv = catalog.get_version(db, ds, version)
    if view == "business":
        from app.services import gold_views

        return gold_views.business_preview(db, ds, dv, limit or get_settings().preview_rows)
    return catalog.preview(dv, limit or get_settings().preview_rows)


@router.get("/{dataset_id}/charts", summary="Charts of a Gold table, grouped by the business names held by its dimensions")
def charts(dataset_id: str, version: int | None = Query(None, ge=1), measure: str | None = Query(None, max_length=63),
           db: Session = Depends(get_db), user: User = Depends(require("datasets.read"))):
    from app.services import gold_views

    ds = _readable(db, user, dataset_id)
    return gold_views.charts(db, ds, catalog.get_version(db, ds, version), measure)


# ── exploration & documentation ──
class DocBody(BaseModel):
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = None
    columns: dict[str, str] | None = None


@router.get("/search/all", summary="Search datasets (name, description, tags, columns, column descriptions) and glossary terms")
def search(q: str = Query(..., min_length=2, max_length=200), db: Session = Depends(get_db), user: User = Depends(require("datasets.read"))):
    from app.services import catalog_docs

    return catalog_docs.search(db, user, q)


@router.get("/{dataset_id}/profile", summary="Column profile: nulls, distinct, min / max / mean, histogram, top values")
def profile(dataset_id: str, version: int | None = Query(None, ge=1), db: Session = Depends(get_db), user: User = Depends(require("datasets.read"))):
    from app.services import catalog_docs

    return catalog_docs.profile(db, user, _readable(db, user, dataset_id), version)


@router.get("/{dataset_id}/diff", summary="Compare two versions: schema changes, rows added / removed / changed")
def diff(dataset_id: str, from_version: int = Query(..., alias="from", ge=1), to_version: int = Query(..., alias="to", ge=1),
         keys: str | None = None, db: Session = Depends(get_db), user: User = Depends(require("datasets.read"))):
    from app.services import catalog_docs

    return catalog_docs.diff(db, user, _readable(db, user, dataset_id), from_version, to_version,
                             [k for k in (keys or "").split(",") if k] or None)


@router.put("/{dataset_id}/documentation", response_model=DatasetOut, summary="Description, tags and column descriptions")
def document(dataset_id: str, body: DocBody, db: Session = Depends(get_db), user: User = Depends(require("catalog.document"))):
    from app.services import catalog_docs

    ds = catalog_docs.document(db, user, _readable(db, user, dataset_id), description=body.description, tags=body.tags, columns=body.columns)
    db.commit()
    return ds


# ── lineage and data model ──
@router.get("/{dataset_id}/lineage", summary="Datasets read to build a version, and datasets produced from this one")
def lineage(dataset_id: str, version: int | None = Query(None, ge=1), db: Session = Depends(get_db), user: User = Depends(require("datasets.read"))):
    ds = _readable(db, user, dataset_id)
    dv = catalog.get_version(db, ds, version) if ds.current_version else None
    produced = []
    for other, cur in db.execute(select(Dataset, DatasetVersion).join(DatasetVersion, DatasetVersion.dataset_id == Dataset.id)
                                 .where(DatasetVersion.version == Dataset.current_version, Dataset.id != ds.id)):
        used = [x["version"] for x in cur.lineage or [] if x["dataset_id"] == ds.id]
        if used:
            produced.append({"dataset_id": other.id, "qualified_name": other.qualified_name, "version": cur.version, "from_version": max(used)})
    return {"dataset": ds.qualified_name, "version": dv.version if dv else None, "sources": (dv.lineage or []) if dv else [],
            "produced": sorted(produced, key=lambda x: x["qualified_name"]), "pipeline_run_id": dv.pipeline_run_id if dv else None,
            "source_import_id": dv.source_import_id if dv else None}


@router.get("/{dataset_id}/model", summary="Place of a Gold dataset in the star schema: role, keys, dimensions and facts")
def data_model(dataset_id: str, db: Session = Depends(get_db), user: User = Depends(require("datasets.read"))):
    ds = _readable(db, user, dataset_id)
    gold = {d.name: d for d in db.scalars(select(Dataset).where(Dataset.layer == "gold"))}
    rels = []
    for r in ds.relationships or []:
        dim = gold.get(r.get("dimension"))
        rels.append({"column": r.get("column"), "dimension": f"gold.{r.get('dimension')}", "dimension_id": dim.id if dim else None,
                     "key": (dim.primary_key or [None])[0] if dim else None})
    used_by = [{"fact": d.qualified_name, "fact_id": d.id, "column": r.get("column")} for d in gold.values() for r in d.relationships or []
               if r.get("dimension") == ds.name and ds.layer == "gold"]
    from app.services.gold_views import links_of

    labels = {x.column: x.label for x in links_of(db, ds)}
    return {"dataset": ds.qualified_name, "role": ds.model_role, "primary_key": ds.primary_key or [], "relationships": rels, "used_by": used_by, "labels": labels}
