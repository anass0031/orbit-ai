"""Pipeline parameters: `{{name}}` placeholders in filters, SQL, expressions and file names.

Definitions live in the pipeline automation settings: [{"name": "region", "type": "string", "default": "Europe"}].
Built-in parameters are always available:
  run_date   – date of the run (backfills set it to the replayed day)
  start_date – first day of the period (defaults to run_date)
  end_date   – last day of the period (defaults to run_date)
  run_id     – id of the run

In SQL, a placeholder becomes a typed literal ('Europe', DATE '2026-11-16', 42) — values are never pasted as raw
SQL, so a parameter cannot inject a statement. In file names / subjects, it becomes plain text restricted to
letters, digits, '-', '_' and '.'.
"""
from __future__ import annotations

import re
from datetime import date

from app.core.errors import ValidationFailed

PARAM_TYPES = ("string", "integer", "number", "date", "boolean")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
PLACEHOLDER = re.compile(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}")
BUILTINS = ("run_date", "start_date", "end_date", "run_id")


def coerce(name: str, typ: str, value):
    if value is None or value == "":
        return None
    try:
        if typ == "integer":
            return int(value)
        if typ == "number":
            return float(value)
        if typ == "boolean":
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("1", "true", "yes", "on")
        if typ == "date":
            return date.fromisoformat(str(value)[:10]).isoformat()
        return str(value)[:500]
    except (TypeError, ValueError) as exc:
        raise ValidationFailed(f"Parameter {name}: '{value}' is not a valid {typ}",
                               details=[{"field": f"params.{name}", "message": f"expected {typ}"}]) from exc


def validate_definitions(defs: list[dict]) -> list[dict]:
    out, seen = [], set()
    for i, d in enumerate(defs or []):
        name = str(d.get("name") or "").strip()
        typ = d.get("type") or "string"
        if not NAME_RE.match(name):
            raise ValidationFailed(f"Parameter {i + 1}: name must be lowercase snake_case",
                                   details=[{"field": f"params.{i}.name", "message": "lowercase snake_case"}])
        if name in BUILTINS or name in seen:
            raise ValidationFailed(f"Parameter '{name}' is reserved or defined twice",
                                   details=[{"field": f"params.{i}.name", "message": "reserved or duplicate"}])
        if typ not in PARAM_TYPES:
            raise ValidationFailed(f"Parameter '{name}': type must be one of {', '.join(PARAM_TYPES)}")
        seen.add(name)
        out.append({"name": name, "type": typ, "default": coerce(name, typ, d.get("default")),
                    "description": (d.get("description") or "")[:200]})
    return out


def resolve(defs: list[dict], overrides: dict | None, *, run_date: str | None = None, run_id: str = "preview") -> dict:
    """{name: {"type": t, "value": v}} for the built-ins and the declared parameters."""
    overrides = dict(overrides or {})
    rd = coerce("run_date", "date", overrides.pop("run_date", None) or run_date or date.today().isoformat())
    out = {"run_date": {"type": "date", "value": rd},
           "start_date": {"type": "date", "value": coerce("start_date", "date", overrides.pop("start_date", None)) or rd},
           "end_date": {"type": "date", "value": coerce("end_date", "date", overrides.pop("end_date", None)) or rd},
           "run_id": {"type": "string", "value": run_id}}
    known = {d["name"]: d for d in defs or []}
    unknown = [k for k in overrides if k not in known]
    if unknown:
        raise ValidationFailed(f"Unknown parameter(s): {', '.join(unknown)}",
                               details=[{"field": "params", "message": f"unknown: {', '.join(unknown)}"}])
    for name, d in known.items():
        v = coerce(name, d["type"], overrides[name]) if name in overrides else d.get("default")
        out[name] = {"type": d["type"], "value": v}
    return out


def values(params: dict) -> dict:
    return {k: v["value"] for k, v in params.items()}


def _literal(p: dict) -> str:
    v, t = p["value"], p["type"]
    if v is None:
        return "NULL"
    if t in ("integer", "number"):
        return repr(v)
    if t == "boolean":
        return "TRUE" if v else "FALSE"
    text = str(v).replace("'", "''")
    return f"DATE '{text}'" if t == "date" else f"'{text}'"


def sql(text: str, params: dict) -> str:
    """Replace {{name}} with a typed SQL literal. Unknown names are an error (typo protection)."""
    def rep(m: re.Match) -> str:
        name = m.group(1)
        if name not in params:
            raise KeyError(name)
        return _literal(params[name])
    return PLACEHOLDER.sub(rep, text or "")


def text(template: str, params: dict) -> str:
    """Replace {{name}} with a filename-safe rendering of the value."""
    def rep(m: re.Match) -> str:
        name = m.group(1)
        if name not in params:
            raise KeyError(name)
        return re.sub(r"[^A-Za-z0-9_.-]", "_", str(params[name]["value"] if params[name]["value"] is not None else ""))
    return PLACEHOLDER.sub(rep, template or "")


def used(text_: str) -> set[str]:
    return set(PLACEHOLDER.findall(text_ or ""))
