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
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.logging import log_event
from app.db.base import new_id, utcnow
from app.ingestion.parsers import SUPPORTED, ParseError, ParseOptions, detect_format
from app.ingestion.schema import METADATA_COLUMNS, Discovery, discover_file
from app.lakehouse import catalog
from app.lakehouse.storage import get_storage
from app.models import FileImport, User
from app.services.audit import audit
from app.services.quality import apply_rules, persist, write_quarantine

logger = logging.getLogger("orbit.imports")
CONTENT_TYPES = {"csv": "text/csv", "json": "application/json", "parquet": "application/vnd.apache.parquet",
                 "xml": "application/xml", "yaml": "application/yaml",
                 "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def safe_filename(name: str) -> str:
    base = re.split(r"[\\/]", name or "")[-1].strip()
    base = re.sub(r"[^\w.\- ]+", "_", base)[:200]
    return base or "upload"


def _run_discovery(imp: FileImport, data: bytes, options: ParseOptions) -> Discovery | None:
    """Parse + infer; fills the import record. Returns None (and marks the import failed) on bad input."""
    try:
        d = discover_file(data, imp.file_format, imp.filename, options)
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
    if not data and fmt != "parquet":  # an empty Parquet upload is recorded as a failed import
        raise ValidationFailed("The file is empty", details=[{"field": "file", "message": "empty file"}])

    imp = FileImport(id=new_id(), filename=name, file_format=fmt, size_bytes=len(data),
                     checksum_sha256=hashlib.sha256(data).hexdigest(), uploaded_by_id=user.id, options={})
    imp.raw_location = f"landing/{imp.id}/{name}"
    get_storage().put_bytes(imp.raw_location, data, CONTENT_TYPES.get(fmt, "application/octet-stream"))
    _run_discovery(imp, data, options)
    earlier = db.scalar(select(FileImport).where(FileImport.checksum_sha256 == imp.checksum_sha256, FileImport.status == "ingested").limit(1))
    if earlier is not None and imp.status == "discovered":
        imp.warnings = [*(imp.warnings or []), f"This exact file was already loaded into Bronze (import {earlier.id[:8]})."]
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


def ingest(db: Session, user: User, imp: FileImport, dataset_name: str, description: str | None = None, append: bool = False):
    if imp.status == "ingested":
        raise Conflict("This import is already in the Bronze layer")
    if imp.status != "discovered":
        raise Conflict("The schema of this file could not be discovered; fix the options and run discovery again")
    catalog.validate_dataset_name(dataset_name)

    # Re-read the original file from storage: what lands in Bronze comes from the stored raw copy.
    data = get_storage().get_bytes(imp.raw_location)
    opts = ParseOptions(**{k: v for k, v in (imp.options or {}).items() if k in ParseOptions.__dataclass_fields__})
    d = discover_file(data, imp.file_format, imp.filename, opts)

    now = utcnow().astimezone(UTC)
    n = d.table.num_rows
    table = d.table
    table = table.append_column("_ingested_at", pa.array([now] * n, pa.timestamp("us", tz="UTC")))
    table = table.append_column("_source_file", pa.array([imp.filename] * n, pa.string()))
    table = table.append_column("_import_id", pa.array([imp.id] * n, pa.string()))
    schema = d.schema + [{**c, "nullable": False, "metadata": True} for c in METADATA_COLUMNS]

    # Quality rules already defined on this Bronze dataset are checked before the new version is written.
    existing = catalog.find_dataset(db, "bronze", dataset_name)
    outcome = apply_rules(db, existing, table) if existing else None
    if outcome and outcome.blocking:
        persist(db, existing, outcome, version=None, trigger="import")
        db.commit()
        raise Conflict(f"Nothing was written to bronze.{dataset_name}: {outcome.blocking}. See Data Quality for details.",
                       code="quality_gate_failed")
    if outcome and outcome.quarantined is not None and outcome.quarantined.num_rows:
        write_quarantine(f"quarantine/bronze_{dataset_name}/{imp.id}.jsonl", outcome.quarantined)
        table = outcome.table
    ds, dv = catalog.write_version(db, layer="bronze", name=dataset_name, table=table, schema=schema, user=user,
                                   description=description, source_import_id=imp.id, append=append)
    if outcome:
        persist(db, ds, outcome, version=dv.version, trigger="import")
    imp.status = "ingested"
    imp.dataset_id = ds.id
    imp.dataset_version_id = dv.id
    imp.ingested_at = utcnow()
    audit(db, actor=user, action="dataset.write", resource_type="dataset", resource_id=ds.id,
          details={"dataset": ds.qualified_name, "version": dv.version, "rows": dv.row_count,
                   "import_id": imp.id, "location": dv.location,
                   "quality": [{"rule": e.rule.name, "passed": e.passed} for e in outcome.evals] if outcome else []})
    try:
        db.commit()
    except Exception:
        db.rollback()
        catalog.discard_version(dv)
        raise
    log_event(logger, logging.INFO, "import.ingested", import_id=imp.id, dataset=ds.qualified_name,
              version=dv.version, rows=dv.row_count)
    return ds, dv


# ── several files at once ────────────────────────────────────────────────
def _already_loaded(db: Session, checksum: str, dataset: str, entity: str | None) -> bool:
    ds = catalog.find_dataset(db, "bronze", dataset)
    if ds is None:
        return False
    done = db.scalars(select(FileImport).where(FileImport.checksum_sha256 == checksum, FileImport.dataset_id == ds.id, FileImport.status == "ingested"))
    return any((i.options or {}).get("sheet_name") == entity or entity is None for i in done)


def _target(base: str, entity: str | None) -> str:
    if entity is None or entity == base:
        return base
    return (entity if entity.startswith(f"{base}_") else f"{base}_{entity}")[:63]


def ingest_batch(db: Session, user: User, files: list[tuple[str, bytes]], *, dataset_name: str | None = None, append: bool = False,
                 skip_duplicates: bool = True) -> dict:
    """Load several files. A file that fails is reported and never stops the others."""
    results: list[dict] = []
    for filename, data in files:
        name = safe_filename(filename)
        base = dataset_name or catalog.suggest_name(name)
        checksum = hashlib.sha256(data).hexdigest()
        entities: list[str | None] = [None]
        fmt = detect_format(name)
        try:
            if fmt and data:
                probe = discover_file(data, fmt, name, ParseOptions())
                if len(probe.sheets) > 1:
                    entities = list(probe.sheets)
        except (ParseError, ValueError):
            pass  # create_import below records the failed import with its reason
        for entity in entities:
            target = _target(base, entity)
            row = {"filename": name, "entity": entity, "dataset": f"bronze.{target}"}
            try:
                if skip_duplicates and _already_loaded(db, checksum, target, entity):
                    results.append({**row, "status": "skipped", "reason": "This file was already loaded into this dataset"})
                    continue
                imp = create_import(db, user, name, data, ParseOptions(sheet_name=entity))
                if imp.status != "discovered":
                    results.append({**row, "status": "failed", "import_id": imp.id, "reason": imp.error_message})
                    continue
                ds, dv = ingest(db, user, imp, target, append=append)
                results.append({**row, "status": "ingested", "import_id": imp.id, "dataset_id": ds.id, "version": dv.version, "rows": dv.row_count,
                                "new_rows": imp.row_count, "rejected_rows": imp.quarantined_count})
            except Exception as exc:  # noqa: BLE001 - partial failures are part of the result
                db.rollback()
                results.append({**row, "status": "failed", "reason": getattr(exc, "message", None) or str(exc)})
    summary = {s: sum(1 for r in results if r["status"] == s) for s in ("ingested", "skipped", "failed")}
    return {"results": results, "summary": summary}
