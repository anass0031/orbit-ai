from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.identity import ORM


class GraphIn(BaseModel):
    nodes: list[dict] = Field(default_factory=list, max_length=200)
    edges: list[dict] = Field(default_factory=list, max_length=400)


class PipelineCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    graph: GraphIn | None = None


class PipelineUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    graph: GraphIn | None = None


class PreviewIn(BaseModel):
    graph: GraphIn
    node_id: str
    limit: int = Field(default=50, ge=1, le=500)
    pipeline_id: str | None = None  # use this pipeline's parameters and node state
    params: dict | None = None


class PipelineOut(ORM):
    id: str
    name: str
    description: str | None = None
    status: str
    current_version: int
    owner_id: str | None = None
    last_run_at: datetime | None = None
    last_run_status: str | None = None
    next_run_at: datetime | None = None
    automation: dict = {}
    created_at: datetime
    updated_at: datetime


class PipelineDetail(PipelineOut):
    graph: dict


class PipelineVersionOut(ORM):
    id: str
    version: int
    created_by_id: str | None = None
    created_at: datetime


class RunOut(ORM):
    id: str
    pipeline_id: str
    pipeline_version: int
    status: str
    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    records_in: int | None = None
    records_out: int | None = None
    error_class: str | None = None
    error_message: str | None = None
    retryable: bool = False
    attempt: int = 1
    retry_of_id: str | None = None
    correlation_id: str | None = None
    triggered_by_id: str | None = None
    cancel_requested: bool = False
    inputs: list[dict] = []
    outputs: list[dict] = []
    trigger: str = "manual"
    params: dict = {}
    artifacts: list[dict] = []


class RunDetail(RunOut):
    pipeline_name: str | None = None
    graph_snapshot: dict
    node_status: dict
    logs: list[dict]


RunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
