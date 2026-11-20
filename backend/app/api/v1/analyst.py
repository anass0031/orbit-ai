"""Business glossary API."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.errors import NotFound
from app.db.session import get_db
from app.models import GlossaryTerm, User
from app.services import catalog_docs

router = APIRouter(tags=["glossary"])


# ── glossary ──
class TermBody(BaseModel):
    name: str = Field(max_length=120)
    definition: str = Field(max_length=4000)
    synonyms: list[str] = Field(default_factory=list)
    links: list[dict] = Field(default_factory=list)


@router.get("/glossary")
def glossary(q: str | None = None, db: Session = Depends(get_db), _: User = Depends(require("glossary.read"))):
    return catalog_docs.terms(db, q)


@router.post("/glossary", status_code=201)
def create_term(body: TermBody, db: Session = Depends(get_db), user: User = Depends(require("catalog.document"))):
    t = catalog_docs.create_term(db, user, name=body.name, definition=body.definition, synonyms=body.synonyms, links=body.links)
    db.commit()
    return catalog_docs.term_out(t)


def _term(db: Session, term_id: str) -> GlossaryTerm:
    t = db.get(GlossaryTerm, term_id)
    if t is None:
        raise NotFound("Term not found")
    return t


@router.put("/glossary/{term_id}")
def update_term(term_id: str, body: TermBody, db: Session = Depends(get_db), user: User = Depends(require("catalog.document"))):
    t = catalog_docs.update_term(db, user, _term(db, term_id), name=body.name, definition=body.definition, synonyms=body.synonyms,
                                 links=body.links)
    db.commit()
    return catalog_docs.term_out(t)


@router.delete("/glossary/{term_id}", status_code=204)
def delete_term(term_id: str, db: Session = Depends(get_db), user: User = Depends(require("catalog.document"))):
    from app.services.audit import audit

    t = _term(db, term_id)
    audit(db, actor=user, action="glossary.delete", resource_type="glossary_term", resource_id=t.id, details={"name": t.name})
    db.delete(t)
    db.commit()
