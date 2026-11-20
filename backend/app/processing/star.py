"""Star schema support for Gold outputs: Unknown member, foreign keys and integrity checks.

Everything here happens before any dataset is written: a failed check blocks the whole run.
- every dimension contains a row whose key is -1 ("Unknown");
- a fact's foreign key without a match in its dimension takes the value -1, so no fact row is lost;
- primary keys must be unique and not null, and every foreign key must exist in its dimension.
"""
from __future__ import annotations

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
from sqlalchemy.orm import Session

from app.lakehouse import catalog
from app.models import Dataset
from app.processing.engine import QualityGateFailed

UNKNOWN_KEY = -1
UNKNOWN_LABEL = "Unknown"


def with_unknown(table: pa.Table, key: str) -> pa.Table:
    """Add the Unknown member of a dimension when it is not there yet."""
    if UNKNOWN_KEY in table.column(key).to_pylist():
        return table
    row = {}
    for f in table.schema:
        value = UNKNOWN_KEY if f.name == key else UNKNOWN_LABEL if pa.types.is_string(f.type) or pa.types.is_large_string(f.type) else None
        row[f.name] = pa.array([value], f.type)
    return pa.concat_tables([table, pa.table(row, schema=table.schema)])


def key_problems(table: pa.Table, key: str | list[str], name: str) -> list[str]:
    """A primary key may span several columns: the grain of a fact is checked on their combination."""
    keys = [key] if isinstance(key, str) else key
    cols = ", ".join(f'"{k}"' for k in keys)
    empty = " OR ".join(f'"{k}" IS NULL' for k in keys)
    con = duckdb.connect()
    try:
        con.register("t", table)
        nulls = con.execute(f"SELECT count(*) FROM t WHERE {empty}").fetchone()[0]
        duplicates = con.execute(f"SELECT coalesce(sum(n - 1), 0) FROM (SELECT count(*) AS n FROM t WHERE NOT ({empty}) GROUP BY {cols})").fetchone()[0]
    finally:
        con.close()
    out = []
    label = " + ".join(keys)
    if nulls:
        out.append(f"{name}: {nulls} row(s) have an empty primary key {label}")
    if duplicates:
        out.append(f"{name}: {duplicates} duplicate value(s) of the primary key {label}")
    return out


def map_foreign_key(table: pa.Table, column: str, keys: pa.Array) -> tuple[pa.Table, int]:
    """Foreign keys without a match become -1. Returns the new table and how many values were replaced."""
    arr = table.column(column).combine_chunks()
    found = pc.fill_null(pc.is_in(arr, value_set=keys.cast(arr.type)), False)
    missing = len(arr) - pc.sum(found).as_py() if len(arr) else 0
    fixed = pc.if_else(found, arr, pa.scalar(UNKNOWN_KEY, arr.type))
    return table.set_column(table.schema.get_field_index(column), table.schema.field(column), fixed), missing


def _dimension(db: Session, name: str, staged: dict[str, tuple[pa.Table, str]]) -> tuple[pa.Table, str] | None:
    if name in staged:
        return staged[name]
    ds = db.query(Dataset).filter(Dataset.layer == "gold", Dataset.name == name, Dataset.model_role == "dimension").first()
    if ds is None or not ds.current_version or not ds.primary_key:
        return None
    return catalog.read_table(catalog.get_version(db, ds, None)), ds.primary_key[0]


def prepare(db: Session, staged: list[tuple], log) -> list[tuple]:
    """Apply the model to the outputs of a run. Returns them in write order: dimensions, then facts, then the rest."""
    def data_of(item):
        return item[2].table if item[2] else item[3]

    def replaced(item, table):
        if item[2]:
            item[2].table = table
        return (item[0], item[1], item[2], table)

    def role(item):
        return item[0]["config"].get("model_role")

    problems: list[str] = []
    dims: dict[str, tuple[pa.Table, str]] = {}
    ordered = []
    for item in [i for i in staged if role(i) == "dimension"]:
        c = item[0]["config"]
        key = c["primary_key"][0]
        table = with_unknown(data_of(item), key)
        problems += key_problems(table, key, f"gold.{c['name']}")
        dims[c["name"]] = (table, key)
        ordered.append(item)
    for n, item in enumerate(ordered):  # snowflake: a dimension may reference a parent dimension
        c = item[0]["config"]
        table = dims[c["name"]][0]
        for rel in c.get("relationships") or []:
            dim = _dimension(db, rel["dimension"], dims)
            if dim is None:
                problems.append(f"gold.{c['name']}: the dimension gold.{rel['dimension']} does not exist")
                continue
            table, missing = map_foreign_key(table, rel["column"], dim[0].column(dim[1]).combine_chunks())
            if missing > 1:  # the Unknown member of the child always points to the Unknown member of its parent
                log(f"gold.{c['name']}: {missing - 1:,} value(s) of {rel['column']} have no match in gold.{rel['dimension']} → {UNKNOWN_KEY} (Unknown)", level="warning")
        dims[c["name"]] = (table, dims[c["name"]][1])
        ordered[n] = replaced(item, table)
    for item in [i for i in staged if role(i) == "fact"]:
        c = item[0]["config"]
        table = data_of(item)
        if c.get("primary_key"):
            problems += key_problems(table, c["primary_key"], f"gold.{c['name']}")
        for rel in c.get("relationships") or []:
            dim = _dimension(db, rel["dimension"], dims)
            if dim is None:
                problems.append(f"gold.{c['name']}: the dimension gold.{rel['dimension']} does not exist")
                continue
            keys = dim[0].column(dim[1]).combine_chunks()
            if UNKNOWN_KEY not in keys.to_pylist():
                problems.append(f"gold.{rel['dimension']} has no Unknown member (key {UNKNOWN_KEY})")
                continue
            table, missing = map_foreign_key(table, rel["column"], keys)
            if missing:
                log(f"gold.{c['name']}: {missing:,} value(s) of {rel['column']} have no match in gold.{rel['dimension']} → {UNKNOWN_KEY} (Unknown)", level="warning")
        ordered.append(replaced(item, table))
    if problems:
        raise QualityGateFailed("Data model check failed, nothing was written — " + "; ".join(problems))
    return ordered + [i for i in staged if role(i) not in ("dimension", "fact")]


def remember(ds: Dataset, config: dict) -> None:
    """Keep the model of a Gold output on its dataset (data model view, SQL joins)."""
    role = config.get("model_role") or None
    ds.model_role = role
    ds.primary_key = (config.get("primary_key") or None) if role else None
    ds.relationships = (config.get("relationships") or None) if role else None
