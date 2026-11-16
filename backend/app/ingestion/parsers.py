"""Read an uploaded file (CSV, Excel, JSON) into a table of *text* cells.

Every value is kept as a string (or None) at this stage; `schema.py` then decides the type of each
column. Reading everything as text first means nothing is silently changed by the parser
(e.g. the code "007" is not turned into the number 7).

Malformed records are not dropped silently: they are returned in `quarantined` with a reason.
"""
from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import PurePath

import pandas as pd

EXTENSIONS = {
    ".csv": "csv", ".tsv": "csv", ".txt": "csv",
    ".xlsx": "excel", ".xlsm": "excel",
    ".json": "json", ".jsonl": "json", ".ndjson": "json",
}
SUPPORTED = ", ".join(sorted(EXTENSIONS))
DELIMITERS = [",", ";", "\t", "|"]


class ParseError(Exception):
    """The file cannot be read with the given format/options. Shown to the user as-is."""


@dataclass
class ParseOptions:
    delimiter: str | None = None  # CSV: None = detect automatically
    has_header: bool = True  # CSV
    encoding: str | None = None  # CSV/JSON: None = UTF-8, falling back to Windows-1252
    sheet_name: str | None = None  # Excel: None = first sheet

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class RawTable:
    columns: list[str]  # names exactly as found in the file
    rows: list[list[str | None]]
    quarantined: list[dict] = field(default_factory=list)  # [{"record": …, "reason": …}]
    notes: list[str] = field(default_factory=list)
    sheets: list[str] = field(default_factory=list)
    options_used: dict = field(default_factory=dict)


def detect_format(filename: str) -> str | None:
    return EXTENSIONS.get(PurePath(filename).suffix.lower())


def parse(data: bytes, fmt: str, filename: str, options: ParseOptions | None = None) -> RawTable:
    opts = options or ParseOptions()
    if not data or not data.strip():
        raise ParseError("The file is empty")
    if fmt == "csv":
        return _csv(data, filename, opts)
    if fmt == "excel":
        return _excel(data, opts)
    if fmt == "json":
        return _json(data, opts)
    raise ParseError(f"Unsupported format '{fmt}'. Supported extensions: {SUPPORTED}")


# ── helpers ──────────────────────────────────────────────────────────────────
def _decode(data: bytes, encoding: str | None, notes: list[str]) -> tuple[str, str]:
    if b"\x00" in data[:4096]:
        raise ParseError("The file looks binary, not text. Check the file type.")
    if encoding:
        try:
            return data.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError) as exc:
            raise ParseError(f"Cannot decode the file as {encoding}: {exc}") from exc
    try:
        return data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        notes.append("The file is not valid UTF-8; it was read as Windows-1252 (Latin). Check accented characters.")
        return data.decode("cp1252", errors="replace"), "cp1252"


def _cell(v) -> str | None:
    """Python value → text cell (None for empty values)."""
    if v is None or v is pd.NaT or v is pd.NA:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return str(int(v))
    if isinstance(v, datetime):
        return v.isoformat(sep=" ") if (v.hour or v.minute or v.second or v.microsecond) else v.date().isoformat()
    if isinstance(v, (date, time)):
        return v.isoformat()
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False, default=str)
    if isinstance(v, pd.Timestamp):
        return _cell(v.to_pydatetime())
    s = str(v)
    return s


# ── CSV ──────────────────────────────────────────────────────────────────────
def _sniff_delimiter(text: str, filename: str) -> str:
    if filename.lower().endswith(".tsv"):
        return "\t"
    sample = "\n".join(text.splitlines()[:50])
    try:
        return csv.Sniffer().sniff(sample, delimiters="".join(DELIMITERS)).delimiter
    except csv.Error:
        first = sample.splitlines()[0] if sample else ""
        counts = {d: first.count(d) for d in DELIMITERS}
        best = max(counts, key=counts.get)
        return best if counts[best] else ","


def _csv(data: bytes, filename: str, opts: ParseOptions) -> RawTable:
    notes: list[str] = []
    text, encoding = _decode(data, opts.encoding, notes)
    if not text.strip():
        raise ParseError("The file is empty")
    delimiter = opts.delimiter or _sniff_delimiter(text, filename)
    if delimiter == "\\t":
        delimiter = "\t"
    if len(delimiter) != 1:
        raise ParseError("The CSV delimiter must be a single character")

    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, skipinitialspace=True)
    lines = [row for row in reader if any(c.strip() for c in row)]  # skip blank lines
    if not lines:
        raise ParseError("The file has no data rows")
    if opts.has_header:
        header, body = [c.strip() for c in lines[0]], lines[1:]
    else:
        header, body = [f"column_{i + 1}" for i in range(len(lines[0]))], lines
    if len(header) == 1 and len(text.splitlines()[0]) > 100:
        notes.append("Only one column was detected — check that the delimiter is correct.")

    rows: list[list[str | None]] = []
    bad: list[dict] = []
    width = len(header)
    for i, row in enumerate(body, start=2 if opts.has_header else 1):
        if len(row) > width:
            bad.append({"record": {"line": i, "raw": delimiter.join(row)[:500]},
                        "reason": f"line {i}: {len(row)} fields, header has {width}"})
            continue
        if len(row) < width:  # short rows are padded with empty values, like most tools do
            row = row + [""] * (width - len(row))
        rows.append([c if c.strip() != "" else None for c in row])
    return RawTable(columns=header, rows=rows, quarantined=bad, notes=notes,
                    options_used={"delimiter": delimiter, "has_header": opts.has_header, "encoding": encoding})


# ── Excel ────────────────────────────────────────────────────────────────────
def _excel(data: bytes, opts: ParseOptions) -> RawTable:
    try:
        book = pd.ExcelFile(io.BytesIO(data), engine="openpyxl")
    except Exception as exc:  # noqa: BLE001 - openpyxl raises many different types for bad files
        raise ParseError(f"Cannot open the Excel workbook: {exc}") from exc
    sheets = [str(s) for s in book.sheet_names]
    if not sheets:
        raise ParseError("The workbook has no sheets")
    sheet = opts.sheet_name or sheets[0]
    if sheet not in sheets:
        raise ParseError(f"Sheet '{sheet}' not found. Available sheets: {', '.join(sheets)}")
    df = book.parse(sheet_name=sheet, dtype=object, header=0 if opts.has_header else None)
    df = df.dropna(how="all")  # blank lines inside the sheet
    df = df.loc[:, ~(df.columns.astype(str).str.startswith("Unnamed:") & df.isna().all())]  # empty side columns
    if df.empty:
        raise ParseError(f"Sheet '{sheet}' contains no data rows")
    columns = [str(c).strip() if not str(c).startswith("Unnamed:") else "" for c in df.columns]
    if not opts.has_header:
        columns = [f"column_{i + 1}" for i in range(len(columns))]
    rows = [[_cell(v) for v in rec] for rec in df.itertuples(index=False, name=None)]
    notes = [f"Workbook has {len(sheets)} sheets; sheet '{sheet}' was imported."] if len(sheets) > 1 else []
    return RawTable(columns=columns, rows=rows, notes=notes, sheets=sheets,
                    options_used={"sheet_name": sheet, "has_header": opts.has_header})


# ── JSON / JSON Lines ────────────────────────────────────────────────────────
def _json(data: bytes, opts: ParseOptions) -> RawTable:
    notes: list[str] = []
    text, encoding = _decode(data, opts.encoding, notes)
    text = text.strip()
    records: list[dict] = []
    bad: list[dict] = []
    if text.startswith("[") or text.startswith("{"):
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as exc:
            if text.startswith("{") and "\n" in text:
                doc = None  # probably JSON Lines (one object per line)
            else:
                raise ParseError(f"Invalid JSON: {exc}") from exc
        if isinstance(doc, dict):
            lists = [k for k, v in doc.items() if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)]
            if len(lists) == 1:
                notes.append(f"Records were read from the '{lists[0]}' array of the JSON document.")
                doc = doc[lists[0]]
            else:
                doc = [doc]
        if isinstance(doc, list):
            for i, rec in enumerate(doc):
                if isinstance(rec, dict):
                    records.append(rec)
                else:
                    bad.append({"record": {"index": i, "raw": json.dumps(rec, default=str)[:500]},
                                "reason": f"element {i} is not a JSON object"})
    if not records and not bad:  # JSON Lines
        for i, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                bad.append({"record": {"line": i, "raw": line[:500]}, "reason": f"line {i}: {exc.msg}"})
                continue
            if isinstance(rec, dict):
                records.append(rec)
            else:
                bad.append({"record": {"line": i, "raw": line[:500]}, "reason": f"line {i}: not a JSON object"})
    if not records:
        raise ParseError("No JSON objects found in the file")

    # Nested objects become flat columns (customer.city → customer_city); arrays stay as JSON text.
    df = pd.json_normalize(records, sep="_")
    columns = [str(c) for c in df.columns]
    rows = [[_cell(v) for v in rec] for rec in df.astype(object).itertuples(index=False, name=None)]
    if any(isinstance(v, dict) for r in records[:500] for v in r.values()):
        notes.append("Nested JSON objects were flattened into separate columns (parent_child).")
    return RawTable(columns=columns, rows=rows, quarantined=bad, notes=notes, options_used={"encoding": encoding})
