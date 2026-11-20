from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.identity import ORM

RuleType = Literal["not_null", "unique", "range", "regex", "allowed_values", "freshness", "row_count", "custom_sql"]
Severity = Literal["low", "medium", "high"]
OnFailure = Literal["warn", "fail", "quarantine"]


class RuleCreate(BaseModel):
    dataset_id: str
    name: str | None = Field(default=None, max_length=160)
    rule_type: RuleType
    column: str | None = Field(default=None, max_length=120)
    params: dict = Field(default_factory=dict)
    severity: Severity = "medium"
    on_failure: OnFailure = "warn"
    threshold: float = Field(default=1.0, ge=0, le=1)


class RuleUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    params: dict | None = None
    severity: Severity | None = None
    on_failure: OnFailure | None = None
    threshold: float | None = Field(default=None, ge=0, le=1)
    enabled: bool | None = None


class ResultOut(ORM):
    id: str
    rule_id: str
    dataset_id: str
    dataset_version: int | None = None
    pipeline_run_id: str | None = None
    trigger: str
    passed: bool
    total_count: int
    failed_count: int
    score: float
    evidence: list
    check_sql: str | None = None
    error_message: str | None = None
    executed_at: datetime


class RuleOut(ORM):
    id: str
    dataset_id: str
    column: str | None = None
    name: str
    rule_type: str
    params: dict
    dimension: str
    severity: str
    on_failure: str
    threshold: float
    enabled: bool
    created_at: datetime
    last_result: ResultOut | None = None
    dataset_name: str | None = None


class IssueOut(ORM):
    id: str
    rule_id: str
    dataset_id: str
    column: str | None = None
    title: str
    dimension: str
    severity: str
    status: str
    failed_count: int
    evidence: list
    occurrences: int
    detected_at: datetime
    last_seen_at: datetime
    acknowledged_by_id: str | None = None
    acknowledged_at: datetime | None = None
    resolved_by_id: str | None = None
    resolved_at: datetime | None = None
    resolution_note: str | None = None
    dataset_name: str | None = None
    rule_name: str | None = None


class IssueUpdate(BaseModel):
    status: Literal["open", "acknowledged", "resolved"]
    note: str | None = Field(default=None, max_length=2000)
