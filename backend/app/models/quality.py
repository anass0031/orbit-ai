"""Data quality: rules (per dataset), results (one per rule execution) and issues (anomalies to handle)."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, JSONType, TimestampMixin, utcnow

RULE_TYPES = ("not_null", "unique", "range", "regex", "allowed_values", "freshness", "row_count", "custom_sql")
DIMENSIONS = ("completeness", "validity", "uniqueness", "consistency", "freshness")
ISSUE_STATUSES = ("open", "acknowledged", "resolved")


class QualityRule(IdMixin, TimestampMixin, Base):
    __tablename__ = "quality_rules"
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    column: Mapped[str | None] = mapped_column(String(120))  # None = dataset-level rule (row_count, custom_sql…)
    name: Mapped[str] = mapped_column(String(160))
    rule_type: Mapped[str] = mapped_column(String(24))
    params: Mapped[dict] = mapped_column(JSONType, default=dict)
    dimension: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(8), default="medium")  # low | medium | high
    # warn: record only · fail: block the write (the dataset keeps its previous version) · quarantine: set failing rows aside
    on_failure: Mapped[str] = mapped_column(String(12), default="warn")
    threshold: Mapped[float] = mapped_column(Float, default=1.0)  # minimum share of passing rows to count as passed
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class QualityResult(IdMixin, Base):
    __tablename__ = "quality_results"
    rule_id: Mapped[str] = mapped_column(ForeignKey("quality_rules.id", ondelete="CASCADE"), index=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    dataset_version: Mapped[int | None] = mapped_column(Integer)  # None = the data was blocked before being written
    pipeline_run_id: Mapped[str | None] = mapped_column(ForeignKey("pipeline_runs.id", ondelete="SET NULL"))
    trigger: Mapped[str] = mapped_column(String(16), default="manual")  # manual | pipeline | import
    passed: Mapped[bool] = mapped_column(Boolean)
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float] = mapped_column(Float)  # 0..100 = share of passing rows
    evidence: Mapped[list] = mapped_column(JSONType, default=list)  # sample of failing rows
    check_sql: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class QualityIssue(IdMixin, Base):
    """An anomaly: opened when a rule fails, updated while it keeps failing, auto-resolved when it passes again."""

    __tablename__ = "quality_issues"
    rule_id: Mapped[str] = mapped_column(ForeignKey("quality_rules.id", ondelete="CASCADE"), index=True)
    result_id: Mapped[str] = mapped_column(ForeignKey("quality_results.id", ondelete="CASCADE"))
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), index=True)
    column: Mapped[str | None] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(255))
    dimension: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | acknowledged | resolved
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    evidence: Mapped[list] = mapped_column(JSONType, default=list)
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    acknowledged_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)
