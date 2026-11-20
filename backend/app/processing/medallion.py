"""Medallion rules enforced by the platform at validation, at publication and just before a run starts.

R1  Gold is built from Silver: no path from Bronze or a raw source may reach a Gold output.
R2  Silver is cleaned: every path from raw data to a Silver output goes through Deduplicate AND Data quality.
R3  No way back: a Silver output never receives data coming from Gold.

Paths are followed through every edge (both ports of a Branch node) and into sub-pipelines.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Dataset, Pipeline, PipelineVersion
from app.processing.spec import Issue, nodes_by_id, topo_order

RAW_SOURCES = {"file_drop_input"}
MAX_DEPTH = 3
MODEL_ROLES = ("fact", "dimension", "mart")


class MedallionViolation(Exception):
    code = "medallion_rule_violation"
    retryable = False


@dataclass(frozen=True)
class Path:
    origin: str  # bronze | raw | silver | gold
    label: str
    deduped: bool = False
    checked: bool = False

    @property
    def is_raw(self) -> bool:
        return self.origin in ("bronze", "raw")


def _subpipeline_paths(db: Session, node: dict, chain: tuple[str, ...]) -> set[Path]:
    c = node.get("config") or {}
    p = db.get(Pipeline, c.get("pipeline_id") or "")
    if p is None or p.id in chain or len(chain) >= MAX_DEPTH:
        return set()  # the engine reports these configuration errors on the node
    q = select(PipelineVersion).where(PipelineVersion.pipeline_id == p.id)
    q = q.where(PipelineVersion.version == int(c["version"])) if c.get("version") else q.order_by(PipelineVersion.version.desc())
    version = db.scalar(q.limit(1))
    if version is None:
        return set()
    return paths_by_node(db, version.graph, (*chain, p.id)).get(c.get("node_id"), set())


def _source_paths(db: Session, node: dict, chain: tuple[str, ...]) -> set[Path]:
    t, c = node["type"], node.get("config") or {}
    if t in ("dataset_input", "incremental_input"):
        ds = db.get(Dataset, c.get("dataset_id") or "")
        return {Path(ds.layer, ds.qualified_name)} if ds else set()
    if t in RAW_SOURCES:
        return {Path("raw", node.get("label") or t)}
    if t == "subpipeline":
        return _subpipeline_paths(db, node, chain)
    return set()  # generated data (calendar): no origin to control


def paths_by_node(db: Session, graph: dict, chain: tuple[str, ...] = ()) -> dict[str, set[Path]]:
    """Every distinct way data can arrive at each node, with the cleaning steps met on the way."""
    by_id = nodes_by_id(graph)
    out: dict[str, set[Path]] = {}
    for nid in topo_order(graph):
        node = by_id[nid]
        paths = set(_source_paths(db, node, chain))
        for e in graph.get("edges", []):
            if e["target"] == nid:
                paths |= out.get(e["source"], set())
        if node["type"] == "dedupe":
            paths = {replace(p, deduped=True) for p in paths}
        elif node["type"] == "quality_check":
            paths = {replace(p, checked=True) for p in paths}
        out[nid] = paths
    return out


def _rule_issues(nid: str, layer: str, paths: set[Path]) -> list[Issue]:
    issues = []
    for label in sorted({p.label for p in paths if p.is_raw and layer == "gold"}):
        issues.append(Issue(nid, f"Gold must be built from Silver: this output receives raw data from {label}. "
                                 "Write it to Silver first, then read the Silver dataset."))
    if layer == "silver":
        for label in sorted({p.label for p in paths if p.origin == "gold"}):
            issues.append(Issue(nid, f"Silver cannot be built from Gold: this output receives data from {label}."))
        missing: dict[str, set[str]] = {}
        for p in paths:
            if p.is_raw and not (p.deduped and p.checked):
                steps = missing.setdefault(p.label, set())
                if not p.deduped:
                    steps.add("Deduplicate")
                if not p.checked:
                    steps.add("Data quality")
        for label, steps in sorted(missing.items()):
            issues.append(Issue(nid, f"Silver must be cleaned: the path from {label} has no {' and no '.join(sorted(steps))} step. "
                                     "Add a Deduplicate and a Data quality step before this output."))
    return issues


def _model_issues(db: Session, graph: dict, node: dict) -> list[Issue]:
    nid, c = node["id"], node.get("config") or {}
    role = c.get("model_role")
    if not role:
        return []
    if role not in MODEL_ROLES:
        return [Issue(nid, f"Model role must be one of {', '.join(MODEL_ROLES)}", "model_role")]
    if c.get("layer") != "gold":
        return [Issue(nid, "Only Gold outputs can be part of the data model", "model_role")]
    issues = []
    if role in ("fact", "dimension") and (c.get("mode") or "overwrite") != "overwrite":
        issues.append(Issue(nid, "Fact and dimension outputs must use the overwrite mode", "mode"))
    if role == "dimension" and len(c.get("primary_key") or []) != 1:
        issues.append(Issue(nid, "A dimension needs exactly one primary key column", "primary_key"))
    declared = {n["config"].get("name") for n in graph["nodes"] if n["type"] == "output" and n["config"].get("layer") == "gold"
                and n["config"].get("model_role") == "dimension"}
    if role == "dimension" and _dimension_cycle(graph, c.get("name")):
        issues.append(Issue(nid, "Dimensions cannot reference each other in a loop", "relationships"))
    for rel in c.get("relationships") or []:
        name = rel.get("dimension") or ""
        known = name in declared or db.scalar(select(Dataset).where(Dataset.layer == "gold", Dataset.name == name, Dataset.model_role == "dimension"))
        if not rel.get("column") or not known:
            issues.append(Issue(nid, f"Relationship on '{rel.get('column')}': gold.{name} is not a dimension of the model", "relationships"))
    return issues


def _dimension_cycle(graph: dict, start: str | None) -> bool:
    parents = {n["config"].get("name"): [r.get("dimension") for r in n["config"].get("relationships") or []]
               for n in graph["nodes"] if n["type"] == "output" and n["config"].get("model_role") == "dimension"}
    seen, todo = set(), list(parents.get(start, []))
    while todo:
        cur = todo.pop()
        if cur == start:
            return True
        if cur not in seen:
            seen.add(cur)
            todo += parents.get(cur, [])
    return False


def check(db: Session, graph: dict) -> list[Issue]:
    """Rule violations of a structurally valid graph."""
    paths = paths_by_node(db, graph)
    issues: list[Issue] = []
    for node in graph.get("nodes", []):
        if node["type"] == "output":
            issues += _rule_issues(node["id"], (node.get("config") or {}).get("layer"), paths.get(node["id"], set()))
            issues += _model_issues(db, graph, node)
    return issues


def errors(db: Session, graph: dict) -> list[Issue]:
    return [i for i in check(db, graph) if i.severity == "error"]
