"""Data quality engine (DuckDB) + results / anomalies persistence + scores.

Rules are evaluated on the data that is ABOUT to be written (pre-write), so:
- on_failure="fail"       → the write is blocked and the dataset keeps its previous version;
- on_failure="quarantine" → failing rows are removed from the output and stored in quarantine/;
- on_failure="warn"       → result + anomaly recorded, data written as-is.
They can also be run on demand against a dataset's current version.

Scores are computed from real results only:
- rule score    = 100 × passing rows / evaluated rows (100 or 0 for dataset-level checks);
- dataset score = mean of the latest score of every enabled rule (None when nothing has run yet).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
import sqlglot
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed
from app.db.base import utcnow
from app.lakehouse.storage import get_storage
from app.models import Dataset, QualityIssue, QualityResult, QualityRule

DIMENSION_FOR = {
    "not_null": "completeness", "row_count": "completeness", "unique": "uniqueness", "range": "validity",
    "regex": "validity", "allowed_values": "validity", "freshness": "freshness", "custom_sql": "consistency",
}
COLUMN_RULES = ("not_null", "unique", "range", "regex", "allowed_values")
EVIDENCE_ROWS = 10


def qi(name: str) -> str:
    """Quote an identifier for DuckDB."""
    return '"' + name.replace('"', '""') + '"'


def cell(v):
    """JSON-safe value for evidence / previews."""
    if v is None or isinstance(v, bool | int | str):
        return v
    if isinstance(v, float):
        return None if v != v else v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime | date | time):
        return v.isoformat()
    return str(v)


@dataclass
class RuleEval:
    rule: QualityRule
    passed: bool
    total: int
    failed: int
    score: float
    evidence: list[dict]
    check_sql: str
    error: str | None = None
    failing_ids: set[int] = field(default_factory=set)


@dataclass
class Outcome:
    table: pa.Table
    evals: list[RuleEval]
    quarantined: pa.Table | None = None
    blocking: str | None = None


def validate_rule(ds: Dataset, rule_type: str, column: str | None, params: dict) -> None:
    cols = {c["name"]: c["type"] for c in ds.schema or []}
    errs = []
    if rule_type not in DIMENSION_FOR:
        errs.append({"field": "rule_type", "message": f"unknown rule type '{rule_type}'"})
    if rule_type in COLUMN_RULES and not column:
        errs.append({"field": "column", "message": "this rule needs a column"})
    if column and cols and column not in cols:
        errs.append({"field": "column", "message": f"column '{column}' does not exist in {ds.qualified_name}"})
    if rule_type == "range":
        if params.get("min") is None and params.get("max") is None:
            errs.append({"field": "params", "message": "range needs a min and/or a max"})
        for k in ("min", "max"):
            if params.get(k) is not None:
                try:
                    float(params[k])
                except (TypeError, ValueError):
                    errs.append({"field": f"params.{k}", "message": f"{k} must be a number"})
    if rule_type == "regex":
        try:
            if not params.get("pattern"):
                raise re.error("empty")
            re.compile(params["pattern"])
        except re.error:
            errs.append({"field": "params.pattern", "message": "a valid regular expression is required"})
    if rule_type == "allowed_values" and not params.get("values"):
        errs.append({"field": "params.values", "message": "list the allowed values"})
    if rule_type == "freshness":
        try:
            if float(params.get("max_age_hours") or 0) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            errs.append({"field": "params.max_age_hours", "message": "max age in hours must be a positive number"})
    if rule_type == "row_count" and params.get("min") is None and params.get("max") is None:
        errs.append({"field": "params", "message": "row count needs a min and/or a max"})
    if rule_type == "custom_sql":
        expr = params.get("expression") or ""
        try:
            parsed = sqlglot.parse_one(expr, read="duckdb")
            if ";" in expr or any(isinstance(n, sqlglot.exp.Select | sqlglot.exp.Subquery) for n in parsed.walk()):
                raise ValueError
        except Exception:  # noqa: BLE001
            errs.append({"field": "params.expression",
                         "message": "must be a single boolean expression, e.g. amount >= 0 AND quantity > 0"})
    if errs:
        raise ValidationFailed("Quality rule is invalid", details=errs)


def _failing_predicate(rule: QualityRule) -> tuple[str, list]:
    """SQL predicate selecting FAILING rows, with bound parameters (no value is pasted into SQL)."""
    c = qi(rule.column) if rule.column else None
    p = rule.params or {}
    if rule.rule_type == "not_null":
        return f"{c} IS NULL", []
    if rule.rule_type == "unique":
        if len(p.get("columns") or []) > 1:  # composite key
            cols = ", ".join(qi(x) for x in p["columns"])
            filled = " AND ".join(f"{qi(x)} IS NOT NULL" for x in p["columns"])
            return f"{filled} AND row({cols}) IN (SELECT row({cols}) FROM t GROUP BY {cols} HAVING count(*) > 1)", []
        return f"{c} IS NOT NULL AND {c} IN (SELECT {c} FROM t GROUP BY {c} HAVING count(*) > 1)", []
    if rule.rule_type == "range":
        parts, args = [], []
        if p.get("min") is not None:
            parts.append(f"{c} < ?")
            args.append(float(p["min"]))
        if p.get("max") is not None:
            parts.append(f"{c} > ?")
            args.append(float(p["max"]))
        return f"{c} IS NOT NULL AND ({' OR '.join(parts)})", args
    if rule.rule_type == "regex":
        return f"{c} IS NOT NULL AND NOT regexp_full_match(CAST({c} AS VARCHAR), ?)", [p["pattern"]]
    if rule.rule_type == "allowed_values":
        vals = [str(v) for v in p["values"]]
        return f"{c} IS NOT NULL AND CAST({c} AS VARCHAR) NOT IN ({', '.join('?' for _ in vals)})", vals
    if rule.rule_type == "custom_sql":
        return f"NOT coalesce(({p['expression']}), FALSE)", []
    raise ValueError(rule.rule_type)


def evaluate(con: duckdb.DuckDBPyConnection, rule: QualityRule, total: int, last_updated_at=None) -> RuleEval:
    """Evaluate one rule against view `t` (which has a `__rowid` column)."""
    try:
        p = rule.params or {}
        if rule.rule_type == "row_count":
            mn, mx = p.get("min"), p.get("max")
            ok = (mn is None or total >= int(mn)) and (mx is None or total <= int(mx))
            return RuleEval(rule, ok, 1, 0 if ok else 1, 100.0 if ok else 0.0,
                            [] if ok else [{"row_count": total, "expected_min": mn, "expected_max": mx}],
                            f"SELECT count(*) FROM t  -- expect {mn if mn is not None else 0}..{mx if mx is not None else '∞'} rows")
        if rule.rule_type == "freshness":
            hours = float(p["max_age_hours"])
            latest = con.execute(f"SELECT max({qi(rule.column)}) FROM t").fetchone()[0] if rule.column else last_updated_at
            if latest is None:
                return RuleEval(rule, False, 1, 1, 0.0, [{"latest": None}], "freshness: no timestamp found")
            if not isinstance(latest, datetime):  # DATE column
                latest = datetime.combine(latest, datetime.min.time())
            age_h = (utcnow().replace(tzinfo=None) - latest.replace(tzinfo=None)).total_seconds() / 3600
            ok = age_h <= hours
            return RuleEval(rule, ok, 1, 0 if ok else 1, 100.0 if ok else 0.0,
                            [] if ok else [{"latest": str(latest), "age_hours": round(age_h, 1), "max_age_hours": hours}],
                            f"SELECT max({rule.column or 'last_updated_at'}) FROM t  -- must be within {hours} h")
        pred, args = _failing_predicate(rule)
        sql = f"SELECT count(*) FROM t WHERE {pred}"
        failed = con.execute(sql, args).fetchone()[0]
        evidence: list[dict] = []
        ids: set[int] = set()
        if failed:
            rel = con.execute(f"SELECT * EXCLUDE (__rowid) FROM t WHERE {pred} LIMIT {EVIDENCE_ROWS}", args)
            cols = [d[0] for d in rel.description]
            evidence = [{k: cell(v) for k, v in zip(cols, r, strict=False)} for r in rel.fetchall()]
            if rule.on_failure == "quarantine":
                ids = {r[0] for r in con.execute(f"SELECT __rowid FROM t WHERE {pred}", args).fetchall()}
        score = 100.0 * (total - failed) / total if total else 100.0
        passed = (total - failed) / total >= (rule.threshold or 1.0) if total else True
        shown = sql
        for a in args:
            shown = shown.replace("?", repr(a), 1)
        return RuleEval(rule, passed, total, int(failed), round(score, 2), evidence, shown, failing_ids=ids)
    except Exception as exc:  # noqa: BLE001 - a broken rule is a failed check with an error, not a crash
        return RuleEval(rule, False, 0, 0, 0.0, [], "", error=f"{type(exc).__name__}: {str(exc).splitlines()[0]}")


def enabled_rules(db: Session, ds: Dataset | None) -> list[QualityRule]:
    if ds is None:
        return []
    return list(db.scalars(select(QualityRule).where(QualityRule.dataset_id == ds.id, QualityRule.enabled.is_(True))
                           .order_by(QualityRule.created_at)))


def apply_rules(db: Session, ds: Dataset | None, table: pa.Table, log=None) -> Outcome:
    """Evaluate the dataset's enabled rules on `table` (data about to be written)."""
    rules = enabled_rules(db, ds)
    if not rules:
        return Outcome(table=table, evals=[])
    con = duckdb.connect()
    try:
        con.register("t_src", table.append_column("__rowid", pa.array(range(table.num_rows), type=pa.int64())))
        con.execute("CREATE TEMP VIEW t AS SELECT * FROM t_src")
        con.execute("SET enable_external_access = false")  # rule expressions cannot touch files / network
        evals = [evaluate(con, r, table.num_rows, ds.last_updated_at) for r in rules]
    finally:
        con.close()
    out = Outcome(table=table, evals=evals)
    if log:
        for e in evals:
            status = "passed" if e.passed else (f"ERROR {e.error}" if e.error else f"failed ({e.failed:,}/{e.total:,} rows)")
            log(f"Quality rule '{e.rule.name}' on {ds.qualified_name}: {status} · score {e.score}",
                level="info" if e.passed else "warning")
    blocking = [e for e in evals if not e.passed and e.rule.on_failure == "fail"]
    if blocking:
        out.blocking = "blocking rule(s) failed: " + ", ".join(f"'{e.rule.name}'" for e in blocking)
    q_ids: set[int] = set().union(*[e.failing_ids for e in evals if not e.passed and e.rule.on_failure == "quarantine"])
    if q_ids:
        mask = pa.array([i in q_ids for i in range(table.num_rows)])
        out.quarantined = table.filter(mask)
        out.table = table.filter(pc.invert(mask))
        if log:
            log(f"{len(q_ids):,} row(s) of {ds.qualified_name} set aside in quarantine", level="warning")
    return out


def persist(db: Session, ds: Dataset, outcome: Outcome, *, version: int | None, trigger: str,
            pipeline_run_id: str | None = None) -> list[QualityResult]:
    """Store results, open / update / auto-resolve anomalies, refresh the dataset score."""
    results = []
    now = utcnow()
    for e in outcome.evals:
        r = QualityResult(rule_id=e.rule.id, dataset_id=ds.id, dataset_version=version, pipeline_run_id=pipeline_run_id,
                          trigger=trigger, passed=e.passed, total_count=e.total, failed_count=e.failed, score=e.score,
                          evidence=e.evidence, check_sql=e.check_sql, error_message=e.error, executed_at=now)
        db.add(r)
        db.flush()
        results.append(r)
        issue = db.scalar(select(QualityIssue).where(QualityIssue.rule_id == e.rule.id,
                                                     QualityIssue.status.in_(("open", "acknowledged"))))
        if not e.passed:
            if e.error:
                title = f"Rule '{e.rule.name}' could not run"
            elif e.rule.rule_type in ("freshness", "row_count"):
                title = f"Check '{e.rule.name}' failed"
            else:
                title = f"{e.failed:,} row(s) fail '{e.rule.name}'"
            if issue is None:
                db.add(QualityIssue(rule_id=e.rule.id, result_id=r.id, dataset_id=ds.id, column=e.rule.column, title=title,
                                    dimension=e.rule.dimension, severity=e.rule.severity, failed_count=e.failed,
                                    evidence=e.evidence, detected_at=now, last_seen_at=now))
            else:
                issue.result_id, issue.title, issue.failed_count, issue.evidence = r.id, title, e.failed, e.evidence
                issue.occurrences += 1
                issue.last_seen_at = now
        elif issue is not None:
            issue.status = "resolved"
            issue.resolved_at = now
            issue.resolution_note = "Resolved automatically: the rule passed" + (f" on v{version}" if version else "")
    db.flush()
    recompute_score(db, ds)
    return results


def latest_results(db: Session, rule_ids: list[str]) -> dict[str, QualityResult]:
    if not rule_ids:
        return {}
    out: dict[str, QualityResult] = {}
    for r in db.scalars(select(QualityResult).where(QualityResult.rule_id.in_(rule_ids))
                        .order_by(QualityResult.executed_at.desc(), QualityResult.id)):
        out.setdefault(r.rule_id, r)
    return out


def recompute_score(db: Session, ds: Dataset) -> float | None:
    rules = [r.id for r in enabled_rules(db, ds)]
    scores = [r.score for r in latest_results(db, rules).values()]
    ds.quality_score = round(sum(scores) / len(scores), 2) if scores else None
    return ds.quality_score


def dimension_scores(db: Session, dataset_ids: list[str] | None = None) -> dict[str, float | None]:
    """Mean latest score per quality dimension (over all enabled rules, or the given datasets)."""
    q = select(QualityRule).where(QualityRule.enabled.is_(True))
    if dataset_ids is not None:
        q = q.where(QualityRule.dataset_id.in_(dataset_ids))
    rules = list(db.scalars(q))
    latest = latest_results(db, [r.id for r in rules])
    acc: dict[str, list[float]] = {}
    for r in rules:
        if r.id in latest:
            acc.setdefault(r.dimension, []).append(latest[r.id].score)
    return {d: round(sum(v) / len(v), 1) for d, v in acc.items()}


def write_quarantine(key: str, table: pa.Table) -> str:
    rows = [{k: cell(v) for k, v in r.items()} for r in table.to_pylist()]
    get_storage().put_bytes(key, "\n".join(json.dumps(r, ensure_ascii=False) for r in rows).encode("utf-8"),
                            "application/x-ndjson")
    return key


def run_on_demand(db: Session, ds: Dataset) -> list[QualityResult]:
    """Evaluate all enabled rules on the dataset's current version."""
    from app.lakehouse import catalog

    if not ds.current_version:
        raise ValidationFailed(f"{ds.qualified_name} has no data yet")
    if not enabled_rules(db, ds):
        raise ValidationFailed(f"{ds.qualified_name} has no enabled quality rules")
    dv = catalog.get_version(db, ds, None)
    outcome = apply_rules(db, ds, catalog.read_table(dv))
    return persist(db, ds, outcome, version=dv.version, trigger="manual")
