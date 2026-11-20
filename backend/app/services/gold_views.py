"""Business view of Gold tables: foreign keys are shown through the names held by their dimensions.

Surrogate keys are stable technical numbers. They are right for joins and meaningless to a reader, so previews, profiles
and charts follow the relationships of the star schema and display the descriptive column of each dimension instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import duckdb
import pyarrow as pa
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.serialize import jsonable
from app.ingestion.schema import arrow_type_name
from app.lakehouse import catalog
from app.lakehouse.storage import get_storage
from app.models import Dataset, DatasetVersion
from app.processing.star import UNKNOWN_LABEL

ID_NAME = re.compile(r"(^id$|_id$|^id_|_key$|_sk$|_uuid$)", re.I)
TIME_PART = re.compile(r"^(year|annee|quarter|trimestre|month|mois|iso_week|week|semaine|day|jour|day_of_week)$", re.I)
AVERAGED = re.compile(r"(price|prix|rate|taux|ratio|pct|percent|score|rating|note|avg|mean|moyenne|age)", re.I)
NAME_EXACT = ("name", "full_name", "label", "title", "nom", "libelle", "designation")
NAME_LIKE = re.compile(r"(_name|_nom|_label|_title|_libelle|_designation)$", re.I)
NOT_A_LABEL = re.compile(r"(^id$|_id$|_key$|code|email|mail|phone|tel|url|description|comment|address|adresse)", re.I)
TOP_GROUPS = 12


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def humanize(name: str) -> str:
    return re.sub(r"\s+", " ", name.replace("_", " ")).strip().capitalize()


def stem_of(dimension: str) -> str:
    return re.sub(r"^dim_", "", dimension)


def label_column(columns: list[dict], key: str | None, dimension: str = "") -> str | None:
    """The column that names a member of a dimension."""
    texts = [c["name"] for c in columns if c.get("type") == "string" and c["name"] != key and not c["name"].startswith("_")]
    for wanted in NAME_EXACT:
        if wanted in texts:
            return wanted
    named = [n for n in texts if NAME_LIKE.search(n)]
    if named:
        return named[0]
    stem = stem_of(dimension)
    for wanted in (stem, stem.rstrip("s")):
        if wanted in texts:
            return wanted
    readable = [n for n in texts if not NOT_A_LABEL.search(n)]
    if readable:
        return readable[0]
    if texts:
        return texts[0]
    return next((c["name"] for c in columns if c.get("type") in ("date", "timestamp") and c["name"] != key), None)


@dataclass
class Link:
    column: str
    dimension: Dataset
    key: str
    label: str | None
    display: str


def links_of(db: Session, ds: Dataset) -> list[Link]:
    """Foreign keys of a Gold table with the dimension, key and readable column behind each of them."""
    out: list[Link] = []
    if ds.layer != "gold":
        return out
    taken = {c["name"] for c in ds.schema or []}
    for rel in ds.relationships or []:
        dim = db.scalar(select(Dataset).where(Dataset.layer == "gold", Dataset.name == rel.get("dimension")))
        if dim is None or not dim.primary_key or not dim.current_version:
            continue
        names = {c["name"] for c in dim.schema or []}
        label = rel.get("label") if rel.get("label") in names else label_column(dim.schema or [], dim.primary_key[0], dim.name)
        display = re.sub(r"(_key|_sk|_id)$", "", rel["column"]) or rel["column"]
        if display in taken:
            display = f"{display}_name"
        taken.add(display)
        out.append(Link(rel["column"], dim, dim.primary_key[0], label, display))
    return out


def _resolve(db: Session, ds: Dataset, table: pa.Table, seen: tuple[str, ...] = ()) -> tuple[pa.Table, dict[str, dict]]:
    """`table` with its foreign keys replaced by names (and the names of the parent dimensions in a snowflake)."""
    links = [x for x in links_of(db, ds) if x.column in table.column_names and x.label and x.dimension.id not in seen]
    hidden = set(ds.primary_key or []) if ds.model_role == "dimension" else set()
    if not links and not hidden:
        return table, {}
    con = duckdb.connect()
    try:
        con.register("base", table.append_column("__row", pa.array(range(table.num_rows), pa.int64())))
        by_column = {x.column: x for x in links}
        parts, joins, info = [], [], {}
        used = set(table.column_names) - {x.column for x in links} - hidden
        for name in table.column_names:
            if name in hidden:
                continue
            link = by_column.get(name)
            if link is None:
                parts.append(f"b.{q(name)}")
                continue
            i = len(joins)
            dim_table = catalog.read_table(catalog.get_version(db, link.dimension, None))
            dim_view, dim_info = _resolve(db, Dataset(layer="gold", name=link.dimension.name, schema=link.dimension.schema,
                                                     relationships=link.dimension.relationships), dim_table, (*seen, ds.id or "", link.dimension.id))
            con.register(f"d{i}", dim_view)
            joins.append(f"LEFT JOIN d{i} ON b.{q(name)} = d{i}.{q(link.key)}")
            parts.append(f"COALESCE(CAST(d{i}.{q(link.label)} AS VARCHAR), '{UNKNOWN_LABEL}') AS {q(link.display)}")
            used.add(link.display)
            info[link.display] = {"column": name, "dimension": link.dimension.qualified_name, "label": link.label,
                                  "calendar": any(c["name"] == link.label and c.get("type") in ("date", "timestamp") for c in link.dimension.schema or [])}
            for parent, meta in dim_info.items():
                alias = parent if parent not in used else f"{link.display}_{parent}"
                parts.append(f"COALESCE(CAST(d{i}.{q(parent)} AS VARCHAR), '{UNKNOWN_LABEL}') AS {q(alias)}")
                used.add(alias)
                info[alias] = {**meta, "parent": True}
        sql = f"SELECT {', '.join(parts)} FROM base b {' '.join(joins)} ORDER BY b.__row"
        return con.execute(sql).arrow(), info
    finally:
        con.close()


def business_preview(db: Session, ds: Dataset, dv: DatasetVersion, limit: int) -> dict:
    table = catalog.read_table(dv, limit)
    view, info = _resolve(db, ds, table)
    return {"version": dv.version, "columns": view.column_names, "rows": [{k: jsonable(v) for k, v in r.items()} for r in view.to_pylist()],
            "total_rows": dv.row_count, "location": get_storage().uri(dv.location), "view": "business", "labels": info}


def annotate_profile(db: Session, ds: Dataset, table: pa.Table, columns: list[dict]) -> list[dict]:
    """Keys are identifiers: their mean or histogram means nothing, their most frequent members do when they are named."""
    links = {x.column: x for x in links_of(db, ds) if x.label}
    keys = set(ds.primary_key or [])
    view, info = _resolve(db, ds, table) if links else (table, {})
    by_column = {m["column"]: name for name, m in info.items() if not m.get("parent")}
    total = table.num_rows
    for col in columns:
        name = col["name"]
        if name not in links and name not in keys and not ID_NAME.search(name):
            continue
        for stat in ("min", "max", "mean", "stddev", "median", "histogram"):
            col.pop(stat, None)
        col["role"] = "foreign_key" if name in links else "primary_key" if name in keys else "identifier"
        if name in by_column:
            display = by_column[name]
            link = links[name]
            col.update(display_name=display, dimension=link.dimension.qualified_name, label_column=link.label)
            con = duckdb.connect()
            try:
                con.register("v", view)
                rows = con.execute(f"SELECT {q(display)}, count(*) FROM v GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 10").fetchall()
            finally:
                con.close()
            col["top_values"] = [{"value": str(v), "count": n, "pct": round(100 * n / total, 2) if total else 0} for v, n in rows]
    return columns


def _measures(ds: Dataset, table: pa.Table, links: list[Link]) -> list[str]:
    skip = {x.column for x in links} | set(ds.primary_key or [])
    return [f.name for f in table.schema if arrow_type_name(f.type) in ("integer", "double") and f.name not in skip
            and not f.name.startswith("_") and not ID_NAME.search(f.name) and not TIME_PART.match(f.name)]


def aggregation_of(measure: str) -> str:
    return "avg" if AVERAGED.search(measure) else "sum"


def _number(v):
    return round(float(v), 2) if v is not None else 0


def charts(db: Session, ds: Dataset, dv: DatasetVersion, measure: str | None = None) -> dict:
    """Ready-to-draw charts of a Gold table, always grouped by readable names."""
    table = catalog.read_table(dv)
    links = links_of(db, ds)
    measures = _measures(ds, table, links)
    view, info = _resolve(db, ds, table)
    chosen = measure if measure in measures else (measures[0] if measures else None)
    con = duckdb.connect()
    try:
        con.register("v", view)
        fn = aggregation_of(chosen) if chosen else "count"
        value = f"{fn}({q(chosen)})" if chosen else "count(*)"
        title = (("Average " + chosen.replace("_", " ")) if fn == "avg" else humanize(chosen)) if chosen else "Rows"
        kpis = [{"label": "Rows", "value": view.num_rows}]
        for m in measures[:4]:
            f = aggregation_of(m)
            kpis.append({"label": ("Average " + m.replace("_", " ")) if f == "avg" else f"Total {m.replace('_', ' ')}",
                         "value": _number(con.execute(f"SELECT {f}({q(m)}) FROM v").fetchone()[0])})
        out = []
        types = {f.name: arrow_type_name(f.type) for f in view.schema}
        calendars = [n for n, m in info.items() if m.get("calendar")]
        dates = calendars + [n for n, t in types.items() if t in ("date", "timestamp") and not n.startswith("_")]
        for name in dates[:2]:
            when = f"TRY_CAST({q(name)} AS DATE)"
            rows = con.execute(f"SELECT strftime({when}, '%Y-%m') AS m, {value} FROM v WHERE {when} IS NOT NULL GROUP BY 1 ORDER BY 1").fetchall()
            if len(rows) > 1:
                out.append({"id": f"time_{name}", "type": "line", "title": f"{title} by month", "subtitle": humanize(name),
                            "x": "Month", "y": title, "points": [{"label": m, "value": _number(v)} for m, v in rows[-36:]], "groups": len(rows)})
        groups = [n for n in info if n not in calendars]
        if not groups:
            label = None
            if ds.model_role == "dimension":
                label = label_column(ds.schema or [], (ds.primary_key or [None])[0], ds.name)
            for n, t in types.items():
                if t == "string" and n != label and not n.startswith("_") and not ID_NAME.search(n):
                    distinct = con.execute(f"SELECT count(DISTINCT {q(n)}) FROM v").fetchone()[0]
                    if 2 <= distinct <= 200:
                        groups.append(n)
        member = label_column(ds.schema or [], (ds.primary_key or [None])[0], ds.name) if ds.model_role == "dimension" else None
        where = f"WHERE {q(member)} <> '{UNKNOWN_LABEL}'" if member in types and types.get(member) == "string" else ""
        for name in groups[:6]:
            rows = con.execute(f"SELECT {q(name)}, {value} AS x FROM v {where} GROUP BY 1 ORDER BY 2 DESC NULLS LAST, 1").fetchall()
            top = [{"label": str(k) if k is not None else UNKNOWN_LABEL, "value": _number(v)} for k, v in rows[:TOP_GROUPS]]
            out.append({"id": f"by_{name}", "type": "bar", "title": f"{title} by {name.replace('_', ' ')}", "subtitle": info.get(name, {}).get("dimension"),
                        "x": humanize(name), "y": title, "points": top, "groups": len(rows)})
        return {"dataset": ds.qualified_name, "version": dv.version, "measures": measures, "measure": chosen, "aggregation": fn, "kpis": kpis, "charts": out}
    finally:
        con.close()
