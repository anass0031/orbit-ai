"""Destination nodes: executed by the pipeline run after the dataset outputs are written.

Each node produces an *artifact* on the run: {node_id, type, label, status: succeeded|failed|skipped, message, details}.
`on_error` (per node): "fail" (default; the run fails, datasets already written are kept) or "warn" (the run continues).
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pacsv
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.serialize import jsonable
from app.lakehouse import catalog
from app.lakehouse.storage import get_storage
from app.models import Pipeline, PipelineRun, User
from app.processing import params as P
from app.services import notify

MIME = {"csv": "text/csv", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "json": "application/json", "parquet": "application/octet-stream"}


class EffectFailed(Exception):
    code = "destination_failed"
    retryable = True

    def __init__(self, node_id: str, message: str):
        super().__init__(message)
        self.node_id = node_id


def render(template: str, params: dict) -> str:
    def rep(m):
        v = params.get(m.group(1), {}).get("value")
        return "" if v is None else str(v)
    return P.PLACEHOLDER.sub(rep, template or "")


def rows_of(table: pa.Table, limit: int | None = None) -> tuple[list[dict], list[list]]:
    t = table.slice(0, limit) if limit else table
    cols = [{"name": f.name, "type": _logical(f.type)} for f in t.schema]
    data = t.to_pylist()
    return cols, [[jsonable(r[c["name"]]) for c in cols] for r in data]


def _logical(t: pa.DataType) -> str:
    if pa.types.is_integer(t):
        return "integer"
    if pa.types.is_floating(t) or pa.types.is_decimal(t):
        return "double"
    if pa.types.is_boolean(t):
        return "boolean"
    if pa.types.is_date(t):
        return "date"
    if pa.types.is_timestamp(t):
        return "timestamp"
    return "string"


# ── files ────────────────────────────────────────────────────────────────
def file_bytes(table: pa.Table, fmt: str) -> bytes:
    if fmt == "csv":
        buf = io.BytesIO()
        pacsv.write_csv(table, buf)
        return buf.getvalue()
    if fmt == "parquet":
        return catalog.to_parquet_bytes(table)
    df = table.to_pandas()
    if fmt == "json":
        return df.to_json(orient="records", date_format="iso", force_ascii=False).encode("utf8")
    for c in df.columns:  # Excel has no time zones
        if hasattr(df[c], "dt") and getattr(df[c].dt, "tz", None) is not None:
            df[c] = df[c].dt.tz_localize(None)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()


def export_file(ctx: dict, node: dict, table: pa.Table) -> dict:
    c, run, p = node["config"], ctx["run"], ctx["pipeline"]
    fmt = c.get("format") or "csv"
    name = P.text(c.get("filename") or "export_{{run_date}}", ctx["params"])
    data = file_bytes(table, fmt)
    key = f"exports/{p.id}/{run.id}/{name}.{fmt}"
    get_storage().put_bytes(key, data, MIME[fmt])
    details = {"key": key, "filename": f"{name}.{fmt}", "size": len(data), "rows": table.num_rows, "format": fmt}
    shared = get_settings().export_dir
    if shared and c.get("copy_to_shared_folder", True):
        folder = Path(shared) / re.sub(r"[^A-Za-z0-9_-]", "_", p.name)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.{fmt}").write_bytes(data)
        details["shared_copy"] = str(folder / f"{name}.{fmt}")
    return {"message": f"{name}.{fmt} · {table.num_rows:,} rows · {len(data):,} bytes", "details": details}


# ── REST ─────────────────────────────────────────────────────────
def rest_output(ctx: dict, node: dict, table: pa.Table) -> dict:
    c = node["config"]
    method, batch = c.get("method") or "POST", int(c.get("batch_size") or 500)
    headers = {"Content-Type": "application/json", "User-Agent": "ORBIT-AI/1.0"}
    url = c["url"]
    for h in c.get("headers") or []:
        if h.get("name"):
            headers[str(h["name"])] = render(str(h.get("value") or ""), ctx["params"])
    _, data = rows_of(table)
    names = table.column_names
    records = [dict(zip(names, r, strict=True)) for r in data]
    wrap = c.get("wrapper_key") or None
    calls, statuses = [], {}
    chunks = [records[i:i + batch] for i in range(0, len(records), batch)] if (c.get("mode") or "batch") == "batch" else [[r] for r in records]
    if len(chunks) > 10_000:
        raise ValueError("Too many requests (more than 10,000): use batch mode or a bigger batch size")
    for chunk in chunks:
        body = chunk if (c.get("mode") or "batch") == "batch" else chunk[0]
        if wrap:
            body = {wrap: body}
        r = notify.http_send(method, url, json=body, headers=headers)
        statuses[r.status_code] = statuses.get(r.status_code, 0) + 1
        calls.append(r.status_code)
        if r.status_code >= 400:
            raise ValueError(f"{method} {url} answered HTTP {r.status_code}: {r.text[:200]}")
    return {"message": f"{len(records):,} record(s) sent in {len(calls)} request(s) to {url}",
            "details": {"url": url, "method": method, "requests": len(calls), "records": len(records), "statuses": statuses}}


# ── e-mail ───────────────────────────────────────────────────────────────
def _html_table(cols: list[dict], rows: list[list]) -> str:
    def esc(v):
        return str("" if v is None else v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    head = "".join(f"<th style='text-align:left;padding:4px 8px;border-bottom:1px solid #ccc'>{esc(x['name'])}</th>" for x in cols)
    body = "".join("<tr>" + "".join(f"<td style='padding:4px 8px;border-bottom:1px solid #eee'>{esc(v)}</td>" for v in r) + "</tr>"
                   for r in rows)
    return f"<table style='border-collapse:collapse;font-family:sans-serif;font-size:13px'><tr>{head}</tr>{body}</table>"


def email_report(ctx: dict, node: dict, table: pa.Table) -> dict:
    c, p = node["config"], ctx["pipeline"]
    to = [t.strip() for t in (c["to"] if isinstance(c["to"], list) else str(c["to"]).split(",")) if t.strip()]
    subject = render(c.get("subject") or f"{p.name} — {{{{run_date}}}}", ctx["params"])
    include = c.get("include") or "table"
    max_rows = int(c.get("max_rows") or 50)
    cols, rows = rows_of(table, max_rows)
    intro = render(c.get("message") or "", ctx["params"])
    link = f"{get_settings().public_url.rstrip('/')}/runs/{ctx['run'].id}"
    text = (f"{intro}\n\n" if intro else "") + f"{table.num_rows:,} row(s) — pipeline '{p.name}'.\n{link}\n"
    html = None
    if include in ("table", "both"):
        text += "\n" + "\n".join(" | ".join(str(v) for v in r) for r in [[x["name"] for x in cols], *rows])
        html = (f"<p>{intro}</p>" if intro else "") + f"<p>{table.num_rows:,} row(s) — pipeline <b>{p.name}</b>"
        html += f"{' (first ' + str(max_rows) + ' shown)' if table.num_rows > max_rows else ''}.</p>{_html_table(cols, rows)}"
        html += f"<p><a href='{link}'>Open the run in ORBIT</a></p>"
    files = []
    if include in ("csv", "both"):
        files.append((f"{re.sub(r'[^A-Za-z0-9_-]', '_', p.name)}.csv", file_bytes(table.slice(0, 100_000), "csv"), "text/csv"))
    notify.send_email(to, subject, text, html, files)
    return {"message": f"sent to {', '.join(to)}", "details": {"to": to, "subject": subject, "rows": table.num_rows,
                                                              "attachment": bool(files)}}


RUNNERS = {"export_file": export_file, "rest_output": rest_output, "email_report": email_report}


def run_effects(db: Session, *, run: PipelineRun, pipeline: Pipeline, user: User, effects: list[tuple[dict, pa.Table]],
                written: dict, params: dict, log, now) -> list[dict]:
    """Execute the destination nodes in order. Returns the artifacts; raises EffectFailed on a blocking failure."""
    ctx = {"db": db, "run": run, "pipeline": pipeline, "user": user, "graph": run.graph_snapshot, "written": written,
           "params": params, "now": now}
    artifacts = []
    for node, table in effects:
        label = node.get("label") or node["type"]
        policy = node["config"].get("on_error") or "fail"
        art = {"node_id": node["id"], "type": node["type"], "label": label}
        try:
            out = RUNNERS[node["type"]](ctx, node, table)
            art.update(status="succeeded", **out)
            log(f"Destination '{label}': {out['message']}")
        except Exception as exc:  # noqa: BLE001 - every failure becomes a readable artifact
            msg = str(getattr(exc, "message", None) or exc).strip().splitlines()[0][:500] if str(exc).strip() else type(exc).__name__
            art.update(status="failed", message=msg, details={"error_class": type(exc).__name__})
            log(f"Destination '{label}' failed: {msg}", level="error" if policy == "fail" else "warning")
            artifacts.append(art)
            if policy == "fail":
                run.artifacts = artifacts
                raise EffectFailed(node["id"], f"Destination '{label}' failed: {msg}") from exc
            continue
        artifacts.append(art)
    return artifacts
