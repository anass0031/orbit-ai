"""Pipeline graph specification — the same JSON drives the React Flow editor and the executor.

{
  "nodes": [{"id": "n1", "type": "dataset_input", "label": "...", "position": {"x": 0, "y": 0}, "config": {...}}],
  "edges": [{"id": "e1", "source": "n1", "target": "n2", "source_handle": null}]
}

`source_handle` is only used by the Branch node: "true" (rows matching the condition) or "false" (the others).
"""
from __future__ import annotations

from dataclasses import dataclass

# type -> (category, min inputs, max inputs (None = unbounded), description)
NODE_TYPES: dict[str, tuple[str, int, int | None, str]] = {
    # ── sources ──
    "dataset_input": ("SOURCES", 0, 0, "Read a lakehouse dataset (current or pinned version)"),
    "incremental_input": ("SOURCES", 0, 0, "Read only the rows of a dataset added since the last successful run (watermark)"),
    "file_drop_input": ("SOURCES", 0, 0, "Read every new file dropped in a folder (CSV, Excel, JSON, Parquet)"),
    "date_dimension": ("SOURCES", 0, 0, "Generate a calendar table (day, week, month, quarter, holidays)"),
    "subpipeline": ("SOURCES", 0, 0, "Run another pipeline's published graph and use one of its steps as input"),
    # ── transformations ──
    "filter": ("TRANSFORM", 1, 1, "Keep rows matching a SQL boolean condition"),
    "select": ("TRANSFORM", 1, 1, "Choose, rename and cast columns"),
    "sql": ("TRANSFORM", 1, 4, "Free SQL SELECT over upstream inputs (input / input_1 … input_n)"),
    "join": ("TRANSFORM", 2, 2, "Join two inputs on key columns"),
    "union": ("TRANSFORM", 2, None, "Union inputs by column name"),
    "aggregate": ("TRANSFORM", 1, 1, "Group by columns and compute aggregates"),
    "dedupe": ("TRANSFORM", 1, 1, "Keep one row per key (latest by an ordering column)"),
    "clean": ("TRANSFORM", 1, 1, "Trim spaces, change case, blank placeholder values, normalise numbers and dates"),
    "fill_missing": ("TRANSFORM", 1, 1, "Fill empty cells: fixed value, mean, median, previous or next value"),
    "derived_column": ("TRANSFORM", 1, 1, "Add or replace columns computed from a SQL expression"),
    "split_column": ("TRANSFORM", 1, 1, "Split one text column into several on a delimiter"),
    "merge_columns": ("TRANSFORM", 1, 1, "Concatenate several columns into one"),
    "lookup": ("TRANSFORM", 1, 2, "Translate codes into labels with a mapping or a reference input"),
    "sort_limit": ("TRANSFORM", 1, 1, "Sort rows and keep the first N (optionally per group)"),
    "pivot": ("TRANSFORM", 1, 1, "Turn the values of a column into columns"),
    "unpivot": ("TRANSFORM", 1, 1, "Turn columns into (name, value) rows"),
    "window": ("TRANSFORM", 1, 1, "Running totals, ranks, moving averages, previous / next values"),
    "scd2": ("TRANSFORM", 1, 1, "Keep the history of changes (valid_from / valid_to / is_current)"),
    "surrogate_key": ("TRANSFORM", 1, 1, "Add a stable numeric key computed from business key columns (star schema)"),
    "python": ("TRANSFORM", 1, 1, "Custom pandas code in a restricted sandbox"),
    # ── quality ──
    "quality_check": ("QUALITY", 1, 1, "Inline data quality assertions with fail / quarantine / warn"),
    "schema_check": ("QUALITY", 1, 1, "Stop or warn when columns disappear or change type"),
    "data_compare": ("QUALITY", 1, 1, "Compare the row count with the previous successful run"),
    "anomaly_detection": ("QUALITY", 1, 1, "Flag unusual values (z-score or IQR)"),
    "branch": ("QUALITY", 1, 1, "Route rows: matching the condition → 'true' port, the others → 'false' port"),
    # ── destinations ──
    "output": ("DESTINATIONS", 1, 1, "Write a Silver or Gold dataset version (overwrite, append or upsert)"),
    "export_file": ("DESTINATIONS", 1, 1, "Write a CSV / Excel / JSON / Parquet file with a download link"),
    "rest_output": ("DESTINATIONS", 1, 1, "Send the rows to an external REST API / webhook"),
    "email_report": ("DESTINATIONS", 1, 1, "Email a table or a CSV attachment after each run"),
}

SOURCE_TYPES = {t for t, v in NODE_TYPES.items() if v[0] == "SOURCES"}
# nodes whose result leaves the pipeline: they need no downstream node
SINK_TYPES = {"output", "export_file", "rest_output", "email_report"}
# side effects executed after the dataset outputs are written (in topological order)
EFFECT_TYPES = SINK_TYPES - {"output"}
BRANCH_HANDLES = ("true", "false")

AGG_FUNCS = {"count", "sum", "avg", "min", "max", "count_distinct"}
JOIN_TYPES = {"inner", "left", "right", "full"}


@dataclass
class Issue:
    node_id: str | None
    message: str
    field: str | None = None
    severity: str = "error"  # error | warning

    def as_dict(self) -> dict:
        return {"node_id": self.node_id, "field": self.field, "message": self.message, "severity": self.severity}


def nodes_by_id(graph: dict) -> dict[str, dict]:
    return {n["id"]: n for n in graph.get("nodes", [])}


def edge_key(e: dict) -> str:
    """Upstream key of an edge: the source node, or `node:true` / `node:false` for a Branch port."""
    h = e.get("source_handle")
    return f"{e['source']}:{h}" if h in BRANCH_HANDLES else e["source"]


def inputs_of(graph: dict, node_id: str) -> list[str]:
    """Upstream keys in edge order (the order the user connected them)."""
    return [edge_key(e) for e in graph.get("edges", []) if e["target"] == node_id]


def node_of(key: str) -> str:
    return key.split(":", 1)[0]


def topo_order(graph: dict) -> list[str]:
    """Kahn's algorithm. Raises ValueError on cycles."""
    ids = [n["id"] for n in graph.get("nodes", [])]
    indeg = {i: 0 for i in ids}
    for e in graph.get("edges", []):
        if e["target"] in indeg and e["source"] in indeg:
            indeg[e["target"]] += 1
    ready = [i for i in ids if indeg[i] == 0]
    order = []
    while ready:
        n = ready.pop(0)
        order.append(n)
        for e in graph.get("edges", []):
            if e["source"] == n and e["target"] in indeg:
                indeg[e["target"]] -= 1
                if indeg[e["target"]] == 0:
                    ready.append(e["target"])
    if len(order) != len(ids):
        raise ValueError("The graph contains a cycle")
    return order


def structural_issues(graph: dict) -> list[Issue]:
    issues: list[Issue] = []
    nodes = graph.get("nodes", [])
    if not nodes:
        return [Issue(None, "The pipeline is empty — add a source and a destination")]
    by_id = nodes_by_id(graph)
    if len(by_id) != len(nodes):
        issues.append(Issue(None, "Duplicate node ids"))
    for e in graph.get("edges", []):
        if e["source"] not in by_id or e["target"] not in by_id:
            issues.append(Issue(None, f"Edge {e.get('id')} references a missing node"))
            continue
        if e["source"] == e["target"]:
            issues.append(Issue(e["source"], "A node cannot connect to itself"))
        if by_id[e["source"]].get("type") == "branch" and e.get("source_handle") not in BRANCH_HANDLES:
            issues.append(Issue(e["source"], "Connect the Branch node from its 'true' or 'false' port"))
    try:
        topo_order(graph)
    except ValueError as exc:
        issues.append(Issue(None, str(exc)))
    if not any(n.get("type") in SOURCE_TYPES for n in nodes):
        issues.append(Issue(None, "Add at least one source node (dataset, file drop, calendar…)"))
    if not any(n.get("type") in SINK_TYPES for n in nodes):
        issues.append(Issue(None, "Add at least one destination (dataset output, file export, API, email…)"))
    outputs_seen = {}
    for n in nodes:
        t = n.get("type")
        if t not in NODE_TYPES:
            issues.append(Issue(n["id"], f"Unknown node type '{t}'"))
            continue
        _, mn, mx, _ = NODE_TYPES[t]
        k = len(inputs_of(graph, n["id"]))
        if k < mn:
            issues.append(Issue(n["id"], f"Missing input: needs {mn} upstream node(s), has {k}"))
        if mx is not None and k > mx:
            issues.append(Issue(n["id"], f"Too many inputs: accepts at most {mx}, has {k}"))
        downstream = [e for e in graph.get("edges", []) if e["source"] == n["id"]]
        if t not in SINK_TYPES and not downstream:
            issues.append(Issue(n["id"], "Node output is not used by any downstream node", severity="warning"))
        if t == "output":
            c = n.get("config", {})
            key = (c.get("layer"), c.get("name"))
            if key in outputs_seen:
                issues.append(Issue(n["id"], f"Two outputs write {key[0]}.{key[1]}"))
            outputs_seen[key] = n["id"]
            if downstream:
                issues.append(Issue(n["id"], "A Dataset output must be the last step of its branch"))
    return issues
