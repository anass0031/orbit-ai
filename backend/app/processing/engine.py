"""DuckDB pipeline engine.

Every node is materialised as a DuckDB table `t_<node>`, in topological order (a Branch node also produces
`t_<node>__true` and `t_<node>__false`, one per port).
1. Source nodes are loaded first (lakehouse datasets, dropped files, sub-pipelines, calendars).
2. Then external access is disabled and the configuration locked: user expressions / SQL can only see the
   pipeline's own tables — no files, no network, no settings changes.

`sample_rows=0`  → dry run: types and expressions are bound and checked, no data is moved (validation).
`sample_rows=N`  → preview on the first N rows of each input.
`sample_rows=None` → real execution.

The engine never writes anything: it returns the tables to write (dataset outputs), the side effects to perform
(exports, deliveries) and the node state to keep for the next run (watermarks, processed files, schema / row-count
baselines). The pipeline service does the writing.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

import duckdb
import pyarrow as pa
import sqlglot
from sqlalchemy.orm import Session
from sqlglot import exp

from app.core.errors import NotFound
from app.lakehouse import catalog
from app.models import Dataset, DatasetVersion, QualityRule
from app.processing import params as P
from app.processing.spec import (
    AGG_FUNCS,
    EFFECT_TYPES,
    JOIN_TYPES,
    SOURCE_TYPES,
    inputs_of,
    node_of,
    nodes_by_id,
    topo_order,
)
from app.services.quality import cell, evaluate, qi, validate_rule

CAST_TYPES = {"string": "VARCHAR", "integer": "BIGINT", "double": "DOUBLE", "boolean": "BOOLEAN",
              "date": "DATE", "timestamp": "TIMESTAMP"}
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
OUTPUT_LAYERS = ("silver", "gold")
OUTPUT_MODES = ("overwrite", "append", "upsert")
MAX_SUBPIPELINE_DEPTH = 3


class NodeError(Exception):
    """A node is misconfigured or its SQL failed. Shown on that node in the editor."""

    retryable = False

    def __init__(self, node_id: str, message: str, field: str | None = None):
        super().__init__(message)
        self.node_id = node_id
        self.field = field


class QualityGateFailed(Exception):
    """A blocking (`fail`) quality check failed: nothing is written."""

    code = "quality_gate_failed"
    retryable = False

    def __init__(self, message: str, node_id: str | None = None):
        super().__init__(message)
        self.node_id = node_id


@dataclass
class NodeResult:
    rows: int | None = None
    schema: list[dict] = field(default_factory=list)
    message: str | None = None
    quality: list[dict] = field(default_factory=list)


@dataclass
class EngineResult:
    nodes: dict[str, NodeResult]
    outputs: list[tuple[dict, pa.Table]]  # (output node, data to write)
    inputs: list[tuple[str, Dataset, DatasetVersion]]
    quarantined: list[tuple[dict, pa.Table]]  # (quality node, rows set aside)
    effects: list[tuple[dict, pa.Table]] = field(default_factory=list)  # (destination node, its input rows)
    state: dict[str, dict] = field(default_factory=dict)  # node id -> state for the next run
    new_data: bool = True  # False when every incremental / file-drop source had nothing new
    lineage: dict[str, list] = field(default_factory=dict)  # source node id -> [(dataset, version)] it read


def kind(duck_type: str) -> str:
    """DuckDB type name → ORBIT logical type."""
    t = duck_type.upper()
    if t.startswith(("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT")):
        return "integer"
    if t.startswith(("DOUBLE", "FLOAT", "REAL", "DECIMAL")):
        return "double"
    if t.startswith("BOOLEAN"):
        return "boolean"
    if t == "DATE":
        return "date"
    if t.startswith("TIMESTAMP"):
        return "timestamp"
    if t.startswith("VARCHAR"):
        return "string"
    return t.lower()


def check_expression(node_id: str, expr_sql: str, field_name: str) -> None:
    if not expr_sql or not expr_sql.strip():
        raise NodeError(node_id, "Expression is required", field_name)
    if ";" in expr_sql:
        raise NodeError(node_id, "Semicolons are not allowed", field_name)
    try:
        parsed = sqlglot.parse_one(expr_sql, read="duckdb")
    except sqlglot.errors.ParseError as exc:
        raise NodeError(node_id, f"Invalid expression: {str(exc).splitlines()[0]}", field_name) from exc
    if any(isinstance(n, exp.Select | exp.Subquery) for n in parsed.walk()):
        raise NodeError(node_id, "Sub-queries are not allowed in expressions; use a SQL node", field_name)


_check_expression = check_expression  # name used by earlier modules


def _check_sql(node_id: str, sql: str, allowed: set[str]) -> None:
    if not sql or not sql.strip():
        raise NodeError(node_id, "SQL query is required", "query")
    try:
        stmts = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise NodeError(node_id, f"SQL syntax error: {str(exc).splitlines()[0]}", "query") from exc
    if len(stmts) != 1 or not isinstance(stmts[0], exp.Select | exp.Union | exp.Except | exp.Intersect):
        raise NodeError(node_id, "Only a single SELECT statement is allowed", "query")
    ctes = {c.alias_or_name.lower() for c in stmts[0].find_all(exp.CTE)}
    for t in stmts[0].find_all(exp.Table):
        name = t.name.lower()
        if not name:  # table function in FROM, e.g. read_csv(...) — never allowed
            raise NodeError(node_id, "Table functions (read_csv, read_parquet, …) are not allowed; read data through input nodes", "query")
        if name not in allowed and name not in ctes:
            raise NodeError(node_id, f"Unknown table '{t.name}'. Use: {', '.join(sorted(allowed))}", "query")


def col(node_id: str, name: str, schema: dict[str, str], field_name: str) -> str:
    if name not in schema:
        err = NodeError(node_id, f"Column '{name}' does not exist upstream (available: {', '.join(list(schema)[:12])})", field_name)
        err.missing_column = True
        raise err
    return qi(name)


_col = col


def ident(node_id: str, name: str, field_name: str) -> str:
    if not name or not IDENT.match(name):
        raise NodeError(node_id, f"Invalid column name '{name}' (letters, digits and _; starts with a letter)", field_name)
    return qi(name)


class DuckDBEngine:
    def __init__(self, db: Session, graph: dict, *, role: str | None = None, sample_rows: int | None = None,
                 on_node: Callable[[str, str, NodeResult | None], None] | None = None,
                 check_cancel: Callable[[], None] | None = None, params: dict | None = None,
                 state: dict | None = None, run_id: str | None = None, pipeline_chain: tuple[str, ...] = ()):
        self.db, self.graph, self.role = db, graph, role
        self.sample_rows = sample_rows
        self.on_node = on_node or (lambda *_: None)
        self.check_cancel = check_cancel or (lambda: None)
        self.params = params if params is not None else P.resolve([], None)
        self.state = state or {}
        self.run_id = run_id
        self.pipeline_chain = pipeline_chain  # pipelines being executed above this one (sub-pipeline recursion guard)
        self.state_updates: dict[str, dict] = {}
        self.new_data: list[bool] = []
        self.lineage: dict[str, list] = {}
        self.dynamic: dict[str, str] = {}  # nodes whose output columns depend on the data (pivot) → later checks at run time
        self.con = duckdb.connect()
        self.con.execute("SET threads = 4")
        self.con.execute("SET memory_limit = '2GB'")
        self.schemas: dict[str, dict[str, str]] = {}

    def close(self) -> None:
        self.con.close()

    @property
    def dry(self) -> bool:
        return self.sample_rows == 0

    @property
    def real(self) -> bool:
        return self.sample_rows is None

    def _table(self, key: str) -> str:
        return f"t_{re.sub(r'[^A-Za-z0-9_]', '_', key.replace(':', '__'))}"

    def _describe(self, table: str) -> dict[str, str]:
        return {r[0]: r[1] for r in self.con.execute(f"DESCRIBE {table}").fetchall()}

    def _materialise(self, node_id: str, select_sql: str, key: str | None = None) -> NodeResult:
        key = key or node_id
        t = self._table(key)
        try:
            self.con.execute(f"CREATE OR REPLACE TABLE {t} AS {select_sql}")
        except duckdb.Error as exc:
            raise NodeError(node_id, str(exc).split("\n")[0]) from exc
        self.schemas[key] = self._describe(t)
        rows = self.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        return NodeResult(rows=rows, schema=[{"name": k, "type": v} for k, v in self.schemas[key].items()])

    def load_arrow(self, node_id: str, table: pa.Table) -> NodeResult:
        self.con.register("__input", table)
        try:
            return self._materialise(node_id, "SELECT * FROM __input")
        finally:
            self.con.unregister("__input")

    def arrow(self, key: str) -> pa.Table:
        return self.con.execute(f"SELECT * FROM {self._table(key)}").arrow()

    def sql_text(self, node_id: str, text: str, field_name: str) -> str:
        """Substitute {{parameters}} as typed SQL literals."""
        try:
            return P.sql(text, self.params)
        except KeyError as exc:
            raise NodeError(node_id, f"Unknown parameter {{{{{exc.args[0]}}}}} — declare it in Automation → Parameters", field_name) from exc

    def node_state(self, node_id: str) -> dict:
        return dict(self.state.get(node_id) or {})

    # ── sources ─────────────────────────────────────────────────────────
    def _dataset(self, node_id: str, dataset_id: str | None, version=None) -> tuple[Dataset, DatasetVersion]:
        ds = self.db.get(Dataset, dataset_id or "")
        if ds is None:
            raise NodeError(node_id, "Select a dataset", "dataset_id")
        try:
            dv = catalog.get_version(self.db, ds, int(version) if version else None)
        except (NotFound, ValueError) as exc:
            raise NodeError(node_id, str(getattr(exc, "message", exc)), "version") from exc
        return ds, dv

    def _load_input(self, node: dict) -> tuple[NodeResult, Dataset, DatasetVersion]:
        c = node.get("config", {})
        ds, dv = self._dataset(node["id"], c.get("dataset_id"), c.get("version"))
        res = self.load_arrow(node["id"], catalog.read_table(dv, self.sample_rows))
        res.message = f"{ds.qualified_name} v{dv.version}" + ("" if c.get("version") else " (latest)")
        return res, ds, dv

    def _load_source(self, node: dict) -> tuple[NodeResult, tuple[Dataset, DatasetVersion] | None]:
        from app.processing import sources

        nid, t = node["id"], node["type"]
        if t == "incremental_input":
            return sources.incremental_input(self, node)
        if t == "file_drop_input":
            return sources.file_drop_input(self, node), None
        if t == "date_dimension":
            return sources.date_dimension(self, node), None
        if t == "subpipeline":
            return sources.subpipeline(self, node), None
        raise NodeError(nid, f"Unsupported source {t}")

    # ── node builders ───────────────────────────────────────────────────
    def _build(self, node: dict, ups: list[str]) -> NodeResult:
        nid, t, c = node["id"], node["type"], node.get("config", {})
        up = [self._table(u) for u in ups]
        sch = self.schemas.get(ups[0], {}) if ups else {}
        if t == "filter":
            cond = self.sql_text(nid, c.get("condition", ""), "condition")
            check_expression(nid, cond, "condition")
            return self._materialise(nid, f"SELECT * FROM {up[0]} WHERE ({cond})")
        if t == "select":
            cols = c.get("columns") or []
            if not cols:
                raise NodeError(nid, "Choose at least one column", "columns")
            parts = []
            for column in cols:
                src = col(nid, column.get("name", ""), sch, "columns")
                cast = column.get("cast")
                if cast and cast not in CAST_TYPES:
                    raise NodeError(nid, f"Unsupported cast type '{cast}'", "columns")
                alias = column.get("alias") or column["name"]
                if not IDENT.match(alias):
                    raise NodeError(nid, f"Invalid column alias '{alias}'", "columns")
                expr = f"TRY_CAST({src} AS {CAST_TYPES[cast]})" if cast else src
                parts.append(f"{expr} AS {qi(alias)}")
            return self._materialise(nid, f"SELECT {', '.join(parts)} FROM {up[0]}")
        if t == "sql":
            names = ["input"] if len(up) == 1 else ["input", *[f"input_{i + 1}" for i in range(len(up))]]
            query = self.sql_text(nid, c.get("query", ""), "query")
            _check_sql(nid, query, set(names))
            for name in names:
                src = up[0] if name == "input" else up[int(name.split("_")[1]) - 1]
                self.con.execute(f"CREATE OR REPLACE TEMP VIEW {name} AS SELECT * FROM {src}")
            try:
                return self._materialise(nid, query.strip().rstrip(";"))
            finally:
                for name in names:
                    self.con.execute(f"DROP VIEW IF EXISTS {name}")
        if t == "join":
            how = (c.get("how") or "inner").lower()
            if how not in JOIN_TYPES:
                raise NodeError(nid, f"Join type must be one of {', '.join(sorted(JOIN_TYPES))}", "how")
            left_id = c.get("left") if c.get("left") in ups else ups[0]
            right_id = [u for u in ups if u != left_id][0]
            ls, rs = self.schemas[left_id], self.schemas[right_id]
            lk, rk = c.get("left_on") or [], c.get("right_on") or []
            if not lk or len(lk) != len(rk):
                raise NodeError(nid, "Choose matching left and right key columns", "left_on")
            conds = [f"l.{col(nid, a, ls, 'left_on')} = r.{col(nid, b, rs, 'right_on')}" for a, b in zip(lk, rk, strict=False)]
            sel = [f"l.{qi(k)}" for k in ls]
            for k in rs:
                if k not in rk:
                    sel.append(f"r.{qi(k)} AS {qi(k + '_right' if k in ls else k)}")
            return self._materialise(nid, f"SELECT {', '.join(sel)} FROM {self._table(left_id)} l "
                                          f"{how.upper()} JOIN {self._table(right_id)} r ON {' AND '.join(conds)}")
        if t == "union":
            op = " UNION BY NAME " if c.get("distinct") else " UNION ALL BY NAME "
            return self._materialise(nid, op.join(f"SELECT * FROM {u}" for u in up))
        if t == "aggregate":
            gb = [col(nid, g, sch, "group_by") for g in c.get("group_by") or []]
            aggs = c.get("aggregations") or []
            if not aggs:
                raise NodeError(nid, "Add at least one aggregation", "aggregations")
            parts = list(gb)
            for a in aggs:
                fn = (a.get("func") or "").lower()
                if fn not in AGG_FUNCS:
                    raise NodeError(nid, f"Unsupported aggregate '{fn}'", "aggregations")
                alias = a.get("alias") or f"{fn}_{a.get('column') or 'rows'}"
                if not IDENT.match(alias):
                    raise NodeError(nid, f"Invalid alias '{alias}'", "aggregations")
                if fn == "count" and a.get("column") in (None, "", "*"):
                    parts.append(f"count(*) AS {qi(alias)}")
                elif fn == "count_distinct":
                    parts.append(f"count(DISTINCT {col(nid, a['column'], sch, 'aggregations')}) AS {qi(alias)}")
                else:
                    parts.append(f"{fn}({col(nid, a.get('column', ''), sch, 'aggregations')}) AS {qi(alias)}")
            group = f" GROUP BY {', '.join(gb)}" if gb else ""
            return self._materialise(nid, f"SELECT {', '.join(parts)} FROM {up[0]}{group}")
        if t == "dedupe":
            keys = [col(nid, k, sch, "keys") for k in c.get("keys") or []]
            if not keys:
                raise NodeError(nid, "Choose the key column(s) that identify a row", "keys")
            order = col(nid, c["order_by"], sch, "order_by") + " DESC" if c.get("order_by") else "1"
            return self._materialise(nid, "SELECT * EXCLUDE (__rn) FROM (SELECT *, row_number() OVER "
                                          f"(PARTITION BY {', '.join(keys)} ORDER BY {order}) AS __rn FROM {up[0]}) WHERE __rn = 1")
        if t == "output":
            layer, name = c.get("layer"), c.get("name") or ""
            if layer not in OUTPUT_LAYERS:
                raise NodeError(nid, "Output layer must be silver or gold (bronze is written by imports and ingestion)", "layer")
            if not catalog.NAME_RE.match(name):
                raise NodeError(nid, "Dataset name must be snake_case and start with a letter", "name")
            mode = c.get("mode") or "overwrite"
            if mode not in OUTPUT_MODES:
                raise NodeError(nid, f"Write mode must be one of {', '.join(OUTPUT_MODES)}", "mode")
            if mode == "upsert":
                keys = c.get("keys") or []
                if not keys:
                    raise NodeError(nid, "Upsert needs the key column(s) that identify a row", "keys")
                for k in keys:
                    col(nid, k, sch, "keys")
            for k in c.get("primary_key") or []:
                col(nid, k, sch, "primary_key")
                if c.get("model_role") == "dimension" and kind(sch[k]) != "integer":
                    raise NodeError(nid, f"The primary key {k} of a dimension must be an integer (use a Surrogate key step)", "primary_key")
            for rel in c.get("relationships") or []:
                col(nid, rel.get("column") or "", sch, "relationships")
                if kind(sch[rel["column"]]) != "integer":
                    raise NodeError(nid, f"The foreign key {rel['column']} must be an integer (use a Surrogate key step)", "relationships")
            res = self._materialise(nid, f"SELECT * FROM {up[0]}")
            res.message = f"{layer}.{name} · {mode}" + (f" · {c['model_role']}" if c.get("model_role") else "")
            return res
        from app.processing import transforms

        builder = transforms.BUILDERS.get(t)
        if builder is None:
            raise NodeError(nid, f"Unsupported node type {t}")
        return builder(self, node, ups)

    def _quality_node(self, node: dict, ups: list[str]) -> tuple[NodeResult, pa.Table | None]:
        """Inline assertions inside the pipeline (fail / quarantine / warn), evaluated on the data flowing through."""
        nid, c = node["id"], node.get("config", {})
        rules_cfg = c.get("rules") or []
        if not rules_cfg:
            raise NodeError(nid, "Add at least one quality assertion", "rules")
        up = self._table(ups[0])
        sch = self.schemas[ups[0]]
        pseudo = []
        step = Dataset(layer="silver", name="pipeline_step", schema=[{"name": k, "type": v} for k, v in sch.items()])
        for i, r in enumerate(rules_cfg):
            try:
                validate_rule(step, r.get("type", ""), r.get("column"), r.get("params") or {})
            except Exception as exc:  # noqa: BLE001
                details = getattr(exc, "details", None) or []
                msg = "; ".join(d["message"] for d in details) if details else str(exc)
                raise NodeError(nid, f"Assertion {i + 1}: {msg}", "rules") from exc
            pseudo.append(QualityRule(name=r.get("name") or f"{r['type']}({r.get('column') or ''})", rule_type=r["type"],
                                      column=r.get("column"), params=r.get("params") or {},
                                      on_failure=r.get("on_failure", "fail"), threshold=float(r.get("threshold", 1.0)),
                                      severity=r.get("severity", "medium")))
        self.con.execute(f"CREATE OR REPLACE TEMP TABLE qc_src AS SELECT *, (row_number() OVER ()) - 1 AS __rowid FROM {up}")
        self.con.execute("CREATE OR REPLACE TEMP VIEW t AS SELECT * FROM qc_src")
        total = self.con.execute("SELECT count(*) FROM qc_src").fetchone()[0]
        evals = [evaluate(self.con, r, total) for r in pseudo] if self.sample_rows != 0 else []
        summary = [{"name": e.rule.name, "type": e.rule.rule_type, "passed": e.passed, "failed": e.failed, "total": e.total,
                    "score": e.score, "on_failure": e.rule.on_failure, "evidence": e.evidence[:5], "error": e.error}
                   for e in evals]
        blocking = [e for e in evals if not e.passed and e.rule.on_failure == "fail"]
        if blocking and self.sample_rows is None:
            raise QualityGateFailed("Quality check failed: " + "; ".join(
                f"'{e.rule.name}' ({e.error or f'{e.failed} of {e.total} rows'})" for e in blocking), nid)
        ids = set().union(*[e.failing_ids for e in evals if not e.passed and e.rule.on_failure == "quarantine"]) if evals else set()
        quarantined = None
        if ids:
            self.con.execute("CREATE OR REPLACE TEMP TABLE q_ids AS SELECT unnest(?::BIGINT[]) AS __rowid", [sorted(ids)])
            quarantined = self.con.execute(
                "SELECT * EXCLUDE (__rowid) FROM qc_src WHERE __rowid IN (SELECT __rowid FROM q_ids)").arrow()
            res = self._materialise(nid, "SELECT * EXCLUDE (__rowid) FROM qc_src WHERE __rowid NOT IN (SELECT __rowid FROM q_ids)")
        else:
            res = self._materialise(nid, "SELECT * EXCLUDE (__rowid) FROM qc_src")
        res.quality = summary
        if summary:
            failed = [s for s in summary if not s["passed"]]
            res.message = f"{len(summary) - len(failed)}/{len(summary)} assertions passed" + (
                f", {len(ids)} row(s) quarantined" if ids else "")
        self.con.execute("DROP VIEW IF EXISTS t")
        return res, quarantined

    # ── run ─────────────────────────────────────────────────────────────
    def run(self) -> EngineResult:
        order = topo_order(self.graph)
        by_id = nodes_by_id(self.graph)
        results: dict[str, NodeResult] = {}
        inputs, outputs, quarantined, effects = [], [], [], []
        # Phase 1: load the sources (they may read object storage or other pipelines)
        for nid in order:
            node = by_id[nid]
            if node["type"] not in SOURCE_TYPES:
                continue
            self.check_cancel()
            self.on_node(nid, "running", None)
            if node["type"] == "dataset_input":
                res, ds, dv = self._load_input(node)
                inputs.append((nid, ds, dv))
                self.lineage[nid] = [(ds, dv)]
            else:
                res, read = self._load_source(node)
                if read:
                    inputs.append((nid, *read))
                    self.lineage[nid] = [read]
            results[nid] = res
            self.on_node(nid, "succeeded", res)
        # Phase 2: lock down — user SQL/expressions cannot read files, reach the network or change settings
        self.con.execute("SET enable_external_access = false")
        self.con.execute("SET lock_configuration = true")
        for nid in order:
            node = by_id[nid]
            if node["type"] in SOURCE_TYPES:
                continue
            self.check_cancel()
            self.on_node(nid, "running", None)
            ups = inputs_of(self.graph, nid)
            if node["type"] == "quality_check":
                res, q = self._quality_node(node, ups)
                if q is not None and q.num_rows:
                    quarantined.append((node, q))
            else:
                res = self._build(node, ups)
            results[nid] = res
            if self.real and node["type"] == "output":
                outputs.append((node, self.arrow(nid)))
            elif self.real and node["type"] in EFFECT_TYPES:
                effects.append((node, self.arrow(nid)))
            self.on_node(nid, "succeeded", res)
        return EngineResult(nodes=results, outputs=outputs, inputs=inputs, quarantined=quarantined, effects=effects,
                            state=self.state_updates,
                            new_data=any(self.new_data) if self.new_data else True, lineage=self.lineage)

    def preview(self, node_id: str, limit: int = 50) -> dict:
        rel = self.con.execute(f"SELECT * FROM {self._table(node_id)} LIMIT {int(limit)}")
        cols = [d[0] for d in rel.description]
        return {"columns": cols, "rows": [{c: cell(v) for c, v in zip(cols, r, strict=False)} for r in rel.fetchall()]}


__all__ = ["DuckDBEngine", "EngineResult", "NodeError", "NodeResult", "QualityGateFailed", "check_expression", "col",
           "ident", "kind", "node_of"]
