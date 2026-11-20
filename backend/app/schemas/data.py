from typing import Literal

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.identity import ORM


class ImportSummary(ORM):
    id: str
    filename: str
    file_format: str
    size_bytes: int
    status: str
    row_count: int | None = None
    quarantined_count: int = 0
    error_message: str | None = None
    dataset_id: str | None = None
    uploaded_by_id: str | None = None
    created_at: datetime
    ingested_at: datetime | None = None


class ImportOut(ImportSummary):
    checksum_sha256: str
    raw_location: str
    options: dict
    schema_: list[dict] = Field(alias="schema", serialization_alias="schema")
    sample_rows: list[dict]
    warnings: list[str]
    inferred_primary_key: list[str]
    sheets: list[str]
    quarantine_location: str | None = None
    dataset_version_id: str | None = None
    suggested_dataset_name: str | None = None


class DiscoverIn(BaseModel):
    delimiter: str | None = Field(default=None, max_length=2)
    has_header: bool = True
    encoding: str | None = Field(default=None, max_length=32)
    sheet_name: str | None = Field(default=None, max_length=120)


class IngestIn(BaseModel):
    dataset_name: str = Field(min_length=1, max_length=63)
    description: str | None = Field(default=None, max_length=1000)
    mode: Literal["replace", "append"] = "replace"


class DatasetVersionOut(ORM):
    id: str
    version: int
    location: str
    row_count: int
    size_bytes: int
    schema_changes: dict
    source_import_id: str | None = None
    pipeline_run_id: str | None = None
    lineage: list[dict] | None = None
    created_by_id: str | None = None
    created_at: datetime


class DatasetOut(ORM):
    id: str
    layer: str
    name: str
    qualified_name: str
    description: str | None = None
    format: str
    schema_: list[dict] = Field(alias="schema", serialization_alias="schema")
    current_version: int
    row_count: int | None = None
    size_bytes: int | None = None
    owner_id: str | None = None
    created_at: datetime
    last_updated_at: datetime | None = None
    quality_score: float | None = None
    tags: list[str] | None = None
    column_docs: dict | None = None
    model_role: str | None = None
    primary_key: list[str] | None = None
    relationships: list[dict] | None = None


class DatasetDetail(DatasetOut):
    versions: list[DatasetVersionOut]


class IngestOut(BaseModel):
    dataset: DatasetOut
    version: DatasetVersionOut
