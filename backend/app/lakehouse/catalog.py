"""Lakehouse catalogue: immutable, versioned Parquet datasets.

Write protocol (a reader never sees a partial file):
1. each new version gets a NEW, unique object key  `bronze/<name>/v<N>/<version id>.parquet`;
2. the Parquet file is fully uploaded first;
3. only then are the DatasetVersion row and the dataset's current-version pointer committed
   (the unique (dataset, version) constraint rejects a concurrent writer of the same version).
   If the database commit fails, the uploaded object is deleted again (see `discard_version`).
Older versions are never overwritten, so any previous state can be read back.
"""
from __future__ import annotations

import io
import re

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFound, ValidationFailed
from app.db.base import new_id, utcnow
from app.ingestion.schema import diff_schema, json_safe
from app.lakehouse.storage import get_storage
from app.models import Dataset, DatasetVersion, User

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def suggest_name(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0]
    n = re.sub(r"[^a-z0-9_]+", "_", stem.strip().lower()).strip("_")
    if not n or not n[0].isalpha():
        n = f"ds_{n}".rstrip("_")
    return n[:63]


def validate_dataset_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise ValidationFailed(
            "Dataset names must start with a letter and use only lowercase letters, digits and underscores (max 63).",
            details=[{"field": "dataset_name", "message": "invalid dataset name"}],
        )


def to_parquet_bytes(table: pa.Table) -> bytes:
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    return buf.getvalue()


def write_version(db: Session, *, layer: str, name: str, table: pa.Table, schema: list[dict], user: User | None,
                  description: str | None = None, source_import_id: str | None = None) -> tuple[Dataset, DatasetVersion]:
    """Write `table` as the next version of `layer.name`. The caller commits the session."""
    validate_dataset_name(name)
    ds = db.scalar(select(Dataset).where(Dataset.layer == layer, Dataset.name == name))
    if ds is None:
        ds = Dataset(layer=layer, name=name, description=description, owner_id=user.id if user else None,
                     current_version=0)
        db.add(ds)
        db.flush()
    elif description:
        ds.description = description
    previous = ds.schema or []
    version = (ds.current_version or 0) + 1
    version_id = new_id()
    key = f"{layer}/{name}/v{version}/{version_id}.parquet"
    data = to_parquet_bytes(table)
    storage = get_storage()
    storage.put_bytes(key, data)
    try:
        dv = DatasetVersion(id=version_id, dataset_id=ds.id, version=version, location=key, row_count=table.num_rows,
                            size_bytes=len(data), schema=schema,
                            schema_changes=diff_schema(previous, schema) if previous else {},
                            source_import_id=source_import_id, created_by_id=user.id if user else None)
        db.add(dv)
        db.flush()
        ds.schema = schema
        ds.current_version = version
        ds.current_version_id = dv.id
        ds.row_count = table.num_rows
        ds.size_bytes = len(data)
        ds.last_updated_at = utcnow()
        db.flush()
    except Exception:
        storage.delete(key)  # keep storage and catalogue consistent
        raise
    return ds, dv


def discard_version(dv: DatasetVersion) -> None:
    """Remove the file of a version whose catalogue entry could not be committed."""
    get_storage().delete(dv.location)


def get_dataset(db: Session, dataset_id: str) -> Dataset:
    ds = db.get(Dataset, dataset_id)
    if ds is None:
        raise NotFound("Dataset not found")
    return ds


def get_version(db: Session, ds: Dataset, version: int | None) -> DatasetVersion:
    v = version or ds.current_version
    dv = db.scalar(select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id, DatasetVersion.version == v))
    if dv is None:
        raise NotFound(f"Version {v} of {ds.qualified_name} not found")
    return dv


def preview(dv: DatasetVersion, limit: int) -> dict:
    """First `limit` rows of a dataset version, read back from the Parquet file in object storage."""
    pf = pq.ParquetFile(io.BytesIO(get_storage().get_bytes(dv.location)))
    batch = next(pf.iter_batches(batch_size=max(1, limit)), None)
    rows = [] if batch is None else pa.Table.from_batches([batch]).to_pylist()
    return {
        "version": dv.version,
        "columns": [f.name for f in pf.schema_arrow],
        "rows": [{k: json_safe(v) for k, v in r.items()} for r in rows[:limit]],
        "total_rows": pf.metadata.num_rows,
        "location": get_storage().uri(dv.location),
    }
