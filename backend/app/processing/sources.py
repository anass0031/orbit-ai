"""Source nodes beyond `dataset_input`: incremental reads, file drops, calendars, sub-pipelines.

They run before the DuckDB lockdown (they read object storage or other pipelines).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from fnmatch import fnmatch

import pyarrow as pa

from app.lakehouse import catalog
from app.lakehouse.storage import get_storage
from app.processing import params as P
from app.processing.engine import MAX_SUBPIPELINE_DEPTH, DuckDBEngine, NodeError, NodeResult, kind
from app.services.quality import qi

FOLDER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,60}$")
DROPZONE = "dropzone"
ARROW_TYPES = {"string": pa.string(), "integer": pa.int64(), "double": pa.float64(), "boolean": pa.bool_(),
               "date": pa.date32(), "timestamp": pa.timestamp("us")}
MAX_PROCESSED_MEMORY = 5000  # processed file keys remembered per node

HOLIDAYS = {  # fixed-date public holidays; moving holidays (Easter, Eid…) go in the custom list
    "fr": {"01-01": "Jour de l'an", "05-01": "Fête du travail", "05-08": "Victoire 1945", "07-14": "Fête nationale",
           "08-15": "Assomption", "11-01": "Toussaint", "11-11": "Armistice", "12-25": "Noël"},
    "ma": {"01-01": "Nouvel an", "01-11": "Manifeste de l'indépendance", "01-14": "Nouvel an amazigh",
           "05-01": "Fête du travail", "07-30": "Fête du Trône", "08-14": "Oued Ed-Dahab",
           "08-20": "Révolution du Roi et du Peuple", "08-21": "Fête de la jeunesse", "11-06": "Marche verte",
           "11-18": "Fête de l'indépendance"},
    "us": {"01-01": "New Year's Day", "06-19": "Juneteenth", "07-04": "Independence Day", "11-11": "Veterans Day",
           "12-25": "Christmas Day"},
}


def folder_prefix(folder: str) -> str:
    if not FOLDER_RE.match(folder or ""):
        raise ValueError("Folder: lowercase letters, digits, '-' or '_'")
    return f"{DROPZONE}/{folder}/"


def _empty(schema: list[dict]) -> pa.Table:
    return pa.table({c["name"]: pa.array([], ARROW_TYPES.get(c.get("type"), pa.string())) for c in schema} or
                    {"_empty": pa.array([], pa.string())})


def render_value(eng: DuckDBEngine, text: str) -> str:
    """Raw parameter rendering (for dates, names): '{{run_date}}' → '2026-11-16'."""
    def rep(m):
        name = m.group(1)
        if name not in eng.params:
            raise KeyError(name)
        v = eng.params[name]["value"]
        return "" if v is None else str(v)
    return P.PLACEHOLDER.sub(rep, str(text or ""))


# ── incremental dataset input ───────────────────────────────────────────
def incremental_input(eng: DuckDBEngine, node: dict):
    nid, c = node["id"], node.get("config", {})
    ds, dv = eng._dataset(nid, c.get("dataset_id"), None)
    table = catalog.read_table(dv, eng.sample_rows)
    wcol = c.get("watermark_column") or ""
    if wcol not in table.column_names:
        raise NodeError(nid, f"Choose the watermark column (a date, timestamp or increasing number of {ds.qualified_name})",
                        "watermark_column")
    last = eng.node_state(nid).get("watermark") or c.get("initial_value") or None
    eng.con.register("__inc", table)
    try:
        typ = kind({r[0]: r[1] for r in eng.con.execute("DESCRIBE __inc").fetchall()}[wcol])
        target = {"integer": "DOUBLE", "double": "DOUBLE", "date": "TIMESTAMP", "timestamp": "TIMESTAMP"}.get(typ, "VARCHAR")
        where = f" WHERE CAST({qi(wcol)} AS {target}) > CAST(? AS {target})" if last else ""
        try:
            eng.con.execute(f"CREATE OR REPLACE TABLE {eng._table(nid)} AS SELECT * FROM __inc{where}", [str(last)] if last else [])
        except Exception as exc:  # noqa: BLE001 - e.g. an initial value that does not match the column type
            raise NodeError(nid, f"Cannot compare {wcol} with the watermark '{last}': {str(exc).splitlines()[0]}", "initial_value") from exc
    finally:
        eng.con.unregister("__inc")
    t = eng._table(nid)
    eng.schemas[nid] = eng._describe(t)
    rows = eng.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    if eng.real:
        eng.new_data.append(rows > 0)
        if rows:
            mx = eng.con.execute(f"SELECT max({qi(wcol)}) FROM {t}").fetchone()[0]
            eng.state_updates[nid] = {"watermark": mx.isoformat() if hasattr(mx, "isoformat") else str(mx),
                                      "dataset_version": dv.version}
    res = NodeResult(rows=rows, schema=[{"name": k, "type": v} for k, v in eng.schemas[nid].items()],
                     message=f"{ds.qualified_name} v{dv.version} · " + (f"{wcol} > {last}" if last else "first run: all rows"))
    return res, (ds, dv)


# ── file drop folder ───────────────────────────────────────────────────
def parse_file(key: str, data: bytes) -> pa.Table:
    from app.ingestion.parsers import ParseOptions, detect_format
    from app.ingestion.schema import discover_file

    name = key.rsplit("/", 1)[-1]
    fmt = detect_format(name)
    if fmt is None:
        raise ValueError(f"{name}: unsupported file type")
    return discover_file(data, fmt, name, ParseOptions()).table


def file_drop_input(eng: DuckDBEngine, node: dict) -> NodeResult:
    nid, c = node["id"], node.get("config", {})
    try:
        prefix = folder_prefix(c.get("folder") or "")
    except ValueError as exc:
        raise NodeError(nid, str(exc), "folder") from exc
    pattern = c.get("pattern") or "*"
    max_files = max(1, min(int(c.get("max_files") or 50), 500))
    st = eng.node_state(nid)
    processed = set(st.get("processed") or [])
    files = [f for f in get_storage().list_keys(prefix) if fnmatch(f["key"].rsplit("/", 1)[-1], pattern)]
    pending = [f for f in files if f["key"] not in processed][:max_files]
    chosen = pending if eng.real else (pending or files[-1:])[:1]
    tables = []
    for f in chosen:
        try:
            t = parse_file(f["key"], get_storage().get_bytes(f["key"]))
        except Exception as exc:  # noqa: BLE001
            raise NodeError(nid, f"Cannot read {f['key'].rsplit('/', 1)[-1]}: {exc}", "folder") from exc
        if eng.sample_rows:
            t = t.slice(0, eng.sample_rows)
        elif eng.dry:
            t = t.slice(0, 0)
        tables.append(t.append_column("_source_file", pa.array([f["key"].rsplit("/", 1)[-1]] * t.num_rows, pa.string())))
    if tables:
        table = pa.concat_tables(tables, promote_options="permissive")
    else:
        table = _empty(st.get("schema") or [{"name": "_source_file", "type": "string"}])
        if eng.dry and not st.get("schema"):  # empty folder: the columns are only known once a file arrives
            eng.dynamic[nid] = node.get("label") or "File drop folder"
    res = eng.load_arrow(nid, table)
    if eng.real:
        eng.new_data.append(bool(pending))
        keep = sorted(processed | {f["key"] for f in pending})[-MAX_PROCESSED_MEMORY:]
        eng.state_updates[nid] = {"processed": keep, "schema": [{"name": k, "type": kind(v)} for k, v in eng.schemas[nid].items()],
                                  "last_files": [f["key"].rsplit("/", 1)[-1] for f in pending]}
    res.message = (f"{len(chosen)} new file(s) in {prefix}" if eng.real else
                   f"{len(pending)} file(s) waiting in {prefix}" + (f" · sample from {chosen[0]['key'].rsplit('/', 1)[-1]}" if chosen else ""))
    return res


# ── calendar ────────────────────────────────────────────────────────────
def date_dimension(eng: DuckDBEngine, node: dict) -> NodeResult:
    nid, c = node["id"], node.get("config", {})
    try:
        start = date.fromisoformat(render_value(eng, c.get("start") or "")[:10])
        end = date.fromisoformat(render_value(eng, c.get("end") or "")[:10])
    except KeyError as exc:
        raise NodeError(nid, f"Unknown parameter {{{{{exc.args[0]}}}}}", "start") from exc
    except ValueError as exc:
        raise NodeError(nid, "Start and end must be dates (YYYY-MM-DD) or parameters like {{start_date}}", "start") from exc
    if end < start:
        raise NodeError(nid, "The end date is before the start date", "end")
    if (end - start).days > 366 * 50:
        raise NodeError(nid, "At most 50 years", "end")
    holidays = dict(HOLIDAYS.get(c.get("holidays") or "", {}))
    custom = {}
    for h in c.get("custom_holidays") or []:
        try:
            custom[date.fromisoformat(str(h.get("date"))[:10])] = str(h.get("name") or "Holiday")[:80]
        except ValueError as exc:
            raise NodeError(nid, f"Custom holiday '{h.get('date')}' is not a date", "custom_holidays") from exc
    days, d = [], start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    names = [custom.get(x) or holidays.get(x.strftime("%m-%d")) for x in days]
    table = pa.table({
        "date": pa.array(days, pa.date32()),
        "year": [x.year for x in days], "quarter": [(x.month - 1) // 3 + 1 for x in days], "month": [x.month for x in days],
        "month_name": [x.strftime("%B") for x in days], "iso_week": [x.isocalendar()[1] for x in days],
        "day": [x.day for x in days], "day_of_week": [x.isoweekday() for x in days], "day_name": [x.strftime("%A") for x in days],
        "is_weekend": [x.isoweekday() >= 6 for x in days], "is_holiday": [n is not None for n in names],
        "holiday_name": pa.array(names, pa.string()),
        "is_working_day": [x.isoweekday() < 6 and n is None for x, n in zip(days, names, strict=True)],
    })
    res = eng.load_arrow(nid, table)
    res.message = f"{start} → {end} · {len(days):,} days" + (f" · holidays: {c.get('holidays')}" if c.get("holidays") else "")
    return res


# ── sub-pipeline ────────────────────────────────────────────────────────
def subpipeline(eng: DuckDBEngine, node: dict) -> NodeResult:
    from sqlalchemy import select

    from app.models import Pipeline, PipelineVersion

    nid, c = node["id"], node.get("config", {})
    p = eng.db.get(Pipeline, c.get("pipeline_id") or "")
    if p is None:
        raise NodeError(nid, "Select a pipeline", "pipeline_id")
    if p.id in eng.pipeline_chain:
        raise NodeError(nid, f"'{p.name}' already runs above this step: a pipeline cannot include itself", "pipeline_id")
    if len(eng.pipeline_chain) >= MAX_SUBPIPELINE_DEPTH:
        raise NodeError(nid, f"Sub-pipelines can be nested {MAX_SUBPIPELINE_DEPTH} levels deep at most", "pipeline_id")
    q = select(PipelineVersion).where(PipelineVersion.pipeline_id == p.id)
    q = q.where(PipelineVersion.version == int(c["version"])) if c.get("version") else q.order_by(PipelineVersion.version.desc())
    v = eng.db.scalar(q.limit(1))
    if v is None:
        raise NodeError(nid, f"Publish '{p.name}' before using it as a sub-pipeline", "pipeline_id")
    graph = v.graph
    step = c.get("node_id")
    ids = {n["id"]: n for n in graph["nodes"]}
    if step not in ids:
        raise NodeError(nid, f"Choose the step of '{p.name}' whose result is used", "node_id")
    keep, frontier = {step}, [step]
    while frontier:
        cur = frontier.pop()
        for e in graph["edges"]:
            if e["target"] == cur and e["source"] not in keep:
                keep.add(e["source"])
                frontier.append(e["source"])
    from app.processing.spec import SINK_TYPES

    # the chosen step only provides data here: a destination step becomes a pass-through (nothing is written)
    sub = {"nodes": [{**n, "type": "sql", "config": {"query": "SELECT * FROM input"}}
                     if n["id"] == step and n["type"] in SINK_TYPES else n
                     for n in graph["nodes"] if n["id"] in keep],
           "edges": [e for e in graph["edges"] if e["source"] in keep and e["target"] in keep]}
    defs = ((p.automation or {}).get("params") or [])
    # the sub-pipeline uses its own parameter defaults, and the period of the calling run
    sub_params = P.resolve(defs, {"start_date": eng.params["start_date"]["value"], "end_date": eng.params["end_date"]["value"]},
                           run_date=eng.params["run_date"]["value"], run_id=eng.params["run_id"]["value"])
    child = DuckDBEngine(eng.db, sub, role=eng.role, sample_rows=eng.sample_rows, params=sub_params,
                         state=(p.state or {}).get("nodes") or {}, run_id=eng.run_id,
                         pipeline_chain=(*eng.pipeline_chain, p.id))
    try:
        try:
            sub_result = child.run()
        except NodeError as exc:
            raise NodeError(nid, f"In sub-pipeline '{p.name}', step '{ids.get(exc.node_id, {}).get('label', exc.node_id)}': {exc}",
                            "pipeline_id") from exc
        table = child.arrow(step)
        eng.lineage[nid] = [pair for pairs in sub_result.lineage.values() for pair in pairs]
    finally:
        child.close()
    res = eng.load_arrow(nid, table)
    res.message = f"{p.name} v{v.version} · step '{ids[step].get('label') or step}'"
    return res
