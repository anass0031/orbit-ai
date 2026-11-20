"""Gold model advisor: analyse Silver, preview a dimensional model and generate it as an editable pipeline."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.errors import Forbidden
from app.core.permissions import has_permission
from app.db.session import get_db
from app.models import User
from app.services import gold_advisor as svc

router = APIRouter(prefix="/gold-models", tags=["gold models"])


class Selection(BaseModel):
    facts: list[str] | None = None
    dimensions: list[str] | None = None
    measures: dict[str, list[str]] = Field(default_factory=dict)
    attributes: dict[str, list[str]] = Field(default_factory=dict)
    labels: dict[str, str] = Field(default_factory=dict)
    marts: bool = True


class PreviewBody(BaseModel):
    model_id: str = Field(min_length=1, max_length=40)
    selection: Selection = Field(default_factory=Selection)


class GenerateBody(PreviewBody):
    name: str | None = Field(default=None, max_length=120)
    run: bool = True


@router.get("/analysis", summary="Facts, dimensions, relationships, grains and star / snowflake proposals found in Silver")
def analysis(db: Session = Depends(get_db), _: User = Depends(require("datasets.read"))):
    return svc.analyze(db)


@router.post("/preview", summary="Tables, columns, join conditions and sample rows of the selected model; nothing is written")
def preview(body: PreviewBody, db: Session = Depends(get_db), user: User = Depends(require("pipelines.write"))):
    return svc.preview(db, user, body.model_id, body.selection.model_dump())


@router.post("/generate", status_code=201, summary="Create the selected model as an editable pipeline and optionally run it")
def generate(body: GenerateBody, db: Session = Depends(get_db), user: User = Depends(require("pipelines.write"))):
    if body.run and not has_permission(user.role, "pipelines.run"):
        raise Forbidden("Your role does not allow running pipelines")
    return svc.generate(db, user, body.model_id, body.selection.model_dump(), body.name, body.run)
