"""Import every model so `Base.metadata` is complete (Alembic, create_all in tests)."""
from app.models.analyst import GlossaryTerm
from app.models.data import IMPORT_STATUSES, LAYERS, Dataset, DatasetVersion, FileImport
from app.models.identity import AuditEvent, User
from app.models.pipelines import Notification, Pipeline, PipelineRun, PipelineVersion
from app.models.quality import DIMENSIONS, ISSUE_STATUSES, RULE_TYPES, QualityIssue, QualityResult, QualityRule

__all__ = [
    "GlossaryTerm",
    "IMPORT_STATUSES", "LAYERS", "Dataset", "DatasetVersion", "FileImport",
    "AuditEvent", "User",
    "Notification", "Pipeline", "PipelineRun", "PipelineVersion",
    "DIMENSIONS", "ISSUE_STATUSES", "RULE_TYPES", "QualityIssue", "QualityResult", "QualityRule",
]
