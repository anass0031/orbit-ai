"""File imports and the lakehouse catalogue (Bronze layer this week; Silver/Gold arrive in week 4)."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, JSONType, TimestampMixin, utcnow

LAYERS = ("bronze", "silver", "gold")
IMPORT_STATUSES = ("discovered", "ingested", "failed")


class FileImport(IdMixin, TimestampMixin, Base):
    """One uploaded file: raw copy in object storage + the schema discovered from it."""

    __tablename__ = "file_imports"
    filename: Mapped[str] = mapped_column(String(255))
    file_format: Mapped[str] = mapped_column(String(16))  # csv | excel | json
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    checksum_sha256: Mapped[str] = mapped_column(String(64))
    raw_location: Mapped[str] = mapped_column(Text)  # object key of the original file (landing zone)
    options: Mapped[dict] = mapped_column(JSONType, default=dict)  # delimiter, sheet, encoding…
    # discovered → ingested (Bronze written) | failed
    status: Mapped[str] = mapped_column(String(16), default="discovered", index=True)
    row_count: Mapped[int | None] = mapped_column(Integer)
    schema: Mapped[list] = mapped_column(JSONType, default=list)  # [{name, source_name, type, nullable, …}]
    sample_rows: Mapped[list] = mapped_column(JSONType, default=list)
    warnings: Mapped[list] = mapped_column(JSONType, default=list)
    inferred_primary_key: Mapped[list] = mapped_column(JSONType, default=list)
    quarantined_count: Mapped[int] = mapped_column(Integer, default=0)
    quarantine_location: Mapped[str | None] = mapped_column(Text)  # JSON Lines file of rejected records
    sheets: Mapped[list] = mapped_column(JSONType, default=list)  # Excel only
    error_message: Mapped[str | None] = mapped_column(Text)
    dataset_id: Mapped[str | None] = mapped_column(ForeignKey("datasets.id", ondelete="SET NULL"))
    dataset_version_id: Mapped[str | None] = mapped_column(ForeignKey("dataset_versions.id", ondelete="SET NULL"))
    uploaded_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Dataset(IdMixin, TimestampMixin, Base):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("layer", "name"),)
    layer: Mapped[str] = mapped_column(String(8), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    format: Mapped[str] = mapped_column(String(16), default="parquet")
    schema: Mapped[list] = mapped_column(JSONType, default=list)
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    current_version_id: Mapped[str | None] = mapped_column(String(32))
    row_count: Mapped[int | None] = mapped_column(BigInteger)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    last_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def qualified_name(self) -> str:
        return f"{self.layer}.{self.name}"


class DatasetVersion(IdMixin, Base):
    """Immutable version of a dataset. A new import never overwrites an older version's file."""

    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("dataset_id", "version"),)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    location: Mapped[str] = mapped_column(Text)  # object key of the Parquet file
    row_count: Mapped[int] = mapped_column(BigInteger, default=0)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    schema: Mapped[list] = mapped_column(JSONType, default=list)
    schema_changes: Mapped[dict] = mapped_column(JSONType, default=dict)  # {added, removed, changed} vs previous
    source_import_id: Mapped[str | None] = mapped_column(String(32))
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
