"""Read an uploaded Parquet file and keep its native column types.

Unlike text formats, a Parquet file already says what each column is, so nothing is inferred from text: integers,
decimals, dates, timestamps and booleans stay as they are. Nested structs become flat columns (parent_child) and
lists / maps are kept as JSON text, because the Bronze layer stores simple columns.
"""
from __future__ import annotations

import io
import json

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from app.ingestion.parsers import ParseError
from app.ingestion.schema import ID_NAME, Discovery, arrow_type_name, normalise_names, profile


def _text(values: list, render) -> pa.Array:
    return pa.array([None if v is None else render(v) for v in values], pa.string())


def _json(v) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def _flatten(name: str, arr: pa.Array, out: list[tuple[str, pa.Array]], notes: set[str]) -> None:
    t = arr.type
    if pa.types.is_dictionary(t):
        _flatten(name, arr.dictionary_decode(), out, notes)
    elif pa.types.is_struct(t):
        notes.add("Nested Parquet structs were flattened into separate columns (parent_child).")
        for i in range(t.num_fields):
            _flatten(f"{name}_{t.field(i).name}", pc.struct_field(arr, [i]), out, notes)
    elif pa.types.is_map(t):
        notes.add("Parquet lists and maps were kept as JSON text.")
        out.append((name, _text(arr.to_pylist(), lambda v: _json(dict(v)))))
    elif pa.types.is_list(t) or pa.types.is_large_list(t) or pa.types.is_fixed_size_list(t):
        notes.add("Parquet lists and maps were kept as JSON text.")
        out.append((name, _text(arr.to_pylist(), _json)))
    elif pa.types.is_timestamp(t) and t.unit == "ns":
        out.append((name, arr.cast(pa.timestamp("us", tz=t.tz), safe=False)))
    elif pa.types.is_binary(t) or pa.types.is_large_binary(t) or pa.types.is_fixed_size_binary(t):
        out.append((name, _text(arr.to_pylist(), bytes.hex)))
    elif pa.types.is_time(t) or pa.types.is_duration(t):
        out.append((name, _text(arr.to_pylist(), str)))
    elif pa.types.is_null(t):
        out.append((name, pa.nulls(len(arr), pa.string())))
    else:
        out.append((name, arr))


def discover_parquet(data: bytes) -> Discovery:
    if not data:
        raise ParseError("The file is empty")
    try:
        source = pq.read_table(io.BytesIO(data))
    except (pa.ArrowException, OSError, ValueError) as exc:
        raise ParseError(f"This Parquet file cannot be read: it is corrupt or is not a Parquet file ({exc})") from exc
    if source.num_columns == 0 or source.num_rows == 0:
        raise ParseError("The Parquet file has no data rows")

    flat: list[tuple[str, pa.Array]] = []
    notes: set[str] = set()
    for field in source.schema:
        _flatten(field.name, source.column(field.name).combine_chunks(), flat, notes)
    originals = [n for n, _ in flat]
    names = normalise_names(originals)
    warnings, schema, primary_key, renamed = sorted(notes), [], [], []
    for src, name, (_, arr) in zip(originals, names, flat, strict=True):
        typ = arrow_type_name(arr.type)
        non_null = pd.Series([str(v) for v in arr.to_pylist() if v is not None], dtype=object)
        prof = profile(arr, typ, non_null)
        if src != name:
            renamed.append(f"'{src}' → {name}")
        if not primary_key and ID_NAME.match(name) and typ in ("integer", "string") and prof["null_count"] == 0 \
                and prof["distinct_count"] == len(arr):
            primary_key.append(name)
        schema.append({"name": name, "source_name": src, "type": typ, "native_type": str(arr.type),
                       "nullable": prof["null_count"] > 0, **prof})
    if renamed:
        warnings.append(f"{len(renamed)} column name(s) normalised to snake_case for SQL: {', '.join(renamed[:8])}"
                        + (" …" if len(renamed) > 8 else "") + ".")
    table = pa.Table.from_arrays([a for _, a in flat], names=names)
    return Discovery(table=table, schema=schema, warnings=warnings, quarantined=[], primary_key=primary_key)
