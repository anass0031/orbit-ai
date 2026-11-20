"""Data quality: rules, on-demand checks, results history, scores and anomalies (issues)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.errors import NotFound
from app.db.base import utcnow
from app.db.session import get_db
from app.lakehouse import catalog
from app.models import Dataset, QualityIssue, QualityResult, QualityRule, User
from app.schemas.identity import Page
from app.schemas.quality import IssueOut, IssueUpdate, ResultOut, RuleCreate, RuleOut, RuleUpdate
from app.services import quality as q
from app.services.audit import audit

router = APIRouter(prefix="/quality", tags=["quality"])


def _rule_out(r: QualityRule, last: QualityResult | None, names: dict[str, str]) -> RuleOut:
    out = RuleOut.model_validate(r)
    out.last_result = ResultOut.model_validate(last) if last else None
    out.dataset_name = names.get(r.dataset_id)
    return out


def _names(db: Session) -> dict[str, str]:
    return {d.id: d.qualified_name for d in db.scalars(select(Dataset))}


@router.get("/overview", summary="Global and per-dataset scores, by dimension, plus open anomalies")
def overview(db: Session = Depends(get_db), _: User = Depends(require("quality.read"))):
    datasets = db.scalars(select(Dataset).where(Dataset.current_version > 0).order_by(Dataset.layer, Dataset.name)).all()
    rules = db.execute(select(QualityRule.dataset_id, func.count()).group_by(QualityRule.dataset_id)).all()
    rule_counts = dict(rules)
    open_issues = dict(db.execute(select(QualityIssue.dataset_id, func.count())
                                  .where(QualityIssue.status.in_(("open", "acknowledged")))
                                  .group_by(QualityIssue.dataset_id)).all())
    scored = [d.quality_score for d in datasets if d.quality_score is not None]
    return {
        "score": round(sum(scored) / len(scored), 1) if scored else None,
        "dimensions": q.dimension_scores(db),
        "datasets": [{"id": d.id, "qualified_name": d.qualified_name, "layer": d.layer, "quality_score": d.quality_score,
                      "rules": rule_counts.get(d.id, 0), "open_issues": open_issues.get(d.id, 0),
                      "dimensions": q.dimension_scores(db, [d.id])} for d in datasets],
        "open_issues": sum(open_issues.values()),
    }


@router.get("/rules", response_model=list[RuleOut])
def list_rules(dataset_id: str | None = None, db: Session = Depends(get_db), _: User = Depends(require("quality.read"))):
    stmt = select(QualityRule).order_by(QualityRule.created_at)
    if dataset_id:
        stmt = stmt.where(QualityRule.dataset_id == dataset_id)
    rules = db.scalars(stmt).all()
    latest = q.latest_results(db, [r.id for r in rules])
    names = _names(db)
    return [_rule_out(r, latest.get(r.id), names) for r in rules]


@router.post("/rules", response_model=RuleOut, status_code=201)
def create_rule(body: RuleCreate, db: Session = Depends(get_db), user: User = Depends(require("quality.write"))):
    ds = catalog.get_dataset(db, body.dataset_id)
    column = body.column or None
    q.validate_rule(ds, body.rule_type, column, body.params)
    name = (body.name or "").strip() or f"{body.rule_type.replace('_', ' ')}" + (f" ({column})" if column else "")
    r = QualityRule(dataset_id=ds.id, column=column, name=name, rule_type=body.rule_type, params=body.params,
                    dimension=q.DIMENSION_FOR[body.rule_type], severity=body.severity, on_failure=body.on_failure,
                    threshold=body.threshold, created_by_id=user.id)
    db.add(r)
    db.flush()
    audit(db, actor=user, action="quality.rule_create", resource_type="quality_rule", resource_id=r.id,
          details={"dataset": ds.qualified_name, "rule": name, "type": body.rule_type})
    db.commit()
    return _rule_out(r, None, {ds.id: ds.qualified_name})


def _get_rule(db: Session, rule_id: str) -> QualityRule:
    r = db.get(QualityRule, rule_id)
    if r is None:
        raise NotFound("Quality rule not found")
    return r


@router.patch("/rules/{rule_id}", response_model=RuleOut)
def update_rule(rule_id: str, body: RuleUpdate, db: Session = Depends(get_db), user: User = Depends(require("quality.write"))):
    r = _get_rule(db, rule_id)
    ds = catalog.get_dataset(db, r.dataset_id)
    changes = body.model_dump(exclude_unset=True)
    if "params" in changes:
        q.validate_rule(ds, r.rule_type, r.column, changes["params"] or {})
    for k, v in changes.items():
        if v is not None:
            setattr(r, k, v.strip() if isinstance(v, str) and k == "name" else v)
    if changes.get("enabled") is False:  # a disabled rule never runs again: close its active anomalies
        for i in db.scalars(select(QualityIssue).where(QualityIssue.rule_id == r.id,
                                                       QualityIssue.status.in_(("open", "acknowledged")))):
            i.status, i.resolved_at, i.resolved_by_id = "resolved", utcnow(), user.id
            i.resolution_note = "Resolved: the rule was disabled"
    db.flush()
    q.recompute_score(db, ds)  # disabling a rule removes it from the score
    audit(db, actor=user, action="quality.rule_update", resource_type="quality_rule", resource_id=r.id, details=changes)
    db.commit()
    return _rule_out(r, q.latest_results(db, [r.id]).get(r.id), {ds.id: ds.qualified_name})


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: str, db: Session = Depends(get_db), user: User = Depends(require("quality.write"))):
    r = _get_rule(db, rule_id)
    ds = catalog.get_dataset(db, r.dataset_id)
    audit(db, actor=user, action="quality.rule_delete", resource_type="quality_rule", resource_id=r.id, details={"rule": r.name})
    db.delete(r)
    db.flush()
    q.recompute_score(db, ds)
    db.commit()


@router.post("/datasets/{dataset_id}/run", response_model=list[ResultOut],
             summary="Run every enabled rule of a dataset on its current version")
def run_checks(dataset_id: str, db: Session = Depends(get_db), user: User = Depends(require("quality.run"))):
    ds = catalog.get_dataset(db, dataset_id)
    results = q.run_on_demand(db, ds)
    audit(db, actor=user, action="quality.run", resource_type="dataset", resource_id=ds.id,
          details={"dataset": ds.qualified_name, "rules": len(results), "failed": sum(1 for r in results if not r.passed)})
    db.commit()
    return results


@router.get("/results", response_model=Page, summary="Result history, newest first")
def results(dataset_id: str | None = None, rule_id: str | None = None, limit: int = Query(50, ge=1, le=200),
            offset: int = Query(0, ge=0), db: Session = Depends(get_db), _: User = Depends(require("quality.read"))):
    stmt = select(QualityResult)
    if dataset_id:
        stmt = stmt.where(QualityResult.dataset_id == dataset_id)
    if rule_id:
        stmt = stmt.where(QualityResult.rule_id == rule_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(QualityResult.executed_at.desc()).limit(limit).offset(offset)).all()
    return Page(items=[ResultOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/issues", response_model=Page, summary="Quality anomalies")
def issues(status: str | None = Query(None, pattern="^(open|acknowledged|resolved|active)$"), dataset_id: str | None = None,
           severity: str | None = Query(None, pattern="^(low|medium|high)$"), limit: int = Query(50, ge=1, le=200),
           offset: int = Query(0, ge=0), db: Session = Depends(get_db), _: User = Depends(require("quality.read"))):
    stmt = select(QualityIssue)
    if status == "active":
        stmt = stmt.where(QualityIssue.status.in_(("open", "acknowledged")))
    elif status:
        stmt = stmt.where(QualityIssue.status == status)
    if dataset_id:
        stmt = stmt.where(QualityIssue.dataset_id == dataset_id)
    if severity:
        stmt = stmt.where(QualityIssue.severity == severity)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(QualityIssue.last_seen_at.desc()).limit(limit).offset(offset)).all()
    names = _names(db)
    rule_names = {r.id: r.name for r in db.scalars(select(QualityRule).where(QualityRule.id.in_([i.rule_id for i in rows])))}
    items = []
    for i in rows:
        out = IssueOut.model_validate(i)
        out.dataset_name, out.rule_name = names.get(i.dataset_id), rule_names.get(i.rule_id)
        items.append(out)
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.patch("/issues/{issue_id}", response_model=IssueOut, summary="Acknowledge, resolve or reopen an anomaly")
def update_issue(issue_id: str, body: IssueUpdate, db: Session = Depends(get_db), user: User = Depends(require("quality.triage"))):
    i = db.get(QualityIssue, issue_id)
    if i is None:
        raise NotFound("Quality issue not found")
    now = utcnow()
    i.status = body.status
    if body.status == "acknowledged":
        i.acknowledged_by_id, i.acknowledged_at = user.id, now
    elif body.status == "resolved":
        i.resolved_by_id, i.resolved_at = user.id, now
        i.resolution_note = body.note or "Resolved manually"
    else:
        i.resolved_by_id = i.resolved_at = None
    if body.note and body.status != "resolved":
        i.resolution_note = body.note
    audit(db, actor=user, action=f"quality.issue_{body.status}", resource_type="quality_issue", resource_id=i.id,
          details={"title": i.title, "note": body.note})
    db.commit()
    return i
