"""ETL pipelines: drafts, validation, immutable versions, previews, runs and their execution.

Lifecycle: the visual editor saves a *draft* graph → `publish` validates it (dry run in DuckDB) and freezes it
as version N → a *run* executes a snapshot of that version in the background (Celery worker) → the outputs
are checked against their quality rules and written as new Silver / Gold dataset versions → the destination nodes
(files, APIs, e-mail) are executed → automation decides what happens next
(retry, notifications, dependent pipelines — see services/automation.py).
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, ServiceUnavailable, ValidationFailed
from app.core.logging import get_correlation_id
from app.db.base import utcnow
from app.db.session import SessionLocal
from app.ingestion.schema import arrow_type_name
from app.lakehouse import catalog
from app.models import Pipeline, PipelineRun, PipelineVersion, User
from app.processing import medallion, star
from app.processing import params as P
from app.processing.engine import DuckDBEngine, NodeError, NodeResult, QualityGateFailed
from app.processing.spec import BRANCH_HANDLES, NODE_TYPES, structural_issues
from app.services.audit import audit
from app.services.quality import apply_rules, persist, write_quarantine
from app.services.runs import Cancelled, RunTracker, ensure_retry_allowed, request_cancel

EMPTY_GRAPH = {"nodes": [], "edges": []}


class RunTimeout(Exception):
    code = "run_timeout"
    retryable = True


def get_pipeline(db: Session, pipeline_id: str) -> Pipeline:
    p = db.get(Pipeline, pipeline_id)
    if p is None:
        raise NotFound("Pipeline not found")
    return p


def get_run(db: Session, run_id: str) -> PipelineRun:
    r = db.get(PipelineRun, run_id)
    if r is None:
        raise NotFound("Pipeline run not found")
    return r


def clean_graph(graph: dict | None) -> dict:
    graph = graph or EMPTY_GRAPH
    g: dict = {"nodes": [], "edges": []}
    for n in graph.get("nodes", []):
        t = n.get("type")
        g["nodes"].append({"id": str(n["id"]), "type": t,
                           "label": (n.get("label") or NODE_TYPES.get(t, ("", 0, 0, t or "node"))[3])[:60],
                           "position": n.get("position") or {"x": 0, "y": 0}, "config": n.get("config") or {}})
    for e in graph.get("edges", []):
        h = e.get("source_handle") or e.get("sourceHandle")
        g["edges"].append({"id": str(e.get("id") or f"{e['source']}-{e['target']}"),
                           "source": str(e["source"]), "target": str(e["target"]),
                           **({"source_handle": h} if h in BRANCH_HANDLES else {})})
    return g


def _logic(graph: dict) -> dict:
    """Graph without positions / labels — what matters for execution (detects real changes)."""
    return {"nodes": sorted([{k: n[k] for k in ("id", "type", "config")} for n in graph.get("nodes", [])], key=lambda n: n["id"]),
            "edges": sorted([(e["source"], e["target"], e.get("source_handle") or "") for e in graph.get("edges", [])])}


def latest_version(db: Session, p: Pipeline) -> PipelineVersion | None:
    return db.scalar(select(PipelineVersion).where(PipelineVersion.pipeline_id == p.id)
                     .order_by(PipelineVersion.version.desc()).limit(1))


def create_pipeline(db: Session, user: User, *, name: str, description: str | None, graph: dict | None) -> Pipeline:
    if db.scalar(select(Pipeline).where(func.lower(Pipeline.name) == name.lower())):
        raise Conflict(f"A pipeline named '{name}' already exists", details=[{"field": "name", "message": "name already used"}])
    p = Pipeline(name=name, description=description, graph=clean_graph(graph), owner_id=user.id, status="draft")
    db.add(p)
    db.flush()
    audit(db, actor=user, action="pipeline.create", resource_type="pipeline", resource_id=p.id, details={"name": name})
    return p


def save_draft(db: Session, p: Pipeline, graph: dict) -> Pipeline:
    p.graph = clean_graph(graph)
    flag_modified(p, "graph")
    lv = latest_version(db, p)
    p.status = "published" if lv and _logic(lv.graph) == _logic(p.graph) else "draft"
    return p


def _context(p: Pipeline | None, overrides: dict | None = None, run_id: str = "preview") -> dict:
    """Engine context of a pipeline: parameters (defaults + overrides), node state, sub-pipeline chain."""
    defs = ((p.automation or {}).get("params") or []) if p else []
    try:
        params = P.resolve(defs, overrides, run_id=run_id)
    except ValidationFailed:
        params = P.resolve(defs, None, run_id=run_id)
    return {"params": params, "state": ((p.state or {}).get("nodes") or {}) if p else {},
            "pipeline_chain": (p.id,) if p else ()}


def validate(db: Session, graph: dict, role: str | None = None, pipeline: Pipeline | None = None) -> dict:
    """Structural checks + a dry run in DuckDB (types and expressions bound, no data moved)."""
    graph = clean_graph(graph)
    issues = [i.as_dict() for i in structural_issues(graph)]
    node_schemas: dict[str, list[dict]] = {}
    structure_ok = not any(i["severity"] == "error" for i in issues)
    if structure_ok:
        engine = DuckDBEngine(db, graph, role=role, sample_rows=0, **_context(pipeline))
        try:
            res = engine.run()
            node_schemas = {k: v.schema for k, v in res.nodes.items()}
        except NodeError as exc:
            if engine.dynamic and getattr(exc, "missing_column", False):  # pivot columns are only known at run time
                names = ", ".join(f"'{v}'" for v in engine.dynamic.values())
                issues.append({"node_id": exc.node_id, "field": exc.field, "severity": "warning",
                               "message": f"Checked at run time — the columns of {names} come from the data: {exc}"})
            else:
                issues.append({"node_id": exc.node_id, "field": exc.field, "message": str(exc), "severity": "error"})
            node_schemas = {k: [{"name": n, "type": t} for n, t in s.items()] for k, s in engine.schemas.items()}
        finally:
            engine.close()
    if structure_ok:
        issues += [i.as_dict() for i in medallion.check(db, graph)]
    return {"valid": not any(i["severity"] == "error" for i in issues), "issues": issues, "node_schemas": node_schemas}


def publish(db: Session, user: User, p: Pipeline) -> PipelineVersion:
    result = validate(db, p.graph, user.role, p)
    if not result["valid"]:
        raise ValidationFailed("The pipeline is invalid — fix the highlighted nodes", details=result["issues"])
    lv = latest_version(db, p)
    if lv and _logic(lv.graph) == _logic(p.graph):  # nothing changed: keep the version, refresh the layout
        lv.graph = copy.deepcopy(p.graph)
        flag_modified(lv, "graph")
        p.status = "published"
        return lv
    v = PipelineVersion(pipeline_id=p.id, version=(lv.version + 1) if lv else 1, graph=copy.deepcopy(p.graph),
                        created_by_id=user.id)
    db.add(v)
    p.current_version = v.version
    p.status = "published"
    db.flush()
    audit(db, actor=user, action="pipeline.publish", resource_type="pipeline", resource_id=p.id, details={"version": v.version})
    return v


def preview_node(db: Session, graph: dict, node_id: str, limit: int = 50, role: str | None = None,
                 pipeline: Pipeline | None = None, params: dict | None = None) -> dict:
    """Run the node and its ancestors on the first rows of each input, return the node's rows."""
    graph = clean_graph(graph)
    if node_id not in {n["id"] for n in graph["nodes"]}:
        raise NotFound("Node not found in the graph")
    keep, frontier = {node_id}, [node_id]
    while frontier:
        cur = frontier.pop()
        for e in graph["edges"]:
            if e["target"] == cur and e["source"] not in keep:
                keep.add(e["source"])
                frontier.append(e["source"])
    sub = {"nodes": [n for n in graph["nodes"] if n["id"] in keep],
           "edges": [e for e in graph["edges"] if e["source"] in keep and e["target"] in keep]}
    if any(n["type"] == "branch" for n in sub["nodes"]):
        sub["edges"] = [e for e in sub["edges"] if e["target"] in keep]
    issues = [i for i in structural_issues(sub) if i.severity == "error" and i.node_id is not None]
    if issues:
        raise ValidationFailed("Fix the graph before previewing", details=[i.as_dict() for i in issues])
    sample = get_settings().pipeline_preview_rows
    engine = DuckDBEngine(db, sub, role=role, sample_rows=sample, **_context(pipeline, params))
    try:
        res = engine.run()
        out = engine.preview(node_id, limit)
        out["rows_in_sample"] = res.nodes[node_id].rows
        out["quality"] = res.nodes[node_id].quality
        out["note"] = f"Preview computed on the first {sample:,} rows of each input; full results may differ."
        return out
    except NodeError as exc:
        raise ValidationFailed(str(exc), details=[{"node_id": exc.node_id, "field": exc.field, "message": str(exc)}]) from exc
    finally:
        engine.close()


# ── runs ──────────────────────────────────────────────────────────────────
def enqueue(db: Session, p: Pipeline, *, user: User | None, retry_of: PipelineRun | None = None, trigger: str = "manual",
            params: dict | None = None) -> PipelineRun:
    if db.scalar(select(PipelineRun).where(PipelineRun.pipeline_id == p.id, PipelineRun.status.in_(("queued", "running")))):
        raise Conflict("This pipeline already has a queued or running run")
    if retry_of is not None:  # a retry re-runs exactly the same snapshot
        version, graph = retry_of.pipeline_version, retry_of.graph_snapshot
    else:
        lv = latest_version(db, p)
        if lv is None:
            raise ValidationFailed("Publish the pipeline before running it")
        version, graph = lv.version, lv.graph
    defs = (p.automation or {}).get("params") or []
    values = P.values(P.resolve(defs, params))  # validated now: a wrong parameter is refused before queueing
    values.pop("run_id", None)
    run = PipelineRun(trigger=trigger, params=values, artifacts=[], pipeline_id=p.id, pipeline_version=version, graph_snapshot=copy.deepcopy(graph),
                      node_status={n["id"]: {"status": "pending"} for n in graph["nodes"]}, triggered_by_id=user.id if user else None,
                      correlation_id=get_correlation_id(), attempt=(retry_of.attempt + 1) if retry_of else 1,
                      retry_of_id=retry_of.id if retry_of else None,
                      logs=[{"ts": utcnow().isoformat(timespec="seconds"), "level": "info",
                             "msg": f"Run queued for version {version} · trigger {trigger}"
                                    + (f" (retry of {retry_of.id[:8]})" if retry_of else "")
                                    + (f" · run_date {values.get('run_date')}" if trigger in ("backfill", "api") or params else "")}])
    db.add(run)
    p.last_run_at = utcnow()
    p.last_run_status = "queued"
    db.flush()
    return run


_enqueue = enqueue


def start(db: Session, run: PipelineRun) -> PipelineRun:
    """Commit the queued run, then hand it to the worker (or run it in-process in eager mode)."""
    from app.worker.dispatch import dispatch

    db.commit()
    try:
        dispatch("pipeline", run.id)
    except ServiceUnavailable as exc:
        run.status, run.finished_at = "failed", utcnow()
        run.error_class, run.error_message, run.retryable = "queue_unavailable", exc.message, True
        db.get(Pipeline, run.pipeline_id).last_run_status = "failed"
        db.commit()
        raise
    db.refresh(run)
    return run


def run_now(db: Session, user: User, p: Pipeline, params: dict | None = None) -> PipelineRun:
    publish(db, user, p)
    run = enqueue(db, p, user=user, params=params)
    audit(db, actor=user, action="pipeline.run", resource_type="pipeline", resource_id=p.id,
          details={"run_id": run.id, "version": run.pipeline_version})
    return start(db, run)


def retry(db: Session, user: User, run: PipelineRun) -> PipelineRun:
    ensure_retry_allowed(run)
    new = enqueue(db, db.get(Pipeline, run.pipeline_id), user=user, retry_of=run, trigger="retry", params=run.params)
    audit(db, actor=user, action="pipeline.retry", resource_type="pipeline_run", resource_id=run.id,
          details={"new_run_id": new.id})
    return start(db, new)


def cancel(db: Session, user: User, run: PipelineRun) -> PipelineRun:
    request_cancel(run)
    if run.status == "cancelled":
        db.get(Pipeline, run.pipeline_id).last_run_status = "cancelled"
    audit(db, actor=user, action="pipeline.cancel", resource_type="pipeline_run", resource_id=run.id)
    db.commit()
    return run


# ── execution (Celery worker) ─────────────────────────────────────────────
def execute_pipeline_run(run_id: str) -> None:
    with SessionLocal() as db:
        run = db.get(PipelineRun, run_id)
        if run is None or run.status != "queued":
            return
        p = db.get(Pipeline, run.pipeline_id)
        user = db.get(User, run.triggered_by_id or "")
        t = RunTracker(db, run, "pipeline")
        labels = {n["id"]: n.get("label") or n["type"] for n in run.graph_snapshot["nodes"]}

        def on_node(nid: str, status: str, res: NodeResult | None):
            st = dict(run.node_status.get(nid, {}))
            st["status"] = status
            st["started_at" if status == "running" else "finished_at"] = utcnow().isoformat(timespec="seconds")
            if res is not None:
                st.update(rows=res.rows, message=res.message, schema=res.schema, quality=res.quality)
            run.node_status = {**run.node_status, nid: st}
            flag_modified(run, "node_status")
            if status == "running":
                db.commit()
            else:
                t.log(f"Step '{labels[nid]}' {status}" + (f" · {res.rows:,} rows" if res and res.rows is not None else "")
                      + (f" · {res.message}" if res and res.message else ""))

        from app.services import automation
        from app.services.destinations import EffectFailed, run_effects

        if user is None:
            user = automation.acting_user(db, p, run)
        a = automation.settings_of(p)
        engine = None
        try:
            t.start()
            p.last_run_status = "running"
            db.commit()
            violations = medallion.errors(db, run.graph_snapshot)  # sub-pipelines may have changed since publication
            if violations:
                first = violations[0]
                run.node_status = {**run.node_status, first.node_id: {**run.node_status.get(first.node_id, {}), "status": "failed", "message": first.message}}
                flag_modified(run, "node_status")
                db.commit()
                raise medallion.MedallionViolation("; ".join(v.message for v in violations))
            params = P.resolve(a["params"], run.params, run_id=run.id)
            deadline = utcnow() + timedelta(minutes=a["timeout_minutes"]) if a.get("timeout_minutes") else None

            def check() -> None:
                t.check_cancel()
                if deadline and utcnow() > deadline:
                    raise RunTimeout(f"The run exceeded its {a['timeout_minutes']} min time limit and was stopped")

            engine = DuckDBEngine(db, run.graph_snapshot, role=user.role if user else None, on_node=on_node,
                                  check_cancel=check, params=params, state=(p.state or {}).get("nodes") or {},
                                  run_id=run.id, pipeline_chain=(p.id,))
            try:
                res = engine.run()
            except (NodeError, QualityGateFailed) as exc:
                nid = getattr(exc, "node_id", None) or next(
                    (k for k, v in run.node_status.items() if v.get("status") == "running"), None)
                if nid:
                    run.node_status = {**run.node_status, nid: {**run.node_status.get(nid, {}), "status": "failed",
                                                                 "message": str(exc)}}
                    flag_modified(run, "node_status")
                    db.commit()
                raise
            run.inputs = [{"dataset_id": ds.id, "qualified_name": ds.qualified_name, "version": dv.version,
                           "rows": res.nodes[nid].rows} for nid, ds, dv in res.inputs]
            records_in = sum(i["rows"] or 0 for i in run.inputs)
            if not res.new_data and a["conditions"].get("skip_if_no_new_data"):
                t.log("No new rows in the incremental / file-drop sources: nothing written (skip if no new data)")
                _save_state(p, res.state)
                t.succeed(records_in=0, records_out=0)
                p.last_run_status = "succeeded"
                db.commit()
                return

            # Quality gate: evaluate every output's registered rules BEFORE writing anything (all or nothing).
            staged = []
            for node, table in res.outputs:
                c = node["config"]
                ds = catalog.find_dataset(db, c["layer"], c["name"])
                staged.append((node, ds, apply_rules(db, ds, table, log=t.log) if ds else None, table))
            blocking = [(ds, o) for _, ds, o, _ in staged if o and o.blocking]
            if blocking:
                for _, ds, o, _ in staged:
                    if o:
                        persist(db, ds, o, version=None, trigger="pipeline", pipeline_run_id=run.id)
                db.commit()
                raise QualityGateFailed("Nothing was written — " + "; ".join(f"{ds.qualified_name}: {o.blocking}" for ds, o in blocking))

            staged = star.prepare(db, staged, t.log)  # star schema: Unknown members, foreign keys, integrity (blocks the writes)
            check()
            records_out, outputs, written = 0, [], {}
            for node, ds, outcome, table in staged:
                c = node["config"]
                data = outcome.table if outcome else table
                if outcome and outcome.quarantined is not None and outcome.quarantined.num_rows:
                    key = write_quarantine(f"quarantine/{c['layer']}_{c['name']}/{run.id}.jsonl", outcome.quarantined)
                    t.log(f"{outcome.quarantined.num_rows:,} row(s) of {c['layer']}.{c['name']} quarantined → {key}", level="warning")
                mode = c.get("mode") or "overwrite"
                if mode == "upsert" and ds is not None and ds.current_version:
                    data = _upsert(catalog.read_table(catalog.get_version(db, ds, None)), data, c.get("keys") or [])
                ds, dv = catalog.write_version(
                    db, layer=c["layer"], name=c["name"], table=data,
                    schema=[{"name": f.name, "type": arrow_type_name(f.type), "nullable": f.nullable} for f in data.schema],
                    user=user, description=c.get("description") or f"Produced by pipeline '{p.name}'", pipeline_run_id=run.id,
                    append=mode == "append", lineage=_lineage_of(run.graph_snapshot, node["id"], res.lineage))
                star.remember(ds, c)
                written[node["id"]] = (ds, dv)
                if outcome:
                    persist(db, ds, outcome, version=dv.version, trigger="pipeline", pipeline_run_id=run.id)
                if dv.schema_changes and any(dv.schema_changes.values()):
                    t.log(f"Schema change on {ds.qualified_name}: {dv.schema_changes}", level="warning")
                t.log(f"Wrote {ds.qualified_name} v{dv.version} ({dv.row_count:,} rows, {mode})")
                records_out += dv.row_count
                outputs.append({"dataset_id": ds.id, "qualified_name": ds.qualified_name, "version": dv.version,
                                "rows": dv.row_count})
            for node, q in res.quarantined:
                key = write_quarantine(f"quarantine/pipeline_{p.id}/{run.id}-{node['id']}.jsonl", q)
                t.log(f"Quality step '{labels[node['id']]}' set {q.num_rows:,} row(s) aside → {key}", level="warning")
            run.outputs = outputs
            db.commit()  # datasets are safe before any delivery starts
            if res.effects:
                check()
                run.artifacts = run_effects(db, run=run, pipeline=p, user=user, effects=res.effects, written=written,
                                            params=params, log=t.log, now=utcnow())
                flag_modified(run, "artifacts")
            _save_state(p, res.state)
            t.succeed(records_in=records_in, records_out=records_out)
            p.last_run_status = "succeeded"
            audit(db, actor=user, action="pipeline.run_succeeded", resource_type="pipeline_run", resource_id=run.id,
                  details={"outputs": [o["qualified_name"] + f" v{o['version']}" for o in outputs]})
            db.commit()
        except Cancelled:
            t.cancelled()
            _close_nodes(run, "skipped")
            p.last_run_status = "cancelled"
            db.commit()
        except Exception as exc:  # noqa: BLE001 - any failure is recorded on the run
            if isinstance(exc, EffectFailed):  # keep what was delivered before the failing destination
                nid = exc.node_id
                run.node_status = {**run.node_status, nid: {**run.node_status.get(nid, {}), "status": "failed", "message": str(exc)}}
                flag_modified(run, "node_status")
                flag_modified(run, "artifacts")
                db.commit()
            t.fail(exc)
            _close_nodes(run, "skipped")
            p.last_run_status = "failed"
            db.commit()
        finally:
            if engine:
                engine.close()
        try:
            automation.on_finished(db, db.get(PipelineRun, run_id))
        except Exception as exc:  # noqa: BLE001 - automation must never change the outcome of the run
            db.rollback()
            import logging

            logging.getLogger("orbit.automation").exception("on_finished failed for run %s: %s", run_id, exc)


def _save_state(p: Pipeline, updates: dict) -> None:
    if not updates:
        return
    st = dict(p.state or {})
    st["nodes"] = {**(st.get("nodes") or {}), **updates}
    p.state = st
    flag_modified(p, "state")


def _upsert(old, new, keys: list[str]):
    """Rows of `old` whose key is not in `new`, plus every row of `new` (columns matched by name)."""
    import duckdb

    con = duckdb.connect()
    try:
        con.register("old_t", old)
        con.register("new_t", new)
        on = " AND ".join(f'o."{k}" IS NOT DISTINCT FROM n."{k}"' for k in keys)
        return con.execute(f"SELECT o.* FROM old_t o ANTI JOIN new_t n ON {on} UNION ALL BY NAME SELECT * FROM new_t").arrow()
    finally:
        con.close()


def _close_nodes(run: PipelineRun, status: str) -> None:
    run.node_status = {nid: ({**st, "status": status if st.get("status") == "pending" else "failed"}
                             if st.get("status") in ("pending", "running") else st)
                       for nid, st in run.node_status.items()}
    flag_modified(run, "node_status")


# ── template: Bronze → Silver in one click ────────────────────────────────
IDENTIFIER_COLUMN = re.compile(r"(^id$|_id$|_key$|code|zip|postal|phone|tel)", re.I)
NUMBER_TEXT = re.compile(r"^[+-]?\d[\d\s.,]*$")
CONVERT_SHARE = 0.8  # a text column is converted when at least 80% of its values fit the type


def _is_date(value: str, formats: list[str]) -> bool:
    for fmt in formats:
        try:
            datetime.strptime(value, fmt)
            return True
        except ValueError:
            continue
    return False


def _text_columns_to_convert(table, keys: list[str]) -> tuple[list[str], list[str]]:
    """Text columns of Bronze that really hold numbers or dates. Identifiers and codes such as 01000 stay text."""
    from app.processing.transforms import DEFAULT_DATE_FORMATS, DEFAULT_NULL_TOKENS

    numbers, dates = [], []
    for field in table.schema:
        name = field.name
        if name in keys or name.startswith("_") or IDENTIFIER_COLUMN.search(name) or arrow_type_name(field.type) != "string":
            continue
        values = [v.strip() for v in table.column(name).to_pylist() if v is not None and v.strip().lower() not in DEFAULT_NULL_TOKENS]
        if not values:
            continue
        numeric = [v for v in values if NUMBER_TEXT.match(v)]
        if len(numeric) >= CONVERT_SHARE * len(values) and not any(len(v) > 1 and v[0] == "0" and v[1].isdigit() for v in numeric):
            numbers.append(name)
        elif sum(_is_date(v, DEFAULT_DATE_FORMATS) for v in values) >= CONVERT_SHARE * len(values):
            dates.append(name)
    return numbers, dates


def _missing_value_rules(schema: list[dict], keys: list[str], numbers: list[str], dates: list[str]) -> list[dict]:
    """Text → "Unknown", numbers → median. Keys, identifiers, dates and booleans stay empty: a guessed value would be false."""
    rules = []
    for c in schema:
        name = c["name"]
        if name in keys or name.startswith("_") or name in dates or re.search(r"(^id$|_id$|_key$)", name, re.I):
            continue
        if name in numbers or c.get("type") in ("integer", "double"):
            rules.append({"column": name, "strategy": "median"})
        elif c.get("type") == "string":
            rules.append({"column": name, "strategy": "value", "value": "Unknown"})
    return rules


def bronze_to_silver(db: Session, user: User, dataset_id: str, keys: list[str], name: str | None = None, silver_name: str | None = None) -> Pipeline:
    """Editable pipeline: input → clean and correct types → deduplicate → fill missing values → quality check → Silver output."""
    ds = catalog.get_dataset(db, dataset_id)
    if ds.layer != "bronze":
        raise ValidationFailed("The template starts from a Bronze dataset", details=[{"field": "dataset_id", "message": "not a Bronze dataset"}])
    columns = {c["name"] for c in ds.schema or []}
    unknown = [k for k in keys if k not in columns]
    if not keys or unknown:
        message = f"Unknown key column(s): {', '.join(unknown)}" if unknown else "Choose at least one key column"
        raise ValidationFailed(message, details=[{"field": "keys", "message": message}])
    target = silver_name or f"{ds.name}_clean"
    catalog.validate_dataset_name(target)
    numbers, dates = _text_columns_to_convert(catalog.read_table(catalog.get_version(db, ds, None), 5000), keys)
    fills = _missing_value_rules(ds.schema or [], keys, numbers, dates)
    rules = [{"name": f"{k} is filled", "type": "not_null", "column": k, "on_failure": "fail"} for k in keys]
    rules.append({"name": f"{' + '.join(keys)} is unique", "type": "unique", "column": keys[0], "on_failure": "fail",
                  "params": {"columns": keys} if len(keys) > 1 else {}})
    rules += [{"name": f"{f['column']} has no missing value", "type": "not_null", "column": f["column"], "on_failure": "warn"} for f in fills]
    steps = [("input", "dataset_input", ds.qualified_name, {"dataset_id": ds.id}),
             ("clean", "clean", "Clean", {"columns": [], "trim": True, "case": "none", "number_columns": numbers, "date_columns": dates}),
             ("dedupe", "dedupe", "Deduplicate", {"keys": keys, "order_by": ""}),
             *([("fill", "fill_missing", "Fill missing values", {"rules": fills, "order_by": ""})] if fills else []),
             ("quality", "quality_check", "Data quality", {"rules": rules}),
             ("output", "output", f"silver.{target}", {"layer": "silver", "name": target, "mode": "overwrite"})]
    graph = {"nodes": [{"id": i, "type": t, "label": label, "position": {"x": 60 + 250 * n, "y": 160}, "config": cfg}
                       for n, (i, t, label, cfg) in enumerate(steps)],
             "edges": [{"id": f"e{n}", "source": steps[n][0], "target": steps[n + 1][0]} for n in range(len(steps) - 1)]}
    return create_pipeline(db, user, name=name or f"{ds.name}: Bronze to Silver", description=f"Cleans {ds.qualified_name} into silver.{target}", graph=graph)


def _lineage_of(graph: dict, node_id: str, sources: dict) -> list[dict]:
    """Datasets and versions read by the steps upstream of a node."""
    keep, frontier = {node_id}, [node_id]
    while frontier:
        cur = frontier.pop()
        for e in graph["edges"]:
            if e["target"] == cur and e["source"] not in keep:
                keep.add(e["source"])
                frontier.append(e["source"])
    seen = {}
    for nid, pairs in sources.items():
        if nid in keep:
            for ds, dv in pairs:
                seen[(ds.id, dv.version)] = {"dataset_id": ds.id, "qualified_name": ds.qualified_name, "version": dv.version}
    return sorted(seen.values(), key=lambda x: (x["qualified_name"], x["version"]))
