"""Automatic schema discovery.

Input: the text table produced by `parsers.py`. For every column we:
1. normalise the name to snake_case (the original name is kept in `source_name`);
2. infer the type from *all* values, with simple, explainable rules
   (boolean → integer → double → date → timestamp → string);
3. cast the values to that type (Apache Arrow), so what is shown in the preview is exactly what
   will be written to Parquet in the Bronze layer;
4. compute a small profile (nulls, distinct values, min / max, examples) and warnings.

A column is only given a non-text type when *every* non-empty value fits it. Otherwise it stays text
and a warning explains why — nothing is converted or dropped silently.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

import pandas as pd
import pyarrow as pa

from app.ingestion.parsers import RawTable

NULL_TOKENS = {"", "na", "n/a", "null", "none", "nan", "nat", "#n/a"}
RE_INT = r"[+-]?(?:0|[1-9]\d{0,17})"
RE_LEADING_ZERO = r"0\d+"
RE_FLOAT = r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?"
RE_FLOAT_COMMA = r"[+-]?\d+,\d+"
RE_BOOL = r"(?i:true|false)"
RE_DATE = r"\d{4}-\d{2}-\d{2}"
RE_DATE_SLASH = r"\d{1,2}/\d{1,2}/\d{4}"
RE_TIMESTAMP = r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?)?(?:Z|[+-]\d{2}:?\d{2})?"
RE_TZ = r".*(?:Z|[+-]\d{2}:?\d{2})"

ID_NAME = re.compile(r"^(id|.+_id)$")
METADATA_COLUMNS = [
    {"name": "_ingested_at", "type": "timestamp", "description": "When the row was written to Bronze (UTC)"},
    {"name": "_source_file", "type": "string", "description": "Name of the uploaded file"},
    {"name": "_import_id", "type": "string", "description": "Identifier of the import"},
]


@dataclass
class Discovery:
    table: pa.Table
    schema: list[dict]
    warnings: list[str]
    quarantined: list[dict]
    primary_key: list[str] = field(default_factory=list)
    sheets: list[str] = field(default_factory=list)
    options_used: dict = field(default_factory=dict)

    @property
    def row_count(self) -> int:
        return self.table.num_rows

    def sample_rows(self, n: int) -> list[dict]:
        return [{k: json_safe(v) for k, v in r.items()} for r in self.table.slice(0, n).to_pylist()]


def json_safe(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, float) and v != v:  # NaN
        return None
    return v


def snake_case(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)  # camelCase → camel_Case
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    if not s:
        return ""
    if s[0].isdigit():
        s = f"c_{s}"
    return s[:63]


def normalise_names(names: list[str]) -> list[str]:
    out: list[str] = []
    for i, n in enumerate(names):
        base = snake_case(n) or f"column_{i + 1}"
        cand, k = base, 2
        while cand in out:
            cand, k = f"{base}_{k}", k + 1
        out.append(cand)
    return out


def _all(s: pd.Series, pattern: str) -> bool:
    return bool(len(s)) and bool(s.str.fullmatch(pattern).all())


def _share(s: pd.Series, pattern: str) -> float:
    return float(s.str.fullmatch(pattern).mean()) if len(s) else 0.0


def _valid_dates(values: pd.Series, fmt: str) -> bool:
    return bool(pd.to_datetime(values, format=fmt, errors="coerce").notna().all())


def infer_type(values: pd.Series) -> tuple[str, dict, list[str]]:
    """values: non-null, stripped strings. Returns (type, cast hints, warnings)."""
    if values.empty:
        return "string", {}, ["is empty in every row — typed as text"]
    if _all(values, RE_BOOL):
        return "boolean", {}, []
    if _all(values, RE_INT):
        return "integer", {}, []
    if _all(values, RE_LEADING_ZERO + "|" + RE_INT):
        return "string", {}, ["looks like a number but has leading zeros (e.g. a code) — kept as text"]
    if _all(values, RE_FLOAT):
        return "double", {}, []
    if _all(values, RE_FLOAT_COMMA + "|" + RE_INT):
        return "double", {"decimal_comma": True}, ["uses a decimal comma (3,14) — converted to a number"]
    if _all(values, RE_DATE):
        if _valid_dates(values, "%Y-%m-%d"):
            return "date", {"format": "%Y-%m-%d"}, []
        return "string", {}, ["looks like dates but some are not valid calendar dates — kept as text"]
    if _all(values, RE_DATE_SLASH):
        parts = values.str.split("/", expand=True).astype(int)
        month_first = bool((parts[1] > 12).any()) and not bool((parts[0] > 12).any())
        fmt = "%m/%d/%Y" if month_first else "%d/%m/%Y"
        if _valid_dates(values, fmt):
            note = "dates read as MM/DD/YYYY" if month_first else "dates read as DD/MM/YYYY"
            return "date", {"format": fmt}, [note]
        return "string", {}, ["looks like dates but some are not valid calendar dates — kept as text"]
    if _all(values, RE_TIMESTAMP):
        has_tz = values.str.fullmatch(RE_TZ)
        utc = bool(has_tz.any())
        if pd.to_datetime(values, format="ISO8601", utc=utc, errors="coerce").isna().any():
            return "string", {}, ["looks like timestamps but some are not valid — kept as text"]
        warns = []
        if utc and not has_tz.all():
            warns.append("mixes timestamps with and without a time zone — values without one are read as UTC")
        return "timestamp", {"utc": utc}, warns

    warns = []
    numeric = _share(values, RE_FLOAT + "|" + RE_FLOAT_COMMA)
    if numeric >= 0.8:
        bad = values[~values.str.fullmatch(RE_FLOAT + "|" + RE_FLOAT_COMMA)].iloc[0]
        warns.append(f"is {numeric:.0%} numeric but contains non-numeric values such as '{bad[:40]}' — kept as text")
    dates = _share(values, RE_TIMESTAMP + "|" + RE_DATE_SLASH)
    if 0.8 <= dates < 1:
        bad = values[~values.str.fullmatch(RE_TIMESTAMP + "|" + RE_DATE_SLASH)].iloc[0]
        warns.append(f"is {dates:.0%} dates but contains other values such as '{bad[:40]}' — kept as text")
    return "string", {}, warns


def cast(series: pd.Series, typ: str, hints: dict) -> pa.Array:
    """series: stripped strings or None. Every non-null value is known to match `typ`."""
    if typ == "boolean":
        return pa.array([None if v is None else v.lower() == "true" for v in series], pa.bool_())
    if typ == "integer":
        return pa.array([None if v is None else int(v) for v in series], pa.int64())
    if typ == "double":
        comma = hints.get("decimal_comma")
        return pa.array([None if v is None else float(v.replace(",", ".") if comma else v) for v in series], pa.float64())
    if typ == "date":
        fmt = hints["format"]
        return pa.array([None if v is None else datetime.strptime(v, fmt).date() for v in series], pa.date32())
    if typ == "timestamp":
        utc = hints.get("utc", False)
        ts = pd.to_datetime(series, format="ISO8601", utc=utc)
        return pa.Array.from_pandas(ts).cast(pa.timestamp("us", tz="UTC" if utc else None), safe=False)
    return pa.array(list(series), pa.string())


def profile(arr: pa.Array, typ: str, raw_non_null: pd.Series) -> dict:
    n = len(arr)
    nulls = arr.null_count
    out = {
        "null_count": nulls,
        "null_pct": round(100 * nulls / n, 2) if n else 0.0,
        "distinct_count": int(raw_non_null.nunique()),
        "examples": [str(v)[:60] for v in raw_non_null.drop_duplicates().head(3)],
        "min": None,
        "max": None,
    }
    if typ in ("integer", "double", "date", "timestamp") and n - nulls:
        import pyarrow.compute as pc

        mm = pc.min_max(arr)
        out["min"] = json_safe(mm["min"].as_py())
        out["max"] = json_safe(mm["max"].as_py())
    return out


def discover(raw: RawTable) -> Discovery:
    """Infer the schema of a parsed file and build the typed Arrow table."""
    names = normalise_names(raw.columns)
    warnings = list(raw.notes)
    if raw.quarantined:
        warnings.append(f"{len(raw.quarantined)} malformed record(s) were set aside (quarantine) and will not be loaded.")
    if not raw.rows:
        raise ValueError("The file has a header but no data rows")

    frame = pd.DataFrame(raw.rows, columns=range(len(names)), dtype=object)
    arrays, schema, pk, renamed = [], [], [], []
    for i, (src, name) in enumerate(zip(raw.columns, names, strict=False)):
        col = frame[i].map(lambda v: None if v is None or v.strip().lower() in NULL_TOKENS else v.strip())
        non_null = col.dropna().astype(str)
        typ, hints, col_warnings = infer_type(non_null)
        arr = cast(col, typ, hints)
        prof = profile(arr, typ, non_null)
        label = f"Column '{src or name}'"
        warnings.extend(f"{label} {w}." for w in col_warnings)
        if src != name:
            renamed.append(f"'{src}' → {name}" if src else f"(no header) → {name}")
        if 20 <= prof["null_pct"] < 100:
            warnings.append(f"{label} is {prof['null_pct']:.0f}% empty.")
        if not pk and ID_NAME.match(name) and typ in ("integer", "string") and prof["null_count"] == 0 \
                and prof["distinct_count"] == len(arr):
            pk.append(name)
        arrays.append(arr)
        schema.append({"name": name, "source_name": src, "type": typ, "nullable": prof["null_count"] > 0, **prof})
    if renamed:
        warnings.append(f"{len(renamed)} column name(s) normalised to snake_case for SQL: {', '.join(renamed[:8])}"
                        + (" …" if len(renamed) > 8 else "") + ".")
    table = pa.Table.from_arrays(arrays, names=names)
    return Discovery(table=table, schema=schema, warnings=warnings, quarantined=raw.quarantined,
                     primary_key=pk, sheets=raw.sheets, options_used=raw.options_used)


def column_types(schema: list[dict]) -> dict[str, str]:
    return {c["name"]: c["type"] for c in schema}


def diff_schema(old: list[dict], new: list[dict]) -> dict:
    o, n = column_types(old), column_types(new)
    return {
        "added": [k for k in n if k not in o],
        "removed": [k for k in o if k not in n],
        "changed": [{"column": k, "from": o[k], "to": n[k]} for k in n if k in o and o[k] != n[k]],
    }
