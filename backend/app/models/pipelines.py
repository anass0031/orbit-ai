"""ETL pipelines, their immutable published versions and their runs (each run keeps a snapshot of the graph)."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, JSONType, RunMixin, TimestampMixin, utcnow


class Pipeline(IdMixin, TimestampMixin, Base):
    __tablename__ = "pipelines"
    name: Mapped[str] = mapped_column(String(160), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    # draft = the working graph differs from the last published version · published = identical
    status: Mapped[str] = mapped_column(String(16), default="draft")
    graph: Mapped[dict] = mapped_column(JSONType, default=dict)  # working draft edited in the visual editor
    current_version: Mapped[int] = mapped_column(Integer, default=0)  # latest published version (0 = never)
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_status: Mapped[str | None] = mapped_column(String(16))
    # schedule, triggers, run conditions, retry, time limit, notifications, SLA, parameters (see services/automation.py)
    automation: Mapped[dict] = mapped_column(JSONType, default=dict)
    # what the next run needs: node state (watermarks, processed files, baselines), pending retry, events log
    state: Mapped[dict] = mapped_column(JSONType, default=dict)
    trigger_token_hash: Mapped[str | None] = mapped_column(String(128))  # API trigger (sha256 of the token)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class PipelineVersion(IdMixin, Base):
    __tablename__ = "pipeline_versions"
    __table_args__ = (UniqueConstraint("pipeline_id", "version"),)
    pipeline_id: Mapped[str] = mapped_column(ForeignKey("pipelines.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    graph: Mapped[dict] = mapped_column(JSONType)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PipelineRun(IdMixin, RunMixin, Base):
    __tablename__ = "pipeline_runs"
    pipeline_id: Mapped[str] = mapped_column(ForeignKey("pipelines.id", ondelete="CASCADE"), index=True)
    pipeline_version: Mapped[int] = mapped_column(Integer)
    graph_snapshot: Mapped[dict] = mapped_column(JSONType)
    # {node_id: {status, started_at, finished_at, rows, message, schema, quality}}
    node_status: Mapped[dict] = mapped_column(JSONType, default=dict)
    # [{dataset_id, qualified_name, version, rows}] read / written by this run
    inputs: Mapped[list] = mapped_column(JSONType, default=list)
    outputs: Mapped[list] = mapped_column(JSONType, default=list)
    engine: Mapped[str] = mapped_column(String(16), default="duckdb")
    # manual | schedule | new_data | after_pipeline | api | retry | backfill
    trigger: Mapped[str] = mapped_column(String(24), default="manual")
    params: Mapped[dict] = mapped_column(JSONType, default=dict)  # parameter values of this run
    # results of the destination nodes: [{node_id, type, status, message, details}]
    artifacts: Mapped[list] = mapped_column(JSONType, default=list)


class Notification(IdMixin, Base):
    """In-app notification for one user (pipeline success / failure / quality warning / SLA breach)."""

    __tablename__ = "notifications"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    level: Mapped[str] = mapped_column(String(16), default="info")  # info | success | warning | error
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(300))
    pipeline_id: Mapped[str | None] = mapped_column(String(32))
    run_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
