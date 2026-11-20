"""Analyst tools on the catalogue: column profiles, version comparison, documentation, glossary and search.

Profiles and comparisons are computed by DuckDB over the immutable Parquet versions (nothing is stored).
"""
from __future__ import annotations

import re

import duckdb
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.serialize import jsonable
from app.ingestion.schema import diff_schema
from app.lakehouse import catalog
from app.models import Dataset, GlossaryTerm, User
from app.processing.engine import kind
from app.services.audit import audit
from app.services.quality import qi

TAG_RE = re.compile(r"^[a-z0-9][a-z0-9 _-]{0,29}$")
HIST_BINS = 10
TOP_VALUES = 10


def readable(db: Session, user: User, dataset_id: str) -> Dataset:
    ds = db.get(Dataset, dataset_id)
    if ds is None:
        raise NotFound("Dataset not found")
    return ds


def _con(table) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.register("__t", table)
    con.execute("CREATE TABLE t AS SELECT * FROM __t")
    con.unregister("__t")
    con.execute("SET enable_external_access = false")
    return con


# ── profile ──────────────────────────────────────────────────────────────
def profile(db: Session, user: User, ds: Dataset, version: int | None) -> dict:
    dv = catalog.get_version(db, ds, version)
    source = catalog.read_table(dv)
    con = _con(source)
    try:
        total = con.execute("SELECT count(*) FROM t").fetchone()[0]
        out = []
        docs = ds.column_docs or {}
        for name, typ in [(r[0], r[1]) for r in con.execute("DESCRIBE t").fetchall()]:
            x, k = qi(name), kind(typ)
            nulls, distinct = con.execute(f"SELECT count(*) - count({x}), count(DISTINCT {x}) FROM t").fetchone()
            col = {"name": name, "type": k, "nulls": nulls, "null_pct": round(100 * nulls / total, 2) if total else 0,
                   "distinct": distinct, "unique": total > 0 and distinct == total - nulls, "description": docs.get(name)}
            if k in ("integer", "double"):
                mn, mx, mean, sd, med = con.execute(f"SELECT min({x}), max({x}), avg({x}), stddev_pop({x}), median({x}) FROM t").fetchone()
                col.update(min=jsonable(mn), max=jsonable(mx), mean=jsonable(mean), stddev=jsonable(sd), median=jsonable(med))
                if mn is not None and mx is not None and mx > mn:
                    w = (mx - mn) / HIST_BINS
                    rows = con.execute(f"SELECT least(floor(({x} - {mn}) / {w}), {HIST_BINS - 1})::INT AS b, count(*) FROM t "
                                       f"WHERE {x} IS NOT NULL GROUP BY 1 ORDER BY 1").fetchall()
                    counts = dict(rows)
                    col["histogram"] = [{"from": jsonable(mn + i * w), "to": jsonable(mn + (i + 1) * w), "count": counts.get(i, 0)}
                                        for i in range(HIST_BINS)]
            elif k in ("date", "timestamp"):
                mn, mx = con.execute(f"SELECT min({x}), max({x}) FROM t").fetchone()
                col.update(min=jsonable(mn), max=jsonable(mx))
                rows = con.execute(f"SELECT strftime(date_trunc('month', {x}), '%Y-%m') AS m, count(*) FROM t WHERE {x} IS NOT NULL "
                                   "GROUP BY 1 ORDER BY 1").fetchall()
                col["by_month"] = [{"month": m, "count": n} for m, n in rows[-36:]]
            if k in ("string", "boolean") or (k == "integer" and distinct <= 20):
                rows = con.execute(f"SELECT CAST({x} AS VARCHAR) AS v, count(*) AS n FROM t WHERE {x} IS NOT NULL GROUP BY 1 "
                                   f"ORDER BY 2 DESC, 1 LIMIT {TOP_VALUES}").fetchall()
                col["top_values"] = [{"value": v, "count": n, "pct": round(100 * n / total, 2) if total else 0} for v, n in rows]
                if k == "string":
                    mn, mx, avg = con.execute(f"SELECT min(length({x})), max(length({x})), avg(length({x})) FROM t").fetchone()
                    col.update(min_length=mn, max_length=mx, avg_length=jsonable(avg))
            out.append(col)
        from app.services import gold_views

        return {"dataset": ds.qualified_name, "version": dv.version, "rows": total, "columns": gold_views.annotate_profile(db, ds, source, out)}
    finally:
        con.close()


# ── version comparison ───────────────────────────────────────────────────
def diff(db: Session, user: User, ds: Dataset, v_from: int, v_to: int, keys: list[str] | None, sample: int = 20) -> dict:
    a, b = catalog.get_version(db, ds, v_from), catalog.get_version(db, ds, v_to)
    ta, tb = catalog.read_table(a), catalog.read_table(b)
    con = duckdb.connect()
    try:
        con.register("va", ta)
        con.register("vb", tb)
        common = [c for c in ta.column_names if c in tb.column_names and not c.startswith("_")]
        schema = diff_schema(a.schema or [], b.schema or [])
        out = {"dataset": ds.qualified_name, "from": a.version, "to": b.version, "rows_from": ta.num_rows, "rows_to": tb.num_rows,
               "schema": schema, "compared_columns": common}
        if not common:
            return {**out, "note": "No column in common"}
        cols = ", ".join(qi(c) for c in common)
        if keys:
            bad = [k for k in keys if k not in common]
            if bad:
                raise ValidationFailed(f"Key column(s) not in both versions: {', '.join(bad)}", details=[{"field": "keys", "message": "unknown"}])
            on = " AND ".join(f"a.{qi(k)} IS NOT DISTINCT FROM b.{qi(k)}" for k in keys)
            others = [c for c in common if c not in keys]
            changed_cond = " OR ".join(f"a.{qi(c)} IS DISTINCT FROM b.{qi(c)}" for c in others) or "FALSE"
            q = {
                "added": f"SELECT b.* FROM vb b ANTI JOIN va a ON {on}",
                "removed": f"SELECT a.* FROM va a ANTI JOIN vb b ON {on}",
                "changed": f"SELECT b.* FROM vb b JOIN va a ON {on} WHERE {changed_cond}",
            }
            counts = {k: con.execute(f"SELECT count(*) FROM ({v})").fetchone()[0] for k, v in q.items()}
            samples = {}
            for k, v in q.items():
                rel = con.execute(f"SELECT {', '.join('x.' + qi(c) for c in common)} FROM ({v}) x LIMIT {sample}")
                samples[k] = [dict(zip(common, map(jsonable, r), strict=True)) for r in rel.fetchall()]
            if counts["changed"]:  # which columns changed, and how often
                per = con.execute("SELECT " + ", ".join(f"count(*) FILTER (WHERE a.{qi(c)} IS DISTINCT FROM b.{qi(c)})" for c in others)
                                  + f" FROM vb b JOIN va a ON {on}").fetchone()
                out["changed_columns"] = {c: n for c, n in zip(others, per, strict=True) if n}
            return {**out, "mode": "keys", "keys": keys, "counts": {**counts, "unchanged": tb.num_rows - counts["added"] - counts["changed"]},
                    "samples": samples}
        only_b = con.execute(f"SELECT count(*) FROM (SELECT {cols} FROM vb EXCEPT ALL SELECT {cols} FROM va)").fetchone()[0]
        only_a = con.execute(f"SELECT count(*) FROM (SELECT {cols} FROM va EXCEPT ALL SELECT {cols} FROM vb)").fetchone()[0]
        samples = {
            "added": [dict(zip(common, map(jsonable, r), strict=True)) for r in con.execute(
                f"SELECT {cols} FROM vb EXCEPT ALL SELECT {cols} FROM va LIMIT {sample}").fetchall()],
            "removed": [dict(zip(common, map(jsonable, r), strict=True)) for r in con.execute(
                f"SELECT {cols} FROM va EXCEPT ALL SELECT {cols} FROM vb LIMIT {sample}").fetchall()],
        }
        return {**out, "mode": "rows", "counts": {"added": only_b, "removed": only_a, "unchanged": tb.num_rows - only_b},
                "samples": samples, "note": "Without a key, a modified row counts as one removed + one added row."}
    finally:
        con.close()


# ── documentation ────────────────────────────────────────────────────────
def document(db: Session, user: User, ds: Dataset, *, description: str | None = None, tags: list[str] | None = None,
             columns: dict[str, str] | None = None) -> Dataset:
    changed = {}
    if description is not None:
        ds.description = description.strip()[:2000] or None
        changed["description"] = True
    if tags is not None:
        clean = sorted({t.strip().lower() for t in tags if t.strip()})
        bad = [t for t in clean if not TAG_RE.match(t)]
        if bad or len(clean) > 20:
            raise ValidationFailed(f"Tags: up to 20, lowercase, 30 characters max ({bad[0] if bad else 'too many'})",
                                   details=[{"field": "tags", "message": "invalid tag"}])
        ds.tags = clean or None
        changed["tags"] = clean
    if columns is not None:
        names = {c["name"] for c in ds.schema or []}
        unknown = [c for c in columns if c not in names]
        if unknown:
            raise ValidationFailed(f"Unknown column(s): {', '.join(unknown[:5])}", details=[{"field": "columns", "message": "unknown column"}])
        docs = dict(ds.column_docs or {})
        for c, text in columns.items():
            if (text or "").strip():
                docs[c] = text.strip()[:500]
            else:
                docs.pop(c, None)
        ds.column_docs = docs or None
        changed["columns"] = sorted(columns)
    audit(db, actor=user, action="dataset.document", resource_type="dataset", resource_id=ds.id,
          details={"dataset": ds.qualified_name, **changed})
    return ds


# ── glossary ─────────────────────────────────────────────────────────────
def term_out(t: GlossaryTerm) -> dict:
    return {"id": t.id, "name": t.name, "definition": t.definition, "synonyms": t.synonyms or [], "links": t.links or [],
            "owner_id": t.owner_id, "updated_at": t.updated_at}


def _check_term(db: Session, name: str, definition: str, synonyms: list[str], links: list[dict], exclude: str | None = None):
    name = (name or "").strip()
    if not name or len(name) > 120:
        raise ValidationFailed("Give the term (120 characters max)", details=[{"field": "name", "message": "required"}])
    if not (definition or "").strip():
        raise ValidationFailed("Give a definition", details=[{"field": "definition", "message": "required"}])
    q = select(GlossaryTerm).where(func.lower(GlossaryTerm.name) == name.lower())
    if exclude:
        q = q.where(GlossaryTerm.id != exclude)
    if db.scalar(q):
        raise Conflict(f"The term '{name}' already exists", details=[{"field": "name", "message": "already exists"}])
    out = []
    for link in links or []:
        qn, column = str(link.get("dataset") or ""), link.get("column")
        layer, _, dname = qn.partition(".")
        ds = catalog.find_dataset(db, layer, dname) if dname else None
        if ds is None:
            raise ValidationFailed(f"Unknown dataset '{qn}'", details=[{"field": "links", "message": "unknown dataset"}])
        if column and column not in {c["name"] for c in ds.schema or []}:
            raise ValidationFailed(f"Unknown column '{column}' in {qn}", details=[{"field": "links", "message": "unknown column"}])
        out.append({"dataset": ds.qualified_name, **({"column": column} if column else {})})
    return name, definition.strip()[:4000], sorted({s.strip() for s in synonyms or [] if s.strip()})[:20], out


def create_term(db: Session, user: User, *, name: str, definition: str, synonyms: list[str], links: list[dict]) -> GlossaryTerm:
    name, definition, synonyms, links = _check_term(db, name, definition, synonyms, links)
    t = GlossaryTerm(name=name, definition=definition, synonyms=synonyms, links=links, owner_id=user.id)
    db.add(t)
    db.flush()
    audit(db, actor=user, action="glossary.create", resource_type="glossary_term", resource_id=t.id, details={"name": name})
    return t


def update_term(db: Session, user: User, t: GlossaryTerm, *, name: str, definition: str, synonyms: list[str], links: list[dict]) -> GlossaryTerm:
    t.name, t.definition, t.synonyms, t.links = _check_term(db, name, definition, synonyms, links, exclude=t.id)
    audit(db, actor=user, action="glossary.update", resource_type="glossary_term", resource_id=t.id, details={"name": t.name})
    return t


def terms(db: Session, q: str | None = None) -> list[dict]:
    items = db.scalars(select(GlossaryTerm).order_by(func.lower(GlossaryTerm.name))).all()
    if q:
        ql = q.lower()
        items = [t for t in items if ql in t.name.lower() or ql in t.definition.lower() or any(ql in s.lower() for s in t.synonyms or [])]
    return [term_out(t) for t in items]


# ── search ───────────────────────────────────────────────────────────────
def search(db: Session, user: User, q: str) -> dict:
    """Datasets (name, description, tags, columns, column descriptions) and glossary terms matching the words of `q`."""
    words = [w for w in re.findall(r"[a-z0-9_]+", (q or "").lower()) if len(w) >= 2]
    if not words:
        return {"datasets": [], "terms": []}
    results = []
    for d in db.scalars(select(Dataset)):
        fields = {"name": d.name, "description": d.description or "", "tags": " ".join(d.tags or []),
                  "columns": " ".join(c["name"] for c in d.schema or []), "column docs": " ".join((d.column_docs or {}).values())}
        weight = {"name": 5, "tags": 4, "description": 2, "columns": 2, "column docs": 1}
        score, why = 0, []
        for w in words:
            for f, text in fields.items():
                if w in text.lower():
                    score += weight[f]
                    why.append(f)
        if score:
            cols = [c["name"] for c in d.schema or [] if any(w in c["name"].lower() or w in ((d.column_docs or {}).get(c["name"]) or "").lower()
                                                            for w in words)]
            results.append({"id": d.id, "qualified_name": d.qualified_name, "layer": d.layer, "description": d.description, "tags": d.tags or [],
                            "rows": d.row_count, "score": score, "matched": sorted(set(why)), "columns": cols[:10]})
    results.sort(key=lambda r: (-r["score"], r["qualified_name"]))
    found_terms = [t for t in terms(db) if any(w in (t["name"] + " " + t["definition"] + " " + " ".join(t["synonyms"])).lower() for w in words)]
    return {"datasets": results[:30], "terms": found_terms[:10]}
