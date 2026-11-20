"""Hierarchical documents (JSON, XML, YAML) turned into relational entities.

Nested objects become flat columns (customer.city → customer_city). Arrays of objects become child entities that keep a
reference to their parent (orders[].items[] → entity orders_items with an order_id column), so no relationship is lost.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter

MAX_NODES = 2_000_000  # protects the workers against documents that expand enormously (YAML aliases)


class DocumentError(ValueError):
    """The document cannot be read. The message is shown to the user as-is."""


def snake(name: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(name))
    return re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower() or "records"


def singular(name: str) -> str:
    if name.endswith("ies") and len(name) > 4:
        return name[:-3] + "y"
    if name.endswith("s") and not name.endswith(("ss", "us")) and len(name) > 3:
        return name[:-1]
    return name


# ── readers ──────────────────────────────────────────────────────────────
def _tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_value(el: ET.Element):
    children = list(el)
    text = (el.text or "").strip()
    if not children and not el.attrib:
        return text or None
    out: dict = {_tag(k): v for k, v in el.attrib.items()}
    counts = Counter(_tag(c.tag) for c in children)
    for c in children:
        name, value = _tag(c.tag), _xml_value(c)
        if counts[name] > 1:
            out.setdefault(name, []).append(value)
        else:
            out[name] = value
    if text and not children:
        out["value"] = text
    return out


def read_xml(text: str) -> dict:
    if re.search(r"<!(DOCTYPE|ENTITY)", text, re.I):
        raise DocumentError("XML files that declare a DOCTYPE or entities are refused for security reasons")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise DocumentError(f"Invalid XML: {exc}") from exc
    return {_tag(root.tag): _xml_value(root)}


def read_yaml(text: str):
    import yaml

    try:
        docs = [d for d in yaml.safe_load_all(text) if d is not None]
    except yaml.YAMLError as exc:
        raise DocumentError(f"Invalid YAML: {str(exc).splitlines()[0]}") from exc
    if not docs:
        raise DocumentError("The YAML file contains no data")
    return docs[0] if len(docs) == 1 else docs


# ── entities ─────────────────────────────────────────────────────────────
def _records(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(x, dict) for x in value)


def collections(doc, default: str = "records") -> dict[str, list[dict]]:
    """The top-level collections of a document."""
    if isinstance(doc, list):
        rows = [x for x in doc if isinstance(x, dict)]
        return {default: rows} if rows else {}
    if not isinstance(doc, dict):
        return {}
    found: dict[str, list[dict]] = {}
    for k, v in doc.items():
        if _records(v):
            found[k] = v
        elif isinstance(v, dict) and len(v) == 1 and _records(next(iter(v.values()))):  # <orders><order/><order/></orders>
            found[k] = next(iter(v.values()))
    if found:
        return found
    if len(doc) == 1 and isinstance(next(iter(doc.values())), dict):  # a wrapper such as the XML root element
        name, inner = next(iter(doc.items()))
        return collections(inner, snake(name))
    return {default: [doc]}


def _id_column(name: str, records: list[dict]) -> str | None:
    """The field that identifies a record: id, <entity>_id, or the first scalar field ending with _id."""
    names = {snake(k): k for r in records[:200] for k, v in r.items() if not isinstance(v, (dict, list))}
    for candidate in ("id", f"{singular(name)}_id", f"{name}_id"):
        if candidate in names:
            return candidate
    return next((n for n in names if n.endswith("_id")), None)


def _flatten(value: dict, prefix: str, row: dict, nested: dict, budget: list[int]) -> None:
    for key, v in value.items():
        budget[0] -= 1
        if budget[0] < 0:
            raise DocumentError("The document is too large or too deeply repeated to be read")
        name = f"{prefix}{snake(key)}"
        if isinstance(v, dict):
            _flatten(v, f"{name}_", row, nested, budget)
        elif isinstance(v, list):
            nested.setdefault(name, []).extend(v)
        else:
            row[name] = v


def _walk(name: str, records: list[dict], parent: tuple[str, list] | None, out: dict[str, list[dict]], budget: list[int]) -> None:
    id_col = _id_column(name, records)
    rows = out.setdefault(name, [])
    ref = (id_col if id_col and id_col != "id" else f"{singular(name)}_id") if id_col else f"{singular(name)}_row"
    children: dict[str, tuple[list[dict], list]] = {}
    repeated = {k for r in records for k, v in r.items() if isinstance(v, list)}  # XML: one <item> in a record, several in another
    for i, rec in enumerate(records):
        rec = {k: [v] if k in repeated and isinstance(v, dict) else v for k, v in rec.items()}
        row: dict = {}
        nested: dict[str, list] = {}
        if parent:
            row[parent[0]] = parent[1][i]
        _flatten(rec, "", row, nested, budget)
        if id_col:
            own = row.get(id_col)
        else:
            own = len(rows) + 1
            row[ref] = own
        rows.append(row)
        for key, items in nested.items():
            kids, owners = children.setdefault(key, ([], []))
            for item in items:
                kids.append(item if isinstance(item, dict) else {"value": item})
                owners.append(own)
    for key, (kids, owners) in children.items():
        if kids:
            _walk(f"{name}_{key}", kids, (ref, owners), out, budget)


def extract(doc, default: str = "records") -> dict[str, list[dict]]:
    """Every entity of a document as flat rows: {entity name: [row, …]}."""
    out: dict[str, list[dict]] = {}
    budget = [MAX_NODES]
    for name, records in collections(doc, snake(default)).items():
        _walk(snake(name), records, None, out, budget)
    return out
