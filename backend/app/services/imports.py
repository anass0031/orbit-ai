"""File import workflow.

    1. upload    → the original file is stored unchanged in object storage (landing/<import_id>/<file>)
    2. discover  → the file is parsed and its schema inferred (types, nulls, keys, warnings, sample)
                   — can be re-run with other options (delimiter, sheet, encoding, header)
    3. ingest    → the typed table + 3 technical columns is written as a new Parquet version of a
                   Bronze dataset (bronze/<dataset>/v<N>/<id>.parquet) and registered in the catalogue
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC

import pyarrow as pa
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.logging import log_event
from app.db.base import new_id, utcnow
from app.ingestion.parsers import SUPPORTED, ParseError, ParseOptions, detect_format, parse
from app.ingestion.schema import METADATA_COLUMNS, Discovery, discover
from app.lakehouse import catalog
from app.lakehouse.storage import get_storage
from app.models import FileImport, User
from app.services.audit import audit

logger = logging.getLogger("orbit.imports")
CONTENT_TYPES = {"csv": "text/csv", "json": "application/json",
                 "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def safe_filename(name: str) -> str:
    base = re.split(r"[\\/]", name or "")[-1].strip()
    base = re.sub(r"[^\w.\- ]+", "_", base)[:200]
    return base or "upload"


def _run_discovery(imp: FileImport, data: bytes, options: ParseOptions) -> Discovery | None:
    """Parse + infer; fills the import record. Returns None (and marks the import failed) on bad input."""
    try:
        d = discover(parse(data, imp.file_format, imp.filename, options))
    except (ParseError, ValueError) as exc:
        imp.status = "failed"
        imp.error_message = str(exc)
        imp.schema, imp.sample_rows, imp.warnings, imp.row_count = [], [], [], None
        return None
    imp.status = "discovered"
    imp.error_message = None
    imp.options = {**options.as_dict(), **d.options_used}
    imp.schema = d.schema
    imp.row_count = d.row_count
    imp.sample_rows = d.sample_rows(get_settings().preview_rows)
    imp.warnings = d.warnings
    imp.inferred_primary_key = d.primary_key
    imp.sheets = d.sheets
    imp.quarantined_count = len(d.quarantined)
    imp.quarantine_location = None
    if d.quarantined:
        key = f"landing/{imp.id}/quarantine.jsonl"
        body = "\n".join(json.dumps(q, ensure_ascii=False, default=str) for q in d.quarantined)
        get_storage().put_bytes(key, body.encode("utf-8"), "application/x-ndjson")
        imp.quarantine_location = key
    return d


def create_import(db: Session, user: User, filename: str, data: bytes, options: ParseOptions) -> FileImport:
    name = safe_filename(filename)
    fmt = detect_format(name)
    if fmt is None:
        raise ValidationFailed(f"Unsupported file type. Supported extensions: {SUPPORTED}",
                               details=[{"field": "file", "message": "unsupported file type"}])
    max_bytes = get_settings().upload_max_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise ValidationFailed(f"The file is larger than the {get_settings().upload_max_mb} MB limit",
                               details=[{"field": "file", "message": "file too large"}])
    if not data:
        raise ValidationFailed("The file is empty", details=[{"field": "file", "message": "empty file"}])

    imp = FileImport(id=new_id(), filename=name, file_format=fmt, size_bytes=len(data),
                     checksum_sha256=hashlib.sha256(data).hexdigest(), uploaded_by_id=user.id, options={})
    imp.raw_location = f"landing/{imp.id}/{name}"
    get_storage().put_bytes(imp.raw_location, data, CONTENT_TYPES.get(fmt, "application/octet-stream"))
    _run_discovery(imp, data, options)
    db.add(imp)
    audit(db, actor=user, action="import.upload", resource_type="file_import", resource_id=imp.id,
          details={"filename": name, "format": fmt, "size_bytes": len(data), "status": imp.status})
    db.commit()
    log_event(logger, logging.INFO, "import.uploaded", import_id=imp.id, status=imp.status, rows=imp.row_count)
    return imp


def get_import(db: Session, import_id: str) -> FileImport:
    imp = db.get(FileImport, import_id)
    if imp is None:
        raise NotFound("Import not found")
    return imp


def rediscover(db: Session, user: User, imp: FileImport, options: ParseOptions) -> FileImport:
    if imp.status == "ingested":
        raise Conflict("This import is already in the Bronze layer; upload the file again to create a new version")
    _run_discovery(imp, get_storage().get_bytes(imp.raw_location), options)
    audit(db, actor=user, action="import.discover", resource_type="file_import", resource_id=imp.id,
          details={"options": options.as_dict(), "status": imp.status})
    db.commit()
    return imp


def ingest(db: Session, user: User, imp: FileImport, dataset_name: str, description: str | None = None):
    if imp.status == "ingested":
        raise Conflict("This import is already in the Bronze layer")
    if imp.status != "discovered":
        raise Conflict("The schema of this file could not be discovered; fix the options and run discovery again")
    catalog.validate_dataset_name(dataset_name)

    # Re-read the original file from storage: what lands in Bronze comes from the stored raw copy.
    data = get_storage().get_bytes(imp.raw_location)
    opts = ParseOptions(**{k: v for k, v in (imp.options or {}).items() if k in ParseOptions.__dataclass_fields__})
    d = discover(parse(data, imp.file_format, imp.filename, opts))

    now = utcnow().astimezone(UTC)
    n = d.table.num_rows
    table = d.table
    table = table.append_column("_ingested_at", pa.array([now] * n, pa.timestamp("us", tz="UTC")))
    table = table.append_column("_source_file", pa.array([imp.filename] * n, pa.string()))
    table = table.append_column("_import_id", pa.array([imp.id] * n, pa.string()))
    schema = d.schema + [{**c, "nullable": False, "metadata": True} for c in METADATA_COLUMNS]

    ds, dv = catalog.write_version(db, layer="bronze", name=dataset_name, table=table, schema=schema, user=user,
                                   description=description, source_import_id=imp.id)
    imp.status = "ingested"
    imp.dataset_id = ds.id
    imp.dataset_version_id = dv.id
    imp.ingested_at = utcnow()
    audit(db, actor=user, action="dataset.write", resource_type="dataset", resource_id=ds.id,
          details={"dataset": ds.qualified_name, "version": dv.version, "rows": dv.row_count,
                   "import_id": imp.id, "location": dv.location})
    try:
        db.commit()
    except Exception:
        db.rollback()
        catalog.discard_version(dv)
        raise
    log_event(logger, logging.INFO, "import.ingested", import_id=imp.id, dataset=ds.qualified_name,
              version=dv.version, rows=dv.row_count)
    return ds, dv
