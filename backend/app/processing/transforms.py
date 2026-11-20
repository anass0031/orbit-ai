"""Transformation, quality and destination nodes added to the visual ETL (all run after the DuckDB lockdown).

Each builder receives the engine, the node and its upstream keys, and materialises the node's table. Destination
nodes only check their configuration here (so mistakes are shown at validation time) and pass their input through;
the pipeline service performs the delivery once the datasets are written.
"""
from __future__ import annotations

import re

import pyarrow as pa

from app.processing import params as P
from app.processing.engine import (
    DuckDBEngine,
    NodeError,
    NodeResult,
    QualityGateFailed,
    check_expression,
    col,
    ident,
    kind,
)
from app.services.quality import qi

DEFAULT_NULL_TOKENS = ["", "na", "n/a", "null", "none", "-", "?", "nan"]
DEFAULT_DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S",
                        "%d/%m/%Y %H:%M", "%Y%m%d"]
WINDOW_FUNCS = ("row_number", "rank", "dense_rank", "percent_rank", "running_sum", "running_avg", "running_count",
                "running_min", "running_max", "moving_avg", "moving_sum", "lag", "lead", "first_value", "last_value",
                "percent_of_total")
FILL_STRATEGIES = ("value", "zero", "empty_text", "mean", "median", "mode", "previous", "next")
PIVOT_AGGS = ("sum", "count", "avg", "min", "max", "first")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def lit(v) -> str:
    """A SQL literal for a user value (numbers stay numbers, everything else is a quoted string)."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int | float):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def _schema(eng: DuckDBEngine, ups: list[str]) -> dict[str, str]:
    return eng.schemas.get(ups[0], {}) if ups else {}


def _cols(nid: str, names, sch: dict, field: str) -> list[str]:
    return [col(nid, n, sch, field) for n in names or []]


def _order(nid: str, c: dict, sch: dict, field: str = "order_by") -> str:
    o = c.get(field)
    if not o:
        return ""
    if isinstance(o, list):
        parts = [f"{col(nid, x['column'], sch, field)} {'DESC' if x.get('desc') else 'ASC'} NULLS LAST" for x in o]
        return ", ".join(parts)
    return f"{col(nid, o, sch, field)} {'DESC' if c.get('desc') else 'ASC'} NULLS LAST"


# ── cleaning ────────────────────────────────────────────────────────────
def clean(eng: DuckDBEngine, node: dict, ups: list[str]) -> NodeResult:
    nid, c = node["id"], node.get("config", {})
    sch = _schema(eng, ups)
    text_cols = [k for k, v in sch.items() if kind(v) == "string"]
    targets = c.get("columns") or text_cols
    for n in targets:
        col(nid, n, sch, "columns")
    tokens = [str(x).strip().lower() for x in (c.get("null_tokens") if c.get("null_tokens") is not None else DEFAULT_NULL_TOKENS)]
    case = c.get("case") or "none"
    if case not in ("none", "upper", "lower", "title"):
        raise NodeError(nid, "Case must be none, upper, lower or title", "case")
    numbers, dates = set(c.get("number_columns") or []), set(c.get("date_columns") or [])
    for n in numbers | dates:
        col(nid, n, sch, "number_columns" if n in numbers else "date_columns")
    formats = c.get("date_formats") or DEFAULT_DATE_FORMATS
    repl = []
    for name in sch:
        is_text = kind(sch[name]) == "string"
        x = qi(name)
        changed = False
        if is_text and (name in targets or name in numbers or name in dates):
            if c.get("trim", True):
                x, changed = f"trim(regexp_replace({x}, '\\s+', ' ', 'g'))", True
            if tokens:
                x, changed = f"CASE WHEN lower(trim({x})) IN ({', '.join(lit(t) for t in tokens)}) THEN NULL ELSE {x} END", True
            if name in targets and case == "upper":
                x = f"upper({x})"
            elif name in targets and case == "lower":
                x = f"lower({x})"
            elif name in targets and case == "title":
                x = f"array_to_string(list_transform(string_split(lower({x}), ' '), w -> upper(w[1]) || w[2:]), ' ')"
        if name in numbers and is_text:
            v = f"regexp_replace({x}, '[^0-9,.+\\-eE]', '', 'g')"
            x = (f"TRY_CAST(CASE WHEN {v} LIKE '%,%' AND {v} LIKE '%.%' THEN "
                 f"(CASE WHEN strpos({v}, ',') < strpos({v}, '.') THEN replace({v}, ',', '') "
                 f"ELSE replace(replace({v}, '.', ''), ',', '.') END) "
                 f"WHEN {v} LIKE '%,%' THEN replace({v}, ',', '.') ELSE {v} END AS DOUBLE)")
            changed = True
        if name in dates and is_text:
            x = "COALESCE(" + ", ".join(f"CAST(try_strptime({x}, {lit(f)}) AS DATE)" for f in formats) + f", TRY_CAST({x} AS DATE))"
            changed = True
        if changed or (name in targets and case != "none"):
            repl.append(f"{x} AS {qi(name)}")
    sql = f"SELECT * REPLACE ({', '.join(repl)}) FROM {eng._table(ups[0])}" if repl else f"SELECT * FROM {eng._table(ups[0])}"
    res = eng._materialise(nid, sql)
    res.message = f"{len(repl)} column(s) cleaned"
    return res


def fill_missing(eng: DuckDBEngine, node: dict, ups: list[str]) -> NodeResult:
    nid, c = node["id"], node.get("config", {})
    sch = _schema(eng, ups)
    rules = c.get("rules") or []
    if not rules:
        raise NodeError(nid, "Add at least one column to fill", "rules")
    part = _cols(nid, c.get("partition_by"), sch, "partition_by")
    over_part = f"PARTITION BY {', '.join(part)}" if part else ""
    order = _order(nid, c, sch)
    repl = []
    for r in rules:
        name, strat = r.get("column") or "", r.get("strategy") or "value"
        x = col(nid, name, sch, "rules")
        typ = sch[name]
        if strat not in FILL_STRATEGIES:
            raise NodeError(nid, f"Unknown strategy '{strat}' (use {', '.join(FILL_STRATEGIES)})", "rules")
        numeric = kind(typ) in ("integer", "double")
        if strat in ("mean", "median") and not numeric:
            raise NodeError(nid, f"{strat} needs a numeric column ({name} is {typ.lower()})", "rules")
        if strat in ("previous", "next") and not order:
            raise NodeError(nid, "Previous / next value need an ordering column", "order_by")
        fill = {
            "value": f"TRY_CAST({lit(r.get('value'))} AS {typ})",
            "zero": f"CAST(0 AS {typ})" if numeric else None,
            "empty_text": "''" if kind(typ) == "string" else None,
            "mean": f"CAST(avg({x}) OVER ({over_part}) AS {typ})",
            "median": f"CAST(median({x}) OVER ({over_part}) AS {typ})",
            "mode": f"mode({x}) OVER ({over_part})",
            "previous": f"last_value({x} IGNORE NULLS) OVER ({over_part} ORDER BY {order} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)",
            "next": f"first_value({x} IGNORE NULLS) OVER ({over_part} ORDER BY {order} ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING)",
        }[strat]
        if fill is None:
            raise NodeError(nid, f"'{strat}' does not fit the {typ.lower()} column {name}", "rules")
        if strat == "value" and r.get("value") in (None, ""):
            raise NodeError(nid, f"Give the value used to fill {name}", "rules")
        repl.append(f"COALESCE({x}, {fill}) AS {x}")
    empties = " + ".join(f"count(*) - count({col(nid, r['column'], sch, 'rules')})" for r in rules)
    before = eng.con.execute(f"SELECT {empties} FROM {eng._table(ups[0])}").fetchone()[0]
    res = eng._materialise(nid, f"SELECT * REPLACE ({', '.join(repl)}) FROM {eng._table(ups[0])}")
    res.message = f"{before:,} empty cell(s) before filling"
    return res


def derived_column(eng: DuckDBEngine, node: dict, ups: list[str]) -> NodeResult:
    nid, c = node["id"], node.get("config", {})
    defs = c.get("columns") or []
    if not defs:
        raise NodeError(nid, "Add at least one column", "columns")
    sql, names = f"SELECT * FROM {eng._table(ups[0])}", set(_schema(eng, ups))
    for d in defs:
        name = d.get("name") or ""
        ident(nid, name, "columns")
        expr = eng.sql_text(nid, d.get("expression") or "", "columns")
        check_expression(nid, expr, "columns")
        sql = (f"SELECT * REPLACE (({expr}) AS {qi(name)}) FROM ({sql})" if name in names
               else f"SELECT *, ({expr}) AS {qi(name)} FROM ({sql})")
        names.add(name)
    return eng._materialise(nid, sql)


def split_column(eng: DuckDBEngine, node: dict, ups: list[str]) -> NodeResult:
    nid, c = node["id"], node.get("config", {})
    sch = _schema(eng, ups)
    name = c.get("column") or ""
    x = col(nid, name, sch, "column")
    delim = c.get("delimiter")
    if delim in (None, ""):
        raise NodeError(nid, "Give the delimiter (e.g. a space, ',' or '-')", "delimiter")
    into = [n for n in (c.get("into") or []) if n] or [f"{name}_{i + 1}" for i in range(int(c.get("parts") or 2))]
    for n in into:
        ident(nid, n, "into")
    parts = [f"NULLIF(trim(split_part(CAST({x} AS VARCHAR), {lit(delim)}, {i + 1})), '') AS {qi(n)}" for i, n in enumerate(into[:-1])]
    # the last part keeps the rest of the text ("Jean Paul Martin" → first: Jean, last: Paul Martin)
    k = len(into)
    parts.append(f"NULLIF(trim(array_to_string(string_split(CAST({x} AS VARCHAR), {lit(delim)})[{k}:], {lit(delim)})), '') AS {qi(into[-1])}")
    keep = "*" if c.get("keep_original", True) else f"* EXCLUDE ({x})"
    return eng._materialise(nid, f"SELECT {keep}, {', '.join(parts)} FROM {eng._table(ups[0])}")


def merge_columns(eng: DuckDBEngine, node: dict, ups: list[str]) -> NodeResult:
    nid, c = node["id"], node.get("config", {})
    sch = _schema(eng, ups)
    cols = _cols(nid, c.get("columns"), sch, "columns")
    if len(cols) < 2:
        raise NodeError(nid, "Choose at least two columns to merge", "columns")
    into = c.get("into") or ""
    ident(nid, into, "into")
    sep = lit(c.get("separator") if c.get("separator") is not None else " ")
    vals = [f"CAST({x} AS VARCHAR)" if c.get("skip_nulls", True) else f"COALESCE(CAST({x} AS VARCHAR), '')" for x in cols]
    expr = f"NULLIF(concat_ws({sep}, {', '.join(vals)}), '')"
    base = "*" if c.get("keep_originals", True) else f"* EXCLUDE ({', '.join(cols)})"
    if into in sch and c.get("keep_originals", True):
        return eng._materialise(nid, f"SELECT * REPLACE ({expr} AS {qi(into)}) FROM {eng._table(ups[0])}")
    return eng._materialise(nid, f"SELECT {base}, {expr} AS {qi(into)} FROM {eng._table(ups[0])}")


def lookup(eng: DuckDBEngine, node: dict, ups: list[str]) -> NodeResult:
    nid, c = node["id"], node.get("config", {})
    sch = _schema(eng, ups)
    name = c.get("column") or ""
    x = col(nid, name, sch, "column")
    into = c.get("into") or f"{name}_label"
    ident(nid, into, "into")
    if len(ups) == 2:
        rs = eng.schemas[ups[1]]
        rk, rv = col(nid, c.get("ref_key") or "", rs, "ref_key"), col(nid, c.get("ref_value") or "", rs, "ref_value")
        ref = f"(SELECT CAST({rk} AS VARCHAR) AS __k, any_value({rv}) AS __v FROM {eng._table(ups[1])} GROUP BY 1)"
    else:
        mapping = c.get("mapping") or {}
        if isinstance(mapping, list):
            mapping = {str(m.get("from")): m.get("to") for m in mapping if m.get("from") not in (None, "")}
        if not mapping:
            raise NodeError(nid, "Add at least one code → label pair, or connect a reference input as 2nd input", "mapping")
        eng.con.register("__map", pa.table({"__k": [str(k) for k in mapping], "__v": [None if v is None else str(v) for v in mapping.values()]}))
        eng.con.execute(f"CREATE OR REPLACE TABLE {eng._table(nid)}__map AS SELECT * FROM __map")
        eng.con.unregister("__map")
        ref = f"{eng._table(nid)}__map"
    # no match: the default value if given, else the original code (mapping list) or empty (reference table)
    keep = c.get("keep_unmatched", len(ups) < 2)
    if c.get("default") not in (None, ""):
        value = f"COALESCE(CAST(m.__v AS VARCHAR), {lit(str(c['default']))})"
    elif keep:
        value = f"COALESCE(CAST(m.__v AS VARCHAR), CAST(s.{x} AS VARCHAR))"
    else:
        value = "m.__v"
    sel = f"s.* REPLACE ({value} AS {qi(into)})" if into in sch else f"s.*, {value} AS {qi(into)}"
    res = eng._materialise(nid, f"SELECT {sel} FROM {eng._table(ups[0])} s LEFT JOIN {ref} m ON CAST(s.{x} AS VARCHAR) = m.__k")
    missing = eng.con.execute(f"SELECT count(*) FROM {eng._table(ups[0])} s ANTI JOIN {ref} m ON CAST(s.{x} AS VARCHAR) = m.__k "
                              f"WHERE s.{x} IS NOT NULL").fetchone()[0]
    res.message = f"{missing:,} row(s) without a match" if missing else "every code matched"
    return res


def sort_limit(eng: DuckDBEngine, node: dict, ups: list[str]) -> NodeResult:
    nid, c = node["id"], node.get("config", {})
    sch = _schema(eng, ups)
    order = _order(nid, c, sch)
    if not order:
        raise NodeError(nid, "Choose at least one column to sort by", "order_by")
    limit = c.get("limit")
    if limit not in (None, "") and (int(limit) < 1 or int(limit) > 10_000_000):
        raise NodeError(nid, "The limit must be a positive number", "limit")
    groups = _cols(nid, c.get("group_by"), sch, "group_by")
    src = eng._table(ups[0])
    if groups and limit:
        return eng._materialise(nid, f"SELECT * EXCLUDE (__rn) FROM (SELECT *, row_number() OVER (PARTITION BY {', '.join(groups)} "
                                     f"ORDER BY {order}) AS __rn FROM {src}) WHERE __rn <= {int(limit)} ORDER BY {', '.join(groups)}, {order}")
    return eng._materialise(nid, f"SELECT * FROM {src} ORDER BY {order}" + (f" LIMIT {int(limit)}" if limit else ""))


def pivot(eng: DuckDBEngine, node: dict, ups: list[str]) -> NodeResult:
    nid, c = node["id"], node.get("config", {})
    sch = _schema(eng, ups)
    index = _cols(nid, c.get("index"), sch, "index")
    pc = col(nid, c.get("column") or "", sch, "column")
    agg = c.get("agg") or "sum"
    if agg not in PIVOT_AGGS:
        raise NodeError(nid, f"Aggregate must be one of {', '.join(PIVOT_AGGS)}", "agg")
    value = c.get("value")
    if agg != "count" and not value:
        raise NodeError(nid, "Choose the value column", "value")
    vexpr = "count(*)" if agg == "count" and not value else f"{agg}({col(nid, value, sch, 'value')})"
    src = eng._table(ups[0])
    max_cols = int(c.get("max_columns") or 100)
    fixed = [str(v) for v in c.get("values") or [] if str(v) != ""]
    values = fixed or [r[0] for r in eng.con.execute(f"SELECT DISTINCT CAST({pc} AS VARCHAR) FROM {src} WHERE {pc} IS NOT NULL "
                                                     f"ORDER BY 1 LIMIT {max_cols + 1}").fetchall()]
    if len(values) > max_cols:
        raise NodeError(nid, f"{c.get('column')} has more than {max_cols} distinct values: pivoting would create too many columns", "column")
    if not values:  # validation (no rows yet): only the index columns are known
        eng.dynamic[nid] = node.get("label") or "Pivot"
        res = eng._materialise(nid, f"SELECT {', '.join(index) or '1 AS _empty'} FROM {src} {'GROUP BY ' + ', '.join(index) if index else ''}")
        res.message = "the pivoted columns appear once there is data"
        return res
    group = f" GROUP BY {', '.join(index)}" if index else ""
    res = eng._materialise(nid, f"PIVOT (SELECT *, CAST({pc} AS VARCHAR) AS __pc FROM {src}) ON __pc IN ({', '.join(lit(v) for v in values)}) "
                                f"USING {vexpr}{group}")
    res.message = f"{len(values)} new column(s)"
    return res


def unpivot(eng: DuckDBEngine, node: dict, ups: list[str]) -> NodeResult:
    nid, c = node["id"], node.get("config", {})
    sch = _schema(eng, ups)
    names = c.get("columns") or []
    if not names:
        raise NodeError(nid, "Choose the columns to turn into rows", "columns")
    cols = _cols(nid, names, sch, "columns")
    ncol, vcol = c.get("name_column") or "attribute", c.get("value_column") or "value"
    ident(nid, ncol, "name_column")
    ident(nid, vcol, "value_column")
    same = len({kind(sch[n]) for n in names}) == 1
    cast = "" if same else f" REPLACE ({', '.join(f'CAST({x} AS VARCHAR) AS {x}' for x in cols)})"
    nulls = " INCLUDE NULLS" if c.get("keep_nulls") else ""
    res = eng._materialise(nid, f"UNPIVOT{nulls} (SELECT *{cast} FROM {eng._table(ups[0])}) ON {', '.join(cols)} "
                                f"INTO NAME {qi(ncol)} VALUE {qi(vcol)}")
    if not same:
        res.message = "mixed column types: values converted to text"
    return res


def window(eng: DuckDBEngine, node: dict, ups: list[str]) -> NodeResult:
    nid, c = node["id"], node.get("config", {})
    sch = _schema(eng, ups)
    funcs = c.get("functions") or []
    if not funcs:
        raise NodeError(nid, "Add at least one window function", "functions")
    parts = []
    for f in funcs:
        fn = f.get("func") or ""
        if fn not in WINDOW_FUNCS:
            raise NodeError(nid, f"Unknown function '{fn}'", "functions")
        alias = f.get("alias") or f"{fn}_{f.get('column') or 'n'}"
        ident(nid, alias, "functions")
        part = _cols(nid, f.get("partition_by"), sch, "functions")
        order = _order(nid, f, sch, "order_by")
        x = col(nid, f.get("column") or "", sch, "functions") if fn not in ("row_number", "rank", "dense_rank", "percent_rank") else None
        if fn in ("row_number", "rank", "dense_rank", "percent_rank", "lag", "lead") or fn.startswith(("running", "moving")):
            if not order:
                raise NodeError(nid, f"{fn} needs an ordering column", "functions")
        over = f"PARTITION BY {', '.join(part)} " if part else ""
        over += f"ORDER BY {order}" if order else ""
        size = int(f.get("size") or 3)
        expr = {
            "row_number": f"row_number() OVER ({over})", "rank": f"rank() OVER ({over})",
            "dense_rank": f"dense_rank() OVER ({over})", "percent_rank": f"percent_rank() OVER ({over})",
            "running_sum": f"sum({x}) OVER ({over} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)",
            "running_avg": f"avg({x}) OVER ({over} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)",
            "running_count": f"count({x}) OVER ({over} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)",
            "running_min": f"min({x}) OVER ({over} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)",
            "running_max": f"max({x}) OVER ({over} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)",
            "moving_avg": f"avg({x}) OVER ({over} ROWS BETWEEN {size - 1} PRECEDING AND CURRENT ROW)",
            "moving_sum": f"sum({x}) OVER ({over} ROWS BETWEEN {size - 1} PRECEDING AND CURRENT ROW)",
            "lag": f"lag({x}, {int(f.get('offset') or 1)}) OVER ({over})",
            "lead": f"lead({x}, {int(f.get('offset') or 1)}) OVER ({over})",
            "first_value": f"first_value({x}) OVER ({over} ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)",
            "last_value": f"last_value({x}) OVER ({over} ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)",
            "percent_of_total": f"100.0 * {x} / NULLIF(sum({x}) OVER ({'PARTITION BY ' + ', '.join(part) if part else ''}), 0)",
        }[fn]
        if fn in ("running_sum", "running_avg", "moving_avg", "moving_sum", "percent_of_total") and kind(sch[f["column"]]) not in ("integer", "double"):
            raise NodeError(nid, f"{fn} needs a numeric column", "functions")
        parts.append(f"{expr} AS {qi(alias)}")
    return eng._materialise(nid, f"SELECT *, {', '.join(parts)} FROM {eng._table(ups[0])}")


def scd2(eng: DuckDBEngine, node: dict, ups: list[str]) -> NodeResult:
    from app.lakehouse import catalog

    nid, c = node["id"], node.get("config", {})
    sch = _schema(eng, ups)
    keys = c.get("keys") or []
    if not keys:
        raise NodeError(nid, "Choose the key column(s) of an entity (e.g. customer_id)", "keys")
    kq = _cols(nid, keys, sch, "keys")
    tracked = c.get("tracked") or [k for k in sch if k not in keys and not k.startswith("_")]
    tq = _cols(nid, tracked, sch, "tracked")
    layer, name = c.get("target_layer") or "silver", c.get("target_name") or ""
    if layer not in ("silver", "gold") or not name:
        raise NodeError(nid, "Name the history dataset (the same as the Dataset output that follows)", "target_name")
    eff = "current_timestamp::TIMESTAMP" if (c.get("effective") or "run_time") == "run_time" else "CAST(" + eng.sql_text(nid, "{{run_date}}", "effective") + " AS TIMESTAMP)"
    src = eng._table(ups[0])
    ds = catalog.find_dataset(eng.db, layer, name)
    on = " AND ".join(f"s.{k} IS NOT DISTINCT FROM c.{k}" for k in kq)
    new_cols = f"{eff} AS valid_from, NULL::TIMESTAMP AS valid_to, TRUE AS is_current"
    if ds is None or not ds.current_version:
        res = eng._materialise(nid, f"SELECT *, {new_cols} FROM {src}")
        res.message = f"first load: {res.rows:,} current row(s)"
        return res
    hist = catalog.read_table(catalog.get_version(eng.db, ds, None))
    missing = {"valid_from", "valid_to", "is_current"} - set(hist.column_names)
    if missing:
        raise NodeError(nid, f"{ds.qualified_name} is not a history table (missing {', '.join(sorted(missing))})", "target_name")
    h = f"{eng._table(nid)}__hist"
    eng.con.register("__hist", hist)
    eng.con.execute(f"CREATE OR REPLACE TABLE {h} AS SELECT * FROM __hist")
    eng.con.unregister("__hist")
    diff = " OR ".join(f"s.{t} IS DISTINCT FROM c.{t}" for t in tq) or "FALSE"
    cur = f"(SELECT * FROM {h} WHERE is_current)"
    changed = f"(SELECT s.* FROM {src} s JOIN {cur} c ON {on} WHERE {diff})"
    gone = f"(SELECT c.* FROM {cur} c ANTI JOIN {src} s ON {on})" if c.get("close_missing") else f"(SELECT * FROM {cur} WHERE FALSE)"
    closed_keys = f"(SELECT {', '.join(kq)} FROM {changed} UNION ALL SELECT {', '.join(kq)} FROM {gone})"
    on_ck = " AND ".join(f"c.{k} IS NOT DISTINCT FROM ck.{k}" for k in kq)
    sql = f"""
        SELECT * FROM {h} WHERE NOT is_current
        UNION ALL BY NAME SELECT c.* FROM {cur} c ANTI JOIN {closed_keys} ck ON {on_ck}
        UNION ALL BY NAME SELECT c.* REPLACE ({eff} AS valid_to, FALSE AS is_current) FROM {cur} c SEMI JOIN {closed_keys} ck ON {on_ck}
        UNION ALL BY NAME SELECT *, {new_cols} FROM {changed}
        UNION ALL BY NAME SELECT s.*, {new_cols} FROM {src} s ANTI JOIN {cur} c ON {on}"""
    counts = eng.con.execute(f"SELECT (SELECT count(*) FROM {changed}), (SELECT count(*) FROM {src} s ANTI JOIN {cur} c ON {on}), "
                             f"(SELECT count(*) FROM {gone})").fetchone()
    res = eng._materialise(nid, sql)
    res.message = f"{counts[1]:,} new · {counts[0]:,} changed · {counts[2]:,} closed (missing from the source)"
    return res


def surrogate_key(eng: DuckDBEngine, node: dict, ups: list[str]) -> NodeResult:
    """Stable key: the same business key gives the same number at every run (first 60 bits of an MD5). Empty key → -1."""
    nid, c = node["id"], node.get("config", {})
    sch = _schema(eng, ups)
    keys = _cols(nid, c.get("keys"), sch, "keys")
    if not keys:
        raise NodeError(nid, "Choose the business key column(s)", "keys")
    into = c.get("into") or ""
    ident(nid, into, "into")
    parts = ", ".join(f"COALESCE(CAST({k} AS VARCHAR), '<null>')" for k in keys)
    empty = " AND ".join(f"{k} IS NULL" for k in keys)
    expr = f"CASE WHEN {empty} THEN -1 ELSE CAST('0x' || substr(md5(concat_ws('|', {parts})), 1, 15) AS BIGINT) END"
    src = eng._table(ups[0])
    return eng._materialise(nid, f"SELECT * REPLACE ({expr} AS {qi(into)}) FROM {src}" if into in sch else f"SELECT *, {expr} AS {qi(into)} FROM {src}")


def python(eng: DuckDBEngine, node: dict, ups: list[str]) -> NodeResult:
    from app.processing.sandbox import run_transform

    nid, c = node["id"], node.get("config", {})
    try:
        out = run_transform(c.get("code") or "", eng.arrow(ups[0]))
    except ValueError as exc:
        raise NodeError(nid, str(exc), "code") from exc
    return eng.load_arrow(nid, out)


# ── quality ─────────────────────────────────────────────────────────────
def _verdict(eng: DuckDBEngine, nid: str, c: dict, problems: list[str], res: NodeResult, name: str) -> NodeResult:
    on_failure = c.get("on_failure") or "fail"
    res.quality = [{"name": name, "type": node_type_label(name), "passed": not problems, "failed": len(problems), "total": None,
                    "on_failure": on_failure, "evidence": problems[:10], "error": None}]
    if problems and eng.real and on_failure == "fail":
        raise QualityGateFailed(f"{name}: " + "; ".join(problems[:5]), nid)
    res.message = ("✓ " + name) if not problems else f"{'⚠' if on_failure == 'warn' else '✗'} " + "; ".join(problems[:3])
    return res


def node_type_label(name: str) -> str:
    return name.lower().replace(" ", "_")


def schema_check(eng: DuckDBEngine, node: dict, ups: list[str]) -> NodeResult:
    nid, c = node["id"], node.get("config", {})
    current = {k: kind(v) for k, v in _schema(eng, ups).items()}
    problems = []
    expected = {e["name"]: e.get("type") for e in c.get("expected") or [] if e.get("name")}
    for name, typ in expected.items():
        if name not in current:
            problems.append(f"column '{name}' is missing")
        elif typ and typ != "any" and current[name] != typ:
            problems.append(f"'{name}' is {current[name]}, expected {typ}")
    if (c.get("mode") or "at_least") == "exact" and expected:
        problems += [f"unexpected column '{n}'" for n in current if n not in expected]
    prev = eng.node_state(nid).get("schema") if c.get("compare_previous", True) else None
    if prev:
        for name, typ in prev.items():
            if name not in current:
                problems.append(f"column '{name}' disappeared since the last run")
            elif current[name] != typ:
                problems.append(f"'{name}' changed type since the last run ({typ} → {current[name]})")
        if not c.get("allow_new_columns", True):
            problems += [f"new column '{n}' since the last run" for n in current if n not in prev]
    if eng.dry and problems and expected and (c.get("on_failure") or "fail") == "fail" and not prev:
        raise NodeError(nid, "; ".join(problems[:5]), "expected")
    res = eng._materialise(nid, f"SELECT * FROM {eng._table(ups[0])}")
    if eng.real:
        eng.state_updates[nid] = {"schema": current}
    return _verdict(eng, nid, c, problems, res, "Schema check")


def data_compare(eng: DuckDBEngine, node: dict, ups: list[str]) -> NodeResult:
    nid, c = node["id"], node.get("config", {})
    res = eng._materialise(nid, f"SELECT * FROM {eng._table(ups[0])}")
    n, prev = res.rows or 0, eng.node_state(nid).get("rows")
    problems = []
    if eng.real:
        if c.get("min_rows") not in (None, "") and n < int(c["min_rows"]):
            problems.append(f"{n:,} rows, fewer than the minimum {int(c['min_rows']):,}")
        if prev:
            change = 100.0 * (n - prev) / prev
            if c.get("max_drop_pct") not in (None, "") and change < -float(c["max_drop_pct"]):
                problems.append(f"row count fell {abs(change):.1f}% ({prev:,} → {n:,}), more than the allowed {float(c['max_drop_pct']):g}%")
            if c.get("max_growth_pct") not in (None, "") and change > float(c["max_growth_pct"]):
                problems.append(f"row count grew {change:.1f}% ({prev:,} → {n:,}), more than the allowed {float(c['max_growth_pct']):g}%")
        eng.state_updates[nid] = {"rows": n}
    res = _verdict(eng, nid, c, problems, res, "Row count comparison")
    if not problems:
        res.message = f"{n:,} rows" + (f" (previous run: {prev:,})" if prev else " · first run: baseline recorded" if eng.real else "")
    return res


def anomaly_detection(eng: DuckDBEngine, node: dict, ups: list[str]) -> NodeResult:
    nid, c = node["id"], node.get("config", {})
    sch = _schema(eng, ups)
    name = c.get("column") or ""
    x = col(nid, name, sch, "column")
    if kind(sch[name]) not in ("integer", "double"):
        raise NodeError(nid, f"{name} is not numeric", "column")
    method = c.get("method") or "zscore"
    thr = float(c.get("threshold") or (3.0 if method == "zscore" else 1.5))
    part = _cols(nid, c.get("group_by"), sch, "group_by")
    w = f"OVER (PARTITION BY {', '.join(part)})" if part else "OVER ()"
    if method == "zscore":
        cond = f"abs({x} - avg({x}) {w}) / NULLIF(stddev_pop({x}) {w}, 0) > {thr}"
    elif method == "iqr":
        q1, q3 = f"quantile_cont({x}, 0.25) {w}", f"quantile_cont({x}, 0.75) {w}"
        cond = f"({x} < {q1} - {thr} * ({q3} - {q1}) OR {x} > {q3} + {thr} * ({q3} - {q1}))"
    else:
        raise NodeError(nid, "Method must be zscore or iqr", "method")
    flag = c.get("flag_column") or "is_anomaly"
    ident(nid, flag, "flag_column")
    action = c.get("action") or "flag"
    if action not in ("flag", "remove", "fail"):
        raise NodeError(nid, "Action must be flag, remove or fail", "action")
    tmp = f"{eng._table(nid)}__scored"
    eng.con.execute(f"CREATE OR REPLACE TABLE {tmp} AS SELECT *, COALESCE({cond}, FALSE) AS {qi(flag)} FROM {eng._table(ups[0])}")
    n = eng.con.execute(f"SELECT count(*) FROM {tmp} WHERE {qi(flag)}").fetchone()[0]
    examples = [str(r[0]) for r in eng.con.execute(f"SELECT {x} FROM {tmp} WHERE {qi(flag)} LIMIT 5").fetchall()]
    res = eng._materialise(nid, f"SELECT * EXCLUDE ({qi(flag)}) FROM {tmp} WHERE NOT {qi(flag)}" if action == "remove" else f"SELECT * FROM {tmp}")
    problems = [f"{n:,} unusual value(s) of {name} ({method}, threshold {thr:g}): {', '.join(examples)}"] if n else []
    res = _verdict(eng, nid, {"on_failure": "fail" if action == "fail" else "warn"}, problems, res, "Anomaly detection")
    if n and action != "fail":
        res.message = f"{n:,} anomal{'y' if n == 1 else 'ies'} " + ("removed" if action == "remove" else f"flagged in {flag}")
    return res


def branch(eng: DuckDBEngine, node: dict, ups: list[str]) -> NodeResult:
    nid, c = node["id"], node.get("config", {})
    cond = eng.sql_text(nid, c.get("condition") or "", "condition")
    check_expression(nid, cond, "condition")
    src = eng._table(ups[0])
    yes = eng._materialise(nid, f"SELECT * FROM {src} WHERE COALESCE(({cond}), FALSE)", key=f"{nid}:true")
    no = eng._materialise(nid, f"SELECT * FROM {src} WHERE NOT COALESCE(({cond}), FALSE)", key=f"{nid}:false")
    res = eng._materialise(nid, f"SELECT *, COALESCE(({cond}), FALSE) AS _branch FROM {src}")
    res.message = f"{yes.rows:,} → true · {no.rows:,} → false"
    return res


# ── destinations: configuration checks, data passes through ─────────────
def _pass(eng: DuckDBEngine, nid: str, ups: list[str], message: str | None = None) -> NodeResult:
    res = eng._materialise(nid, f"SELECT * FROM {eng._table(ups[0])}")
    res.message = message
    return res


def _render(eng: DuckDBEngine, nid: str, template: str, field: str) -> str:
    try:
        return P.text(template, eng.params)
    except KeyError as exc:
        raise NodeError(nid, f"Unknown parameter {{{{{exc.args[0]}}}}}", field) from exc


def export_file(eng: DuckDBEngine, node: dict, ups: list[str]) -> NodeResult:
    nid, c = node["id"], node.get("config", {})
    fmt = c.get("format") or "csv"
    if fmt not in ("csv", "xlsx", "json", "parquet"):
        raise NodeError(nid, "Format must be csv, xlsx, json or parquet", "format")
    name = _render(eng, nid, c.get("filename") or "export_{{run_date}}", "filename")
    if not re.match(r"^[A-Za-z0-9_.-]{1,120}$", name):
        raise NodeError(nid, "File name: letters, digits, '-', '_' and '.' only", "filename")
    if fmt == "xlsx" and (_pass(eng, nid, ups).rows or 0) > 1_000_000:
        raise NodeError(nid, "Excel files are limited to 1,000,000 rows", "format")
    return _pass(eng, nid, ups, f"{name}.{fmt}")


def rest_output(eng: DuckDBEngine, node: dict, ups: list[str]) -> NodeResult:
    nid, c = node["id"], node.get("config", {})
    target = c.get("url") or ""
    if not re.match(r"^https?://[^\s/$.?#].[^\s]*$", target):
        raise NodeError(nid, "Give an http(s) URL", "url")
    if (c.get("method") or "POST") not in ("POST", "PUT", "PATCH"):
        raise NodeError(nid, "Method must be POST, PUT or PATCH", "method")
    if not 1 <= int(c.get("batch_size") or 500) <= 10_000:
        raise NodeError(nid, "Batch size must be between 1 and 10,000", "batch_size")
    return _pass(eng, nid, ups, f"{c.get('method') or 'POST'} {target}")


def email_report(eng: DuckDBEngine, node: dict, ups: list[str]) -> NodeResult:
    nid, c = node["id"], node.get("config", {})
    to = [t.strip() for t in (c.get("to") if isinstance(c.get("to"), list) else str(c.get("to") or "").split(",")) if t.strip()]
    if not to:
        raise NodeError(nid, "Add at least one recipient", "to")
    bad = [t for t in to if not EMAIL_RE.match(t)]
    if bad:
        raise NodeError(nid, f"Invalid email address: {bad[0]}", "to")
    if (c.get("include") or "table") not in ("table", "csv", "both"):
        raise NodeError(nid, "Include must be table, csv or both", "include")
    try:
        P.sql(c.get("subject") or "", eng.params)
    except KeyError as exc:
        raise NodeError(nid, f"Unknown parameter {{{{{exc.args[0]}}}}}", "subject") from exc
    return _pass(eng, nid, ups, f"to {', '.join(to[:3])}{'…' if len(to) > 3 else ''}")


BUILDERS = {
    "clean": clean, "fill_missing": fill_missing, "derived_column": derived_column, "split_column": split_column,
    "merge_columns": merge_columns, "lookup": lookup, "sort_limit": sort_limit, "pivot": pivot, "unpivot": unpivot,
    "window": window, "scd2": scd2, "surrogate_key": surrogate_key, "python": python,
    "schema_check": schema_check, "data_compare": data_compare, "anomaly_detection": anomaly_detection, "branch": branch,
    "export_file": export_file, "rest_output": rest_output, "email_report": email_report,
}
