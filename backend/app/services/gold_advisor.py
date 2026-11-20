"""Gold model advisor: reads every Silver table and proposes the dimensional models the data really allows.

Nothing here depends on table names. The analysis measures the data (unique keys, value overlap between columns,
functional dependencies, measures, dates), classifies each table, then assembles star and snowflake proposals, one per
group of connected facts. A proposal becomes an ordinary editable pipeline: Silver inputs → SQL → Surrogate key → Gold outputs.
"""
from __future__ import annotations

import hashlib
import itertools
import re
from dataclasses import dataclass, field
from datetime import date

import duckdb
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailed
from app.lakehouse import catalog
from app.models import Dataset, Pipeline, User
from app.processing.engine import kind
from app.services.gold_views import NAME_EXACT, NAME_LIKE, aggregation_of, label_column

SAMPLE_ROWS = 200_000
MAX_EMBEDDED_MEMBERS = 5_000
KEY_NAME = re.compile(r"(^id$|_id$|^id_|_key$|_code$|^code_|^code$|_ref$|^ref_|_no$|_num$|_number$|_uuid$|_sk$)", re.I)
FREE_TEXT = re.compile(r"(comment|description|note|remark|message|address|adresse|email|mail|phone|tel|url|link|token|password)", re.I)
TIME_PART = re.compile(r"^(year|annee|quarter|trimestre|month|mois|week|semaine|day|jour)$", re.I)
DIM_HINT = re.compile(r"(client|customer|product|produit|categor|region|city|ville|country|pays|segment|brand|marque|store|magasin|"
                      r"supplier|fournisseur|channel|canal|status|statut|type|department|departement|employee|agent|vendor|seller|vendeur)", re.I)
MEASURE_NAME = re.compile(r"(amount|montant|total|price|prix|qty|quantity|quantite|cost|cout|revenue|sales|discount|remise|tax|tva|margin|marge|fee|frais|weight|poids|duration|duree|score|rating|units)", re.I)
SUFFIX = re.compile(r"(_clean|_cleaned|_silver|_valid|_std|_standardized|_stg|_curated)$")
KEY_SUFFIX = re.compile(r"(_id|_key|_code|_ref|_no|_num|_number|_sk|_uuid)$")
SYNONYMS = {"client": "customer", "ville": "city", "produit": "product", "pays": "country", "categorie": "category",
            "fournisseur": "supplier", "magasin": "store", "marque": "brand", "canal": "channel", "statut": "status"}


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def singular(name: str) -> str:
    if name.endswith("ies") and len(name) > 4:
        return name[:-3] + "y"
    if name.endswith("s") and not name.endswith(("ss", "us")) and len(name) > 3:
        return name[:-1]
    return name


def canonical(name: str) -> str:
    base = singular(KEY_SUFFIX.sub("", name.lower()))
    return SYNONYMS.get(base, base)


@dataclass
class Col:
    name: str
    kind: str
    non_null: int
    distinct: int
    role: str = "attribute"  # key | date | measure | attribute | text | technical
    sequence: bool = False  # unique integers without gaps, like a row identifier


@dataclass
class Table:
    ds: Dataset
    view: str
    rows: int
    cols: dict[str, Col]
    stem: str
    pk: list[str] = field(default_factory=list)
    role: str = "standalone"
    reason: str = ""
    fks: list[dict] = field(default_factory=list)
    incoming: list[dict] = field(default_factory=list)

    def by_role(self, role: str) -> list[str]:
        return [c.name for c in self.cols.values() if c.role == role]

    @property
    def measures(self) -> list[str]:
        return [m for m in self.by_role("measure") if m not in self.pk]

    @property
    def dates(self) -> list[str]:
        return self.by_role("date")


def _column_role(c: Col, rows: int) -> str:
    if c.name.startswith("_"):
        return "technical"
    if c.kind in ("date", "timestamp"):
        return "date"
    if c.kind == "boolean":
        return "attribute"
    if KEY_NAME.search(c.name) and c.kind in ("integer", "string"):
        return "key"
    if c.kind in ("integer", "double"):
        if TIME_PART.match(c.name):
            return "attribute"
        if c.sequence and not MEASURE_NAME.search(c.name):
            return "key"
        return "measure"
    return "text" if FREE_TEXT.search(c.name) else "attribute" if c.kind == "string" else "technical"


def _load(con: duckdb.DuckDBPyConnection, db: Session) -> list[Table]:
    tables = []
    silver = db.scalars(select(Dataset).where(Dataset.layer == "silver").order_by(Dataset.name)).all()
    for i, ds in enumerate(silver):
        if not ds.current_version:
            continue
        view = f"t{i}"
        con.register(view, catalog.read_table(catalog.get_version(db, ds, None), SAMPLE_ROWS))
        rows = con.execute(f"SELECT count(*) FROM {view}").fetchone()[0]
        described = [(r[0], kind(r[1])) for r in con.execute(f"DESCRIBE {view}").fetchall()]
        cols = {}
        if described and rows:
            stats = con.execute("SELECT " + ", ".join(f"count({q(n)}), count(DISTINCT {q(n)})" for n, _ in described) + f" FROM {view}").fetchone()
            for j, (n, k) in enumerate(described):
                c = Col(n, k, stats[2 * j], stats[2 * j + 1])
                if k == "integer" and rows > 2 and c.distinct == c.non_null == rows:
                    low, high = con.execute(f"SELECT min({q(n)}), max({q(n)}) FROM {view}").fetchone()
                    c.sequence = high - low + 1 <= 1.2 * rows
                c.role = _column_role(c, rows)
                cols[n] = c
        tables.append(Table(ds, view, rows, cols, SUFFIX.sub("", ds.name)))
    return tables


def _primary_key(con, t: Table) -> list[str]:
    candidates = [c for c in t.cols.values() if c.role != "technical" and c.kind in ("integer", "string") and c.non_null == t.rows == c.distinct]
    if candidates:
        stem = singular(t.stem)
        last = singular(t.stem.rsplit("_", 1)[-1])  # shop_payments is identified by payment_id

        def rank(c: Col) -> tuple:
            return (c.name not in (f"{stem}_id", f"{last}_id"), c.name != "id", not c.name.endswith("_id"), c.role != "key", c.name)

        return [sorted(candidates, key=rank)[0].name]
    keys = [c.name for c in t.cols.values() if c.role == "key" and c.non_null == t.rows][:8]
    pool = keys + [d for d in t.dates if t.cols[d].non_null == t.rows][:2]
    for size in (2, 3):
        for combo in itertools.combinations(pool, size):
            if not set(combo) & set(keys):
                continue
            cols = ", ".join(q(c) for c in combo)
            if con.execute(f"SELECT count(*) FROM (SELECT DISTINCT {cols} FROM {t.view})").fetchone()[0] == t.rows:
                return list(combo)
    return []


def _names_match(column: str, target: Table) -> bool:
    if column == target.pk[0]:
        return True
    base = KEY_SUFFIX.sub("", column.lower())
    stem = singular(target.stem)
    pk_stem = KEY_SUFFIX.sub("", target.pk[0].lower())
    return (base in (stem, target.stem, pk_stem) or base.endswith(f"_{stem}") or base.endswith(f"_{pk_stem}")
            or canonical(base) == canonical(stem) or (len(base) >= 3 and stem.startswith(base)))


def _overlap(con, a: Table, col: str, b: Table, key: str) -> tuple[int, int, int]:
    """Distinct values of a.col, how many exist in b.key, and how many rows of a have no match."""
    return con.execute(f"""
        SELECT count(DISTINCT x.v), count(DISTINCT CASE WHEN y.v IS NOT NULL THEN x.v END), count(*) FILTER (WHERE y.v IS NULL)
        FROM (SELECT CAST({q(col)} AS VARCHAR) AS v FROM {a.view} WHERE {q(col)} IS NOT NULL) x
        LEFT JOIN (SELECT DISTINCT CAST({q(key)} AS VARCHAR) AS v FROM {b.view}) y ON x.v = y.v""").fetchone()


def _relationships(con, tables: list[Table]) -> list[dict]:
    found = []
    for a in tables:
        for c in a.cols.values():
            if c.role not in ("key", "attribute") or c.kind not in ("integer", "string") or not c.distinct or c.name.lower() == "id":
                continue
            best = None
            for b in tables:
                if b is a or len(b.pk) != 1 or (c.kind != b.cols[b.pk[0]].kind and c.role != "key"):
                    continue
                named = _names_match(c.name, b)
                if not named and (c.kind != "string" or c.distinct < 3 or c.role != "key"):
                    continue
                total, matched, orphans = _overlap(con, a, c.name, b, b.pk[0])
                coverage = matched / total if total else 0
                if coverage < (0.5 if named else 0.95):
                    continue
                score = (named, coverage)
                if best is None or score > best[0]:
                    one = c.distinct == c.non_null
                    best = (score, {"from_table": a.ds.name, "from_column": c.name, "to_table": b.ds.name, "to_column": b.pk[0],
                                    "cardinality": "1:1" if one else "N:1", "coverage": round(100 * coverage, 1), "orphan_rows": orphans,
                                    "confidence": "high" if named and coverage >= 0.95 else "medium",
                                    "join": f"{a.ds.name}.{c.name} = {b.ds.name}.{b.pk[0]}"})
            if best:
                found.append(best[1])
    by_name = {t.ds.name: t for t in tables}
    for r in found:
        by_name[r["from_table"]].fks.append(r)
        by_name[r["to_table"]].incoming.append(r)
    return found


def _is_entity(t: Table) -> bool:
    """A table that names things (customers, products) rather than recording events."""
    names = [c.name for c in t.cols.values() if c.role == "attribute" and c.kind == "string" and c.distinct >= 0.9 * max(c.non_null, 1)]
    return any(n in NAME_EXACT or NAME_LIKE.search(n) or canonical(n) == canonical(t.stem) for n in names)


def _embedded_candidates(t: Table) -> list[str]:
    fk_columns = {r["from_column"] for r in t.fks}
    out = []
    for c in t.cols.values():
        if c.role != "attribute" or c.kind != "string" or c.name in fk_columns or c.name in t.pk:
            continue
        if c.distinct < 2 or c.distinct > MAX_EMBEDDED_MEMBERS:
            continue
        hinted = bool(DIM_HINT.search(c.name))
        if hinted or c.distinct <= 0.9 * max(c.non_null, 1):
            out.append(c.name)
    return out


def _classify(tables: list[Table]) -> None:
    for t in tables:
        fk_cols = {r["from_column"] for r in t.fks}
        measures, dates = t.measures, t.dates
        if not t.rows:
            t.role, t.reason = "standalone", "The table is empty."
        elif len(fk_cols) >= 2 and not measures and (not t.pk or set(t.pk) <= fk_cols | set(dates)) and not _is_entity(t):
            t.role, t.reason = "bridge", "It only links other tables together: a many-to-many relationship."
        elif t.incoming:
            if measures and dates and not _is_entity(t):
                t.role, t.reason = "fact", "It records dated events with measures, and other tables give more detail about them."
            else:
                t.role, t.reason = "dimension", "Other tables reference it and it describes a business entity."
        elif measures and (t.fks or dates or _embedded_candidates(t)) and not (_is_entity(t) and not t.fks and t.pk):
            t.role, t.reason = "fact", f"It holds the measures {', '.join(measures[:4])} at a repeated business grain."
        elif t.fks and (dates or len(fk_cols) >= 2):
            t.role, t.reason = "fact", "It records events without measures (factless fact)."
        else:
            t.role, t.reason = "standalone", "No measure and no relationship with another table: it does not need a dimensional model."


# ── embedded dimensions ──────────────────────────────────────────────────
def _determines(con, t: Table, a: str, b: str) -> bool:
    pairs, members = con.execute(f"""SELECT count(DISTINCT ({q(a)}, {q(b)})), count(DISTINCT {q(a)}) FROM {t.view}
                                     WHERE {q(a)} IS NOT NULL AND {q(b)} IS NOT NULL""").fetchone()
    return members > 0 and pairs == members


def _dependencies(con, t: Table, names: list[str]) -> dict[str, set[str]]:
    """a → b when every member of a has one value of b, measured on repeated members only (chance is not evidence)."""
    deps: dict[str, set[str]] = {n: set() for n in names}
    for a, b in itertools.permutations(names, 2):
        ca, cb = t.cols[a], t.cols[b]
        if ca.non_null / max(ca.distinct, 1) >= 2 and ca.distinct >= cb.distinct and _determines(con, t, a, b):
            deps[a].add(b)
    return deps


def _dim_name(stem: str, taken: set[str], table: str = "") -> str:
    base = f"dim_{re.sub(r'[^a-z0-9_]+', '_', stem.lower()).strip('_')}"[:58]
    name, n = base, 2
    if name in taken and table:
        name = f"{base}_{table}"[:58]
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    return name


def _key_name(column: str, taken: set[str]) -> str:
    base = f"{KEY_SUFFIX.sub('', column)}_key"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}{n}", n + 1
    taken.add(name)
    return name


def _fact_name(t: Table, taken: set[str]) -> str:
    name, n = f"fact_{t.stem}"[:60], 2
    while name in taken:
        name, n = f"fact_{t.stem}_{n}"[:60], n + 1
    taken.add(name)
    return name


def _calendar_range(con, facts: list[Table]) -> tuple[str, str] | None:
    low, high = None, None
    for t in facts:
        for d in t.dates:
            mn, mx = con.execute(f"SELECT min({q(d)})::DATE, max({q(d)})::DATE FROM {t.view} WHERE year({q(d)}) BETWEEN 1900 AND 2100").fetchone()
            if mn:
                low, high = min(low or mn, mn), max(high or mx, mx)
    if not low:
        return None
    first = max(low.year, high.year - 49)
    return date(first, 1, 1).isoformat(), date(high.year, 12, 31).isoformat()


def _build_dimensions(con, tables: list[Table]) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """Every dimension of the analysis, and the links of each fact to them."""
    by_name = {t.ds.name: t for t in tables}
    facts = [t for t in tables if t.role in ("fact", "bridge")]
    dims: dict[str, dict] = {}
    taken: set[str] = set()
    table_dim: dict[str, str] = {}
    for t in [x for x in tables if x.role == "dimension" and len(x.pk) == 1]:
        name = _dim_name(t.stem, taken)
        taken.add(name)
        table_dim[t.ds.name] = name
        natural = t.pk[0]
        attrs = [c.name for c in t.cols.values() if c.role != "technical" and c.name != natural]
        schema = [{"name": c.name, "type": c.kind} for c in t.cols.values()]
        dims[name] = {"name": name, "kind": "table", "natural_key": natural, "key": f"{KEY_SUFFIX.sub('', natural)}_key", "label": label_column(schema, natural, t.stem) or natural,
                      "attributes": attrs, "sources": [{"table": t.ds.name, "dataset_id": t.ds.id, "column": natural}], "parents": [], "used_by": []}
    for t in [x for x in tables if x.role == "dimension" and x.ds.name in table_dim]:
        d = dims[table_dim[t.ds.name]]
        for r in t.fks:
            parent = table_dim.get(r["to_table"])
            if parent and parent != d["name"]:
                d["parents"].append({"dimension": parent, "column": r["from_column"], "key": f"{KEY_SUFFIX.sub('', r['from_column'])}_key", "kind": "table", "join": r["join"]})
    links: dict[str, list[dict]] = {t.ds.name: [] for t in facts}
    embedded: dict[str, list[dict]] = {}
    for t in facts:
        keys_taken: set[str] = set()
        fact_links = links[t.ds.name]
        for r in t.fks:
            target = by_name[r["to_table"]]
            if r["to_table"] in table_dim:
                fact_links.append({"dimension": table_dim[r["to_table"]], "column": r["from_column"], "key": _key_name(r["from_column"], keys_taken), "via": None, "join": r["join"],
                                   "orphan_rows": r["orphan_rows"]})
            elif target.role == "fact":
                for inherited in target.fks:
                    if inherited["to_table"] in table_dim and not any(x["column"] == inherited["from_column"] for x in fact_links):
                        fact_links.append({"dimension": table_dim[inherited["to_table"]], "column": inherited["from_column"], "key": _key_name(inherited["from_column"], keys_taken),
                                           "via": {"table": target.ds.name, "dataset_id": target.ds.id, "on": r["from_column"], "target": r["to_column"]},
                                           "join": f"{r['join']} then {inherited['join']}", "orphan_rows": inherited["orphan_rows"]})
        names = _embedded_candidates(t)
        natural_links = []
        for n in list(names):
            for dim_table, dim_name in table_dim.items():
                other = by_name[dim_table]
                same = [c.name for c in other.cols.values() if c.kind == "string" and c.distinct == c.non_null == other.rows and canonical(c.name) in (canonical(n), canonical(other.stem))]
                if same and canonical(n) in (canonical(other.stem), canonical(same[0])):
                    total, matched, orphans = _overlap(con, t, n, other, same[0])
                    if total and matched / total >= 0.8:
                        natural_links.append(n)
                        fact_links.append({"dimension": dim_name, "column": n, "key": _key_name(n, keys_taken), "lookup": {"table": dim_table, "dataset_id": other.ds.id, "column": same[0], "key": other.pk[0]},
                                           "via": None, "join": f"{t.ds.name}.{n} = {dim_table}.{same[0]}", "orphan_rows": orphans})
                        break
        names = [n for n in names if n not in natural_links]
        deps = _dependencies(con, t, names)
        merged = {b for a in names for b in deps[a] if a in deps.get(b, set()) and names.index(a) < names.index(b)}
        levels = [n for n in names if n not in merged]
        children = {b for a in levels for b in deps[a] if b in levels and a not in deps.get(b, set())}
        for n in levels:
            attrs = sorted(merged & deps[n])
            parents = sorted((b for b in deps[n] if b in levels and n not in deps.get(b, set())), key=lambda b: -t.cols[b].distinct)
            embedded.setdefault(canonical(n), []).append({"table": t, "column": n, "attributes": attrs, "parent": parents[0] if parents else None,
                                                         "ancestors": parents, "linked": n not in children})
    for stem, members in embedded.items():
        groups: list[list[dict]] = []
        for m in members:
            for g in groups:
                head = g[0]
                total, matched, _ = _overlap(con, m["table"], m["column"], head["table"], head["column"])
                if total and matched / total >= 0.3:
                    g.append(m)
                    break
            else:
                groups.append([m])
        for g in groups:
            head = g[0]
            name = _dim_name(head["column"], taken, head["table"].stem)
            taken.add(name)
            attrs = sorted({a for m in g for a in m["attributes"]})
            dims[name] = {"name": name, "kind": "embedded", "natural_key": head["column"], "key": f"{KEY_SUFFIX.sub('', head['column'])}_key", "label": head["column"],
                          "attributes": attrs, "ancestors": sorted({a for m in g for a in m["ancestors"]}),
                          "sources": [{"table": m["table"].ds.name, "dataset_id": m["table"].ds.id, "column": m["column"], "attributes": m["attributes"], "ancestors": m["ancestors"]} for m in g],
                          "parents": [], "used_by": []}
            for m in g:
                m["dimension"] = name
    for members in embedded.values():
        for m in members:
            if m["parent"]:
                parent_dim = next((x["dimension"] for ms in embedded.values() for x in ms if x["table"] is m["table"] and x["column"] == m["parent"]), None)
                d = dims[m["dimension"]]
                if parent_dim and not any(p["dimension"] == parent_dim for p in d["parents"]):
                    d["parents"].append({"dimension": parent_dim, "column": m["parent"], "key": f"{KEY_SUFFIX.sub('', m['parent'])}_key", "kind": "embedded",
                                         "join": f"{d['name']}.{m['parent']} = {parent_dim}.{m['parent']}"})
            if m["linked"]:
                t = m["table"]
                taken_keys = {x["key"] for x in links[t.ds.name]}
                links[t.ds.name].append({"dimension": m["dimension"], "column": m["column"], "key": _key_name(m["column"], taken_keys), "via": None,
                                         "join": f"{t.ds.name}.{m['column']} = {m['dimension']}.{m['column']}", "orphan_rows": 0})
    period = _calendar_range(con, facts)
    if period and any(t.dates for t in facts):
        dims["dim_time"] = {"name": "dim_time", "kind": "calendar", "natural_key": "date", "key": "date_key", "label": "date", "period": period,
                            "attributes": ["year", "quarter", "month", "month_name", "day", "day_name", "is_weekend"], "sources": [], "parents": [], "used_by": []}
        for t in facts:
            taken_keys = {x["key"] for x in links[t.ds.name]}
            for d in t.dates:
                links[t.ds.name].append({"dimension": "dim_time", "column": d, "key": _key_name(d, taken_keys), "via": None, "calendar": True,
                                         "join": f"{t.ds.name}.{d} = dim_time.date", "orphan_rows": 0})
            for r in t.fks:  # a detail line takes the dates of its header
                header = by_name[r["to_table"]]
                if header.role == "fact":
                    for d in header.dates:
                        if d not in t.cols:
                            links[t.ds.name].append({"dimension": "dim_time", "column": d, "key": _key_name(d, taken_keys), "calendar": True, "orphan_rows": 0,
                                                     "via": {"table": header.ds.name, "dataset_id": header.ds.id, "on": r["from_column"], "target": r["to_column"]},
                                                     "join": f"{r['join']} then {header.ds.name}.{d} = dim_time.date"})
    return dims, links


# ── proposals ────────────────────────────────────────────────────────────
def _closure(names: set[str], dims: dict[str, dict]) -> set[str]:
    out, todo = set(), list(names)
    while todo:
        n = todo.pop()
        if n not in out and n in dims:
            out.add(n)
            todo += [p["dimension"] for p in dims[n]["parents"]]
    return out


def _fact_spec(t: Table, name: str, links: list[dict], degenerate_ok: set[str]) -> dict:
    grain = (" + ".join(t.pk) if t.pk else "each source row")
    fk_cols = {x["column"] for x in links}
    degenerate = [c.name for c in t.cols.values() if c.role == "key" and c.name not in fk_cols and c.name not in t.pk and c.name in degenerate_ok]
    return {"name": name, "source": t.ds.qualified_name, "source_table": t.ds.name, "dataset_id": t.ds.id, "rows": t.rows, "type": t.role,
            "grain": f"One row per {grain}", "primary_key": t.pk, "degenerate": degenerate,
            "measures": [{"column": m, "aggregation": aggregation_of(m)} for m in t.measures], "links": links}


def _proposals(tables: list[Table], dims: dict[str, dict], links: dict[str, list[dict]]) -> list[dict]:
    facts = [t for t in tables if t.role in ("fact", "bridge")]
    names: set[str] = set()
    specs = {t.ds.name: _fact_spec(t, _fact_name(t, names) if t.role == "fact" else f"bridge_{t.stem}"[:60], links[t.ds.name], set(t.cols)) for t in facts}
    parent = {t.ds.name: t.ds.name for t in facts}

    def find(x: str) -> str:
        while parent[x] != x:
            x = parent[x]
        return x

    owners: dict[str, list[str]] = {}
    for t in facts:
        for x in links[t.ds.name]:
            if not x.get("calendar"):
                owners.setdefault(x["dimension"], []).append(t.ds.name)
        for r in t.fks:
            if r["to_table"] in parent:
                parent[find(t.ds.name)] = find(r["to_table"])
    for tabs in owners.values():
        for other in tabs[1:]:
            parent[find(other)] = find(tabs[0])
    groups: dict[str, list[Table]] = {}
    for t in facts:
        groups.setdefault(find(t.ds.name), []).append(t)
    out = []
    for group in groups.values():
        group_specs = [specs[t.ds.name] for t in group]
        used = _closure({x["dimension"] for s in group_specs for x in s["links"]}, dims)
        shared = sorted(d for d in used if sum(any(x["dimension"] == d for x in s["links"]) for s in group_specs) > 1)
        hierarchy = any(dims[d]["parents"] or dims[d].get("ancestors") for d in used)
        title = " + ".join(s["name"].replace("fact_", "").replace("bridge_", "") for s in group_specs)
        ident = hashlib.md5("|".join(sorted(s["name"] for s in group_specs)).encode()).hexdigest()[:8]
        for variant in ("star", "snowflake") if hierarchy else ("star",):
            variant_dims = sorted(used) if variant == "snowflake" else sorted({x["dimension"] for s in group_specs for x in s["links"]})
            model_dims = []
            for d in variant_dims:
                spec = {**dims[d], "used_by": [s["name"] for s in group_specs if any(x["dimension"] == d for x in s["links"])]}
                if variant == "star":
                    spec["flatten"] = [{**p, "parent": {k: dims[p["dimension"]][k] for k in ("natural_key", "attributes", "sources")}} for p in spec["parents"]]
                    spec["parents"] = []
                elif spec.get("ancestors"):
                    spec["ancestors"] = [p["column"] for p in spec["parents"]]
                model_dims.append(spec)
            warnings = []
            orphans = sum(x.get("orphan_rows") or 0 for s in group_specs for x in s["links"])
            if orphans:
                warnings.append(f"{orphans:,} fact row(s) reference a member that does not exist: they will be attached to the Unknown member (-1).")
            for s in group_specs:
                if not s["primary_key"]:
                    warnings.append(f"{s['name']}: no unique key was found, so its grain is each source row.")
            out.append({"id": f"{ident}_{variant}", "name": title, "schema_type": variant, "constellation": sum(1 for s in group_specs if s["type"] == "fact") > 1,
                        "facts": group_specs, "dimensions": model_dims, "shared_dimensions": shared, "warnings": warnings,
                        "summary": f"{len(group_specs)} fact table(s), {len(model_dims)} dimension(s)"})
    return out


# ── data quality signals between tables ──────────────────────────────────
def _conflicts(con, tables: list[Table]) -> list[dict]:
    """Same key in two tables, different value for the same attribute."""
    out = []
    for a, b in itertools.combinations([t for t in tables if len(t.pk) == 1], 2):
        if a.pk != b.pk:
            continue
        key = a.pk[0]
        for name in sorted(set(a.cols) & set(b.cols) - {key}):
            if a.cols[name].role == "technical":
                continue
            n = con.execute(f"""SELECT count(*) FROM {a.view} x JOIN {b.view} y ON CAST(x.{q(key)} AS VARCHAR) = CAST(y.{q(key)} AS VARCHAR)
                                WHERE CAST(x.{q(name)} AS VARCHAR) IS DISTINCT FROM CAST(y.{q(name)} AS VARCHAR)""").fetchone()[0]
            if n:
                out.append({"tables": [a.ds.name, b.ds.name], "key": key, "column": name, "conflicting_rows": n})
    return out


def _same_entities(con, tables: list[Table]) -> list[dict]:
    """Two tables naming the same members with different identifiers."""
    out = []
    for a, b in itertools.combinations([t for t in tables if len(t.pk) == 1 and t.role != "fact"], 2):
        la, lb = (label_column([{"name": c.name, "type": c.kind} for c in t.cols.values()], t.pk[0], t.stem) for t in (a, b))
        if not la or not lb or a.cols[la].kind != "string" or b.cols[lb].kind != "string":
            continue
        total, matched, _ = _overlap(con, a, la, b, lb)
        keys, same_keys, _ = _overlap(con, a, a.pk[0], b, b.pk[0])
        if total and matched / total >= 0.6 and (not keys or same_keys / keys < 0.2):
            out.append({"tables": [a.ds.name, b.ds.name], "labels": [la, lb], "shared_members": matched,
                        "message": f"{a.ds.name} and {b.ds.name} name the same members with different identifiers."})
    return out


def _dependency_report(con, tables: list[Table]) -> list[dict]:
    out = []
    for t in tables:
        names = [c.name for c in t.cols.values() if c.role == "attribute" and c.kind == "string" and c.distinct >= 2][:12]
        for a, bs in _dependencies(con, t, names).items():
            out += [{"table": t.ds.name, "determinant": a, "dependent": b} for b in sorted(bs)]
    return out


def analyze(db: Session) -> dict:
    con = duckdb.connect()
    try:
        tables = _load(con, db)
        for t in tables:
            t.pk = _primary_key(con, t) if t.rows else []
        relationships = _relationships(con, tables)
        _classify(tables)
        dims, links = _build_dimensions(con, tables)
        models = _proposals(tables, dims, links)
        bridges = [t for t in tables if t.role == "bridge"]
        for t in bridges:
            ends = sorted({r["to_table"] for r in t.fks})
            if len(ends) >= 2:
                relationships.append({"from_table": ends[0], "from_column": None, "to_table": ends[1], "to_column": None, "cardinality": "N:M",
                                      "coverage": 100.0, "orphan_rows": 0, "confidence": "high", "join": f"through {t.ds.name}"})
        return {
            "silver_tables": [{"id": t.ds.id, "name": t.ds.name, "qualified_name": t.ds.qualified_name, "rows": t.rows, "role": t.role, "reason": t.reason,
                               "primary_key": t.pk, "grain": f"One row per {' + '.join(t.pk)}" if t.pk else "No unique key found", "measures": t.measures, "dates": t.dates,
                               "attributes": [c.name for c in t.cols.values() if c.role in ("attribute", "text")],
                               "columns": [{"name": c.name, "type": c.kind, "role": c.role, "distinct": c.distinct, "empty": t.rows - c.non_null} for c in t.cols.values() if c.role != "technical"]}
                              for t in tables],
            "relationships": relationships,
            "functional_dependencies": _dependency_report(con, tables),
            "conflicts": _conflicts(con, tables),
            "same_entities": _same_entities(con, tables),
            "standalone": [{"table": t.ds.name, "reason": t.reason} for t in tables if t.role == "standalone"],
            "models": models,
        }
    finally:
        con.close()


# ── selection ────────────────────────────────────────────────────────────
def _model(analysis: dict, model_id: str) -> dict:
    model = next((m for m in analysis["models"] if m["id"] == model_id), None)
    if model is None:
        raise ValidationFailed("This proposal no longer exists: the Silver data changed. Analyse again.", details=[{"field": "model_id", "message": "unknown proposal"}])
    return model


def apply_selection(model: dict, selection: dict) -> dict:
    """The proposal restricted to what the user kept. Only names of the proposal are accepted."""
    facts = {f["name"]: f for f in model["facts"]}
    dims = {d["name"]: d for d in model["dimensions"]}
    chosen_facts = selection.get("facts") or list(facts)
    chosen_dims = selection.get("dimensions") if selection.get("dimensions") is not None else list(dims)
    unknown = [n for n in [*chosen_facts, *chosen_dims] if n not in facts and n not in dims]
    if unknown:
        raise ValidationFailed(f"Not part of this proposal: {', '.join(unknown)}", details=[{"field": "selection", "message": "unknown table"}])
    if not chosen_facts:
        raise ValidationFailed("Keep at least one fact table", details=[{"field": "facts", "message": "required"}])
    out_facts = []
    for name in chosen_facts:
        f = dict(facts[name])
        f["links"] = [x for x in f["links"] if x["dimension"] in chosen_dims]
        wanted = (selection.get("measures") or {}).get(name)
        if wanted is not None:
            bad = [m for m in wanted if m not in {x["column"] for x in f["measures"]}]
            if bad:
                raise ValidationFailed(f"{name}: unknown measure {', '.join(bad)}", details=[{"field": "measures", "message": "unknown measure"}])
            f["measures"] = [m for m in f["measures"] if m["column"] in wanted]
        out_facts.append(f)
    needed = {x["dimension"] for f in out_facts for x in f["links"]}
    grew = True
    while grew:
        more = {p["dimension"] for d in needed for p in dims[d]["parents"] if p["dimension"] in chosen_dims} - needed
        needed |= more
        grew = bool(more)
    out_dims = []
    for name in sorted(needed):
        d = dict(dims[name])
        d["parents"] = [p for p in d["parents"] if p["dimension"] in needed]
        wanted = (selection.get("attributes") or {}).get(name)
        if wanted is not None:
            bad = [a for a in wanted if a not in d["attributes"]]
            if bad:
                raise ValidationFailed(f"{name}: unknown attribute {', '.join(bad)}", details=[{"field": "attributes", "message": "unknown attribute"}])
            keep = set(wanted) | {d["label"]} | {p["column"] for p in d["parents"]} | {p["column"] for p in d.get("flatten") or []}
            d["attributes"] = [a for a in d["attributes"] if a in keep]
        label = (selection.get("labels") or {}).get(name)
        if label:
            if label not in {*d["attributes"], d["natural_key"]}:
                raise ValidationFailed(f"{name}: {label} cannot be its display name", details=[{"field": "labels", "message": "unknown column"}])
            d["label"] = label
        out_dims.append(d)
    return {**model, "facts": out_facts, "dimensions": out_dims, "marts": bool(selection.get("marts", True))}


# ── pipeline ─────────────────────────────────────────────────────────────
class Graph:
    def __init__(self):
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self.inputs: dict[str, str] = {}
        self.row = 0

    def add(self, nid: str, typ: str, label: str, config: dict, column: int) -> str:
        self.nodes.append({"id": nid, "type": typ, "label": label[:60], "position": {"x": 60 + 270 * column, "y": 60 + 130 * self.row}, "config": config})
        return nid

    def link(self, a: str, b: str) -> None:
        self.edges.append({"id": f"e{len(self.edges)}", "source": a, "target": b})

    def source(self, dataset_id: str, name: str) -> str:
        if dataset_id not in self.inputs:
            self.inputs[dataset_id] = self.add(f"in_{len(self.inputs)}", "dataset_input", f"silver.{name}", {"dataset_id": dataset_id}, 0)
        return self.inputs[dataset_id]

    def chain(self, first: str, steps: list[tuple[str, str, str, dict]]) -> str:
        cur, column = first, 2
        for nid, typ, label, config in steps:
            self.add(nid, typ, label, config, column)
            self.link(cur, nid)
            cur, column = nid, column + 1
        self.row += 1
        return cur


def _inputs(names: list[str]) -> list[str]:
    return ["input"] if len(names) == 1 else [f"input_{i + 1}" for i in range(len(names))]


def _dimension_sql(d: dict, sources: list[dict]) -> tuple[str, list[tuple[str, str]]]:
    """SQL of a dimension and the Silver tables it reads."""
    if d["kind"] == "table":
        src = d["sources"][0]
        reads = [(src["dataset_id"], src["table"])]
        cols = [d["natural_key"], *d["attributes"]]
        flatten = [p for p in d.get("flatten") or [] if p["kind"] == "table"]
        if not flatten:
            return f"SELECT {', '.join(q(c) for c in cols)} FROM input WHERE {q(d['natural_key'])} IS NOT NULL", reads
        select_parts, joins = [f"c.{q(c)}" for c in cols], []
        for i, p in enumerate(flatten[:3]):
            parent = p["parent"]
            reads.append((parent["sources"][0]["dataset_id"], parent["sources"][0]["table"]))
            alias = f"p{i}"
            joins.append(f"LEFT JOIN input_{i + 2} {alias} ON CAST(c.{q(p['column'])} AS VARCHAR) = CAST({alias}.{q(parent['natural_key'])} AS VARCHAR)")
            prefix = KEY_SUFFIX.sub("", p["column"])
            for a in parent["attributes"]:
                out = a if a not in cols else f"{prefix}_{a}"
                if out not in cols:
                    select_parts.append(f"{alias}.{q(a)} AS {q(out)}")
                    cols.append(out)
        return f"SELECT {', '.join(select_parts)} FROM input_1 c {' '.join(joins)} WHERE c.{q(d['natural_key'])} IS NOT NULL", reads
    natural = d["natural_key"]
    extras = [*d["attributes"], *[a for a in d.get("ancestors") or [] if a not in d["attributes"]]]
    reads, selects = [], []
    names = _inputs(sources)
    for name, s in zip(names, sources, strict=True):
        reads.append((s["dataset_id"], s["table"]))
        present = set(s.get("attributes") or []) | set(s.get("ancestors") or [])
        parts = [f"{q(s['column'])} AS {q(natural)}"] + [f"{q(a)} AS {q(a)}" if a in present else f"CAST(NULL AS VARCHAR) AS {q(a)}" for a in extras]
        selects.append(f"SELECT {', '.join(parts)} FROM {name} WHERE {q(s['column'])} IS NOT NULL AND lower(trim(CAST({q(s['column'])} AS VARCHAR))) <> 'unknown'")
    body = " UNION ALL ".join(selects)
    aggregated = ", ".join([q(natural), *[f"max({q(a)}) AS {q(a)}" for a in extras]])
    return f"SELECT {aggregated} FROM ({body}) GROUP BY {q(natural)}", reads


def build_graph(model: dict) -> dict:
    g = Graph()
    dims = {d["name"]: d for d in model["dimensions"]}
    sql_nodes: dict[str, str] = {}
    for d in model["dimensions"]:
        name = d["name"]
        rels = [{"column": p["key"], "dimension": p["dimension"], "label": dims[p["dimension"]]["label"]} for p in d["parents"]]
        output = {"layer": "gold", "name": name, "mode": "overwrite", "model_role": "dimension", "primary_key": [d["key"]], "relationships": rels,
                  "description": f"Dimension generated from Silver by the Gold model advisor"}
        if d["kind"] == "calendar":
            start, end = d["period"]
            first = g.add(f"cal_{name}", "date_dimension", "Calendar", {"start": start, "end": end}, 0)
            sql = g.add(f"sql_{name}", "sql", "Calendar columns", {"query": "SELECT CAST(strftime(date, '%Y%m%d') AS BIGINT) AS date_key, date, year, quarter, month, month_name, day, day_name, is_weekend FROM input"}, 1)
            g.link(first, sql)
            g.chain(sql, [(f"out_{name}", "output", f"gold.{name}", output)])
            continue
        query, reads = _dimension_sql(d, d["sources"])
        sources = [g.source(i, t) for i, t in reads]
        sql = g.add(f"sql_{name}", "sql", f"Members of {name}", {"query": query}, 1)
        sql_nodes[name] = sql
        for s in sources:
            g.link(s, sql)
        steps = [(f"pk_{name}_{i}", "surrogate_key", f"Key of {p['dimension']}", {"keys": [p["column"]], "into": p["key"]}) for i, p in enumerate(d["parents"])]
        steps.append((f"key_{name}", "surrogate_key", "Surrogate key", {"keys": [d["natural_key"]], "into": d["key"]}))
        steps.append((f"out_{name}", "output", f"gold.{name}", output))
        g.chain(sql, steps)
    for f in model["facts"]:
        name = f["name"]
        reads = [(f["dataset_id"], f["source_table"])]
        joins, parts, seen = [], [], set()

        def reader(dataset_id: str, table: str) -> int:
            if (dataset_id, table) not in reads:
                reads.append((dataset_id, table))
            return reads.index((dataset_id, table))

        links = []
        for x in f["links"]:
            if (x.get("via") or x.get("lookup")) and len(reads) >= 4:
                continue
            links.append(x)
            if x.get("via"):
                reader(x["via"]["dataset_id"], x["via"]["table"])
            elif x.get("lookup"):
                reader(x["lookup"]["dataset_id"], x["lookup"]["table"])
        names = _inputs(reads)
        alias = {i: f"j{i}" for i in range(1, len(reads))}
        joined = set()
        base = [*f["primary_key"], *f["degenerate"], *[m["column"] for m in f["measures"]]]
        for c in base:
            if c not in seen:
                parts.append(f"f.{q(c)}")
                seen.add(c)
        for x in links:
            source = f"__{x['key']}"
            owner = "f"
            if x.get("via"):
                i = reads.index((x["via"]["dataset_id"], x["via"]["table"]))
                owner = alias[i]
                if i not in joined:
                    joins.append(f"LEFT JOIN {names[i]} {owner} ON CAST(f.{q(x['via']['on'])} AS VARCHAR) = CAST({owner}.{q(x['via']['target'])} AS VARCHAR)")
                    joined.add(i)
            if x.get("calendar"):
                parts.append(f"CAST(strftime({owner}.{q(x['column'])}, '%Y%m%d') AS BIGINT) AS {q(x['key'])}")
                parts.append(f"{owner}.{q(x['column'])} AS {q(source)}")
            elif x.get("lookup"):
                i = reads.index((x["lookup"]["dataset_id"], x["lookup"]["table"]))
                lk = x["lookup"]
                sub = f"(SELECT {q(lk['column'])} AS v, min({q(lk['key'])}) AS k FROM {names[i]} GROUP BY 1)"
                joins.append(f"LEFT JOIN {sub} l{i} ON f.{q(x['column'])} = l{i}.v")
                parts.append(f"l{i}.k AS {q(source)}")
            else:
                parts.append(f"{owner}.{q(x['column'])} AS {q(source)}")
        if not parts:
            parts = ["f.*"]
        query = f"SELECT {', '.join(parts)} FROM {names[0]} f {' '.join(joins)}".strip()
        sources = [g.source(i, t) for i, t in reads]
        sql = g.add(f"sql_{name}", "sql", f"Rows of {name}", {"query": query}, 1)
        for s in sources:
            g.link(s, sql)
        steps = [(f"k{n}_{name}", "surrogate_key", f"Key of {x['dimension']}", {"keys": [f"__{x['key']}"], "into": x["key"]}) for n, x in enumerate(links) if not x.get("calendar")]
        final = [*dict.fromkeys([*f["primary_key"], *f["degenerate"], *[x["key"] for x in links], *[m["column"] for m in f["measures"]]])]
        steps.append((f"sel_{name}", "select", "Fact columns", {"columns": [{"name": c} for c in final]}))
        rels = [{"column": x["key"], "dimension": x["dimension"], "label": dims[x["dimension"]]["label"]} for x in links]
        steps.append((f"out_{name}", "output", f"gold.{name}", {"layer": "gold", "name": name, "mode": "overwrite", "model_role": "fact", "primary_key": f["primary_key"], "relationships": rels,
                                                               "description": f"{f['grain']}. Generated from {f['source']} by the Gold model advisor"}))
        g.chain(sql, steps)
        if model.get("marts") and f["type"] == "fact":
            _mart(g, f, links, dims, sql, sql_nodes)
    return {"nodes": g.nodes, "edges": g.edges}


def _mart(g: Graph, f: dict, links: list[dict], dims: dict, fact_sql: str, sql_nodes: dict[str, str]) -> None:
    """KPIs of a fact by month and by its main dimension, with readable names."""
    named = next((x for x in links if not x.get("calendar") and x["dimension"] in sql_nodes), None)
    when = next((x for x in links if x.get("calendar")), None)
    if not named and not when:
        return
    measures = [m for m in f["measures"]][:4]
    select_parts, group = [], []
    joins = ""
    inputs = [fact_sql]
    if named:
        d = dims[named["dimension"]]
        inputs.append(sql_nodes[named["dimension"]])
        joins = f" LEFT JOIN input_2 d ON CAST(f.{q('__' + named['key'])} AS VARCHAR) = CAST(d.{q(d['natural_key'])} AS VARCHAR)"
        select_parts.append(f"COALESCE(CAST(d.{q(d['label'])} AS VARCHAR), 'Unknown') AS {q(d['label'])}")
        group.append(q(d["label"]))
    if when:
        select_parts.append(f"CAST(date_trunc('month', f.{q('__' + when['key'])}) AS DATE) AS month")
        group.append("month")
    select_parts.append("count(*) AS row_count")
    select_parts += [f"round({m['aggregation']}(f.{q(m['column'])}), 2) AS {q(m['aggregation'] + '_' + m['column'])}" for m in measures]
    source = "input_1" if named else "input"
    query = f"SELECT {', '.join(select_parts)} FROM {source} f{joins} GROUP BY {', '.join(group)}"
    name = f"mart_{f['name'].replace('fact_', '')}_kpis"[:60]
    sql = g.add(f"sql_{name}", "sql", "KPI calculation", {"query": query}, 1)
    for i in inputs:
        g.link(i, sql)
    g.chain(sql, [(f"out_{name}", "output", f"gold.{name}", {"layer": "gold", "name": name, "mode": "overwrite", "model_role": "mart",
                                                            "description": f"KPIs of {f['name']} generated by the Gold model advisor"})])


def preview(db: Session, user: User, model_id: str, selection: dict) -> dict:
    from app.services import pipelines as svc

    model = apply_selection(_model(analyze(db), model_id), selection)
    graph = build_graph(model)
    check = svc.validate(db, graph, user.role)
    tables = []
    for n in graph["nodes"]:
        if n["type"] != "output":
            continue
        c = n["config"]
        rows, error = [], None
        if check["valid"]:
            try:
                rows = svc.preview_node(db, graph, n["id"], 5, user.role)["rows"]
            except Exception as exc:  # noqa: BLE001 - one table that cannot be sampled must not hide the others
                error = str(exc)
        tables.append({"name": c["name"], "role": c.get("model_role"), "primary_key": c.get("primary_key") or [], "relationships": c.get("relationships") or [],
                       "columns": check["node_schemas"].get(n["id"], []), "sample": rows, "error": error})
    return {"model": model, "valid": check["valid"], "issues": check["issues"], "tables": tables}


def generate(db: Session, user: User, model_id: str, selection: dict, name: str | None, run: bool) -> dict:
    from app.services import pipelines as svc

    model = apply_selection(_model(analyze(db), model_id), selection)
    graph = build_graph(model)
    title = (name or f"Gold model: {model['name']} ({model['schema_type']})")[:120]
    existing = db.scalar(select(Pipeline).where(Pipeline.name == title))
    description = f"{model['schema_type'].capitalize()} schema generated from Silver by the Gold model advisor"
    if existing is not None and (existing.description or "") != description:
        raise ValidationFailed(f"A pipeline named '{title}' already exists", details=[{"field": "name", "message": "name already used"}])
    p = svc.save_draft(db, existing, graph) if existing is not None else svc.create_pipeline(db, user, name=title, description=description, graph=graph)
    db.flush()
    if not run:
        db.commit()
        return {"pipeline_id": p.id, "pipeline_name": p.name, "run": None, "tables": [n["config"]["name"] for n in graph["nodes"] if n["type"] == "output"]}
    r = svc.run_now(db, user, p)
    return {"pipeline_id": p.id, "pipeline_name": p.name, "run": {"id": r.id, "status": r.status, "error": r.error_message},
            "tables": [n["config"]["name"] for n in graph["nodes"] if n["type"] == "output"]}
