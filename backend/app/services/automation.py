"""Pipeline automation: schedules, triggers, run conditions, retries, time limit, notifications, SLA, backfill, calendar.

Settings live in `Pipeline.automation`; what the next decisions need (pending retry, queued trigger, seen dataset
versions, backfill queue, events log) lives in `Pipeline.state`. Everything is driven by the existing scheduler
tick (Celery beat, every 60 s) plus the end of each run — there is no extra process.

Times are UTC (the scheduler, the calendar and the SLA deadlines all use UTC).

Triggers (a run's `trigger`):
  manual · schedule · new_data (a watched dataset got a new version, or a file was dropped) · after_pipeline
  (another pipeline finished) · api (token-protected URL) · retry (automatic retry after a failure) · backfill
"""
from __future__ import annotations

import calendar as cal
import hashlib
import hmac
import logging
import re
import secrets
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.logging import log_event
from app.core.permissions import ROLES
from app.db.base import utcnow
from app.models import Dataset, Pipeline, PipelineRun, User
from app.processing import params as P
from app.services import notify
from app.services.runs import aware

logger = logging.getLogger("orbit.automation")

SCHEDULE_KINDS = ("interval", "daily", "weekly", "monthly", "cron")
NOTIFY_EVENTS = ("failure", "success", "quality_warning", "sla", "retry")
AUTO_TRIGGERS = ("schedule", "new_data", "after_pipeline")  # the ones subject to "skip if no new data"
MAX_EVENTS = 100
MAX_BACKFILL_RUNS = 366
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

DEFAULTS = {
    "schedule": {"enabled": False, "kind": "daily", "every_minutes": 60, "time": "06:00", "weekdays": [1, 2, 3, 4, 5],
                 "day_of_month": 1, "cron": "0 6 * * *"},
    "on_new_data": {"enabled": False, "dataset_ids": [], "file_drop": True},
    "after_pipelines": {"enabled": False, "pipeline_ids": [], "on": "success"},
    "api_trigger": {"enabled": False},
    "conditions": {"skip_if_no_new_data": False, "min_input_quality": None, "if_running": "skip"},
    "retry": {"max_retries": 0, "backoff_seconds": 60, "backoff_factor": 2},
    "timeout_minutes": None,
    "notifications": {"on": ["failure"], "in_app": True, "roles": ["admin", "data_engineer"], "emails": [], "webhook_url": ""},
    "sla": {"enabled": False, "dataset_id": None, "deadline": "08:00", "max_age_hours": None},
    "params": [],
}


def _merge(cfg: dict | None) -> dict:
    out = {}
    for k, v in DEFAULTS.items():
        cur = (cfg or {}).get(k)
        out[k] = {**v, **(cur or {})} if isinstance(v, dict) else (cur if cur is not None else v)
    return out


def settings_of(p: Pipeline) -> dict:
    return _merge(p.automation)


# ── cron ─────────────────────────────────────────────────────────────────
def _field(spec: str, lo: int, hi: int, name: str) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            if not s.isdigit() or int(s) < 1:
                raise ValueError(f"{name}: invalid step '{s}'")
            step = int(s)
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            a = b = int(part)
            if step > 1:
                b = hi
        if a < lo or b > hi or a > b:
            raise ValueError(f"{name}: {part} is outside {lo}-{hi}")
        out |= set(range(a, b + 1, step))
    return out


def parse_cron(expr: str) -> tuple[set, set, set, set, set, bool, bool]:
    parts = (expr or "").split()
    if len(parts) != 5:
        raise ValueError("A cron expression has 5 fields: minute hour day-of-month month day-of-week (e.g. '*/15 8-18 * * 1-5')")
    try:
        minute, hour = _field(parts[0], 0, 59, "minute"), _field(parts[1], 0, 23, "hour")
        dom, month = _field(parts[2], 1, 31, "day of month"), _field(parts[3], 1, 12, "month")
        dow = {d % 7 for d in _field(parts[4], 0, 7, "day of week")}  # 0 and 7 = Sunday
    except ValueError as exc:
        raise ValueError(str(exc) if "outside" in str(exc) or "step" in str(exc) else f"Invalid cron expression: {exc}") from exc
    return minute, hour, dom, month, dow, parts[2] != "*", parts[4] != "*"


def _cron_next(expr: str, after: datetime) -> datetime | None:
    minute, hour, dom, month, dow, dom_r, dow_r = parse_cron(expr)
    start = (after + timedelta(minutes=1)).replace(second=0, microsecond=0)
    day = start.date()
    for _ in range(400 * 5):  # at most ~5 years ahead
        if day.month in month:
            cron_dow = (day.isoweekday()) % 7
            ok = (day.day in dom or cron_dow in dow) if dom_r and dow_r else (day.day in dom and cron_dow in dow)
            if ok:
                for h in sorted(hour):
                    for m in sorted(minute):
                        t = datetime.combine(day, time(h, m), tzinfo=UTC)
                        if t >= start:
                            return t
        day += timedelta(days=1)
    return None


def next_run(schedule: dict, after: datetime) -> datetime | None:
    """First time strictly after `after` (UTC) at which the schedule fires."""
    after = aware(after)
    if not schedule.get("enabled"):
        return None
    kind = schedule.get("kind")
    if kind == "interval":
        return after + timedelta(minutes=int(schedule.get("every_minutes") or 60))
    if kind == "cron":
        return _cron_next(schedule.get("cron") or "", after)
    hh, mm = (int(x) for x in (schedule.get("time") or "06:00").split(":"))
    day = after.date()
    for _ in range(400):
        t = datetime.combine(day, time(hh, mm), tzinfo=UTC)
        if t > after:
            if kind == "daily":
                return t
            if kind == "weekly" and day.isoweekday() in (schedule.get("weekdays") or [1]):
                return t
            if kind == "monthly":
                want = min(int(schedule.get("day_of_month") or 1), cal.monthrange(day.year, day.month)[1])
                if day.day == want:
                    return t
        day += timedelta(days=1)
    return None


def upcoming(schedule: dict, start: datetime, end: datetime, limit: int = 200) -> list[datetime]:
    out, t = [], start
    while len(out) < limit:
        t = next_run(schedule, t)
        if t is None or t > end:
            break
        out.append(t)
    return out


def describe(schedule: dict) -> str:
    if not schedule.get("enabled"):
        return "not scheduled"
    k = schedule.get("kind")
    if k == "interval":
        return f"every {schedule.get('every_minutes')} min"
    if k == "daily":
        return f"daily at {schedule.get('time')} UTC"
    if k == "weekly":
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        return f"{', '.join(names[d - 1] for d in sorted(schedule.get('weekdays') or []))} at {schedule.get('time')} UTC"
    if k == "monthly":
        return f"monthly on day {schedule.get('day_of_month')} at {schedule.get('time')} UTC"
    return f"cron '{schedule.get('cron')}' (UTC)"


# ── validation ───────────────────────────────────────────────────────────
def _bad(field: str, message: str):
    return ValidationFailed(message, details=[{"field": field, "message": message}])


def validate(db: Session, p: Pipeline, cfg: dict) -> dict:
    a = _merge(cfg)
    s = a["schedule"]
    if s["kind"] not in SCHEDULE_KINDS:
        raise _bad("schedule.kind", f"Schedule kind must be one of {', '.join(SCHEDULE_KINDS)}")
    if s["kind"] == "interval" and not 1 <= int(s.get("every_minutes") or 0) <= 60 * 24 * 31:
        raise _bad("schedule.every_minutes", "Every N minutes: between 1 and 44,640")
    if s["kind"] in ("daily", "weekly", "monthly") and not TIME_RE.match(s.get("time") or ""):
        raise _bad("schedule.time", "Time must be HH:MM (24 h, UTC)")
    if s["kind"] == "weekly" and not (s.get("weekdays") and all(1 <= int(d) <= 7 for d in s["weekdays"])):
        raise _bad("schedule.weekdays", "Choose at least one day (1 = Monday … 7 = Sunday)")
    if s["kind"] == "monthly" and not 1 <= int(s.get("day_of_month") or 0) <= 31:
        raise _bad("schedule.day_of_month", "Day of month between 1 and 31")
    if s["kind"] == "cron":
        try:
            parse_cron(s.get("cron") or "")
        except ValueError as exc:
            raise _bad("schedule.cron", str(exc)) from exc
    nd = a["on_new_data"]
    for dsid in nd.get("dataset_ids") or []:
        if db.get(Dataset, dsid) is None:
            raise _bad("on_new_data.dataset_ids", "Unknown dataset")
    ap = a["after_pipelines"]
    if ap.get("on") not in ("success", "any"):
        raise _bad("after_pipelines.on", "Run after success or after any end")
    for pid in ap.get("pipeline_ids") or []:
        if pid == p.id:
            raise _bad("after_pipelines.pipeline_ids", "A pipeline cannot run after itself")
        if db.get(Pipeline, pid) is None:
            raise _bad("after_pipelines.pipeline_ids", "Unknown pipeline")
    if ap.get("enabled") and _creates_cycle(db, p.id, ap.get("pipeline_ids") or []):
        raise _bad("after_pipelines.pipeline_ids", "These dependencies create a loop (A after B after A)")
    c = a["conditions"]
    if c.get("if_running") not in ("skip", "queue"):
        raise _bad("conditions.if_running", "If a run is active: skip or queue")
    if c.get("min_input_quality") not in (None, "") and not 0 <= float(c["min_input_quality"]) <= 100:
        raise _bad("conditions.min_input_quality", "Quality score between 0 and 100")
    r = a["retry"]
    if not 0 <= int(r.get("max_retries") or 0) <= 5:
        raise _bad("retry.max_retries", "Between 0 and 5 automatic retries")
    if not 5 <= int(r.get("backoff_seconds") or 60) <= 3600:
        raise _bad("retry.backoff_seconds", "First wait between 5 and 3,600 seconds")
    if not 1 <= float(r.get("backoff_factor") or 2) <= 10:
        raise _bad("retry.backoff_factor", "Growth factor between 1 and 10")
    if a["timeout_minutes"] not in (None, "") and not 1 <= int(a["timeout_minutes"]) <= 24 * 60:
        raise _bad("timeout_minutes", "Time limit between 1 minute and 24 hours")
    n = a["notifications"]
    if any(e not in NOTIFY_EVENTS for e in n.get("on") or []):
        raise _bad("notifications.on", f"Events: {', '.join(NOTIFY_EVENTS)}")
    if any(r not in ROLES for r in n.get("roles") or []):
        raise _bad("notifications.roles", "Unknown role")
    n["emails"] = [e.strip() for e in (n.get("emails") if isinstance(n.get("emails"), list) else str(n.get("emails") or "").split(","))
                   if e.strip()]
    bad = [e for e in n["emails"] if not EMAIL_RE.match(e)]
    if bad:
        raise _bad("notifications.emails", f"Invalid e-mail address: {bad[0]}")
    if n.get("webhook_url"):
        try:
            notify.check_url(n["webhook_url"])
        except notify.DeliveryError as exc:
            raise _bad("notifications.webhook_url", str(exc)) from exc
    sla = a["sla"]
    if sla.get("enabled"):
        if not sla.get("max_age_hours") and not TIME_RE.match(sla.get("deadline") or ""):
            raise _bad("sla.deadline", "Give a daily deadline (HH:MM UTC) or a maximum age in hours")
        if sla.get("dataset_id") and db.get(Dataset, sla["dataset_id"]) is None:
            raise _bad("sla.dataset_id", "Unknown dataset")
    a["params"] = P.validate_definitions(a.get("params") or [])
    a["timeout_minutes"] = int(a["timeout_minutes"]) if a["timeout_minutes"] not in (None, "") else None
    return a


def _creates_cycle(db: Session, pid: str, upstream: list[str]) -> bool:
    """Would `pid` running after `upstream` create a dependency loop?"""
    deps = {p.id: (settings_of(p)["after_pipelines"].get("pipeline_ids") or []) if settings_of(p)["after_pipelines"].get("enabled") else []
            for p in db.scalars(select(Pipeline))}
    deps[pid] = upstream
    seen, stack = set(), list(upstream)
    while stack:
        cur = stack.pop()
        if cur == pid:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(deps.get(cur, []))
    return False


def save(db: Session, user: User, p: Pipeline, cfg: dict) -> Pipeline:
    from app.services.audit import audit

    a = validate(db, p, cfg)
    p.automation = a
    flag_modified(p, "automation")
    p.next_run_at = next_run(a["schedule"], utcnow()) if a["schedule"]["enabled"] else None
    st = dict(p.state or {})
    if a["on_new_data"]["enabled"]:  # start watching from the current versions (no run for data that already exists)
        st["seen_versions"] = _watched_versions(db, p, a)
    p.state = st
    flag_modified(p, "state")
    event(p, "settings", f"Automation updated: {describe(a['schedule'])}")
    audit(db, actor=user, action="pipeline.automation", resource_type="pipeline", resource_id=p.id,
          details={"schedule": describe(a["schedule"]), "new_data": a["on_new_data"]["enabled"],
                   "after": a["after_pipelines"]["pipeline_ids"] if a["after_pipelines"]["enabled"] else [],
                   "retries": a["retry"]["max_retries"], "timeout": a["timeout_minutes"]})
    return p


def automation_out(db: Session, p: Pipeline) -> dict:
    a = settings_of(p)
    st = p.state or {}
    return {"settings": a, "next_run_at": p.next_run_at, "schedule_text": describe(a["schedule"]),
            "has_token": bool(p.trigger_token_hash), "trigger_url": f"/api/v1/pipelines/{p.id}/trigger",
            "pending": st.get("pending"), "retry": st.get("retry"), "backfill": st.get("backfill"),
            "events": list(reversed(st.get("events") or []))[:50],
            "dependents": [{"id": d.id, "name": d.name} for d in db.scalars(select(Pipeline))
                           if p.id in (settings_of(d)["after_pipelines"].get("pipeline_ids") or []) and settings_of(d)["after_pipelines"]["enabled"]]}


# ── state helpers ────────────────────────────────────────────────────────
def event(p: Pipeline, kind: str, message: str, run_id: str | None = None) -> None:
    st = dict(p.state or {})
    st["events"] = [*(st.get("events") or []), {"at": utcnow().isoformat(timespec="seconds"), "kind": kind, "message": message,
                                                "run_id": run_id}][-MAX_EVENTS:]
    p.state = st
    flag_modified(p, "state")


def _set_state(p: Pipeline, **kw) -> None:
    st = dict(p.state or {})
    for k, v in kw.items():
        if v is None:
            st.pop(k, None)
        else:
            st[k] = v
    p.state = st
    flag_modified(p, "state")


def _graph(db: Session, p: Pipeline) -> dict:
    from app.services.pipelines import latest_version

    lv = latest_version(db, p)
    return lv.graph if lv else p.graph


def _watched_versions(db: Session, p: Pipeline, a: dict) -> dict:
    ids = a["on_new_data"].get("dataset_ids") or [n["config"].get("dataset_id") for n in _graph(db, p)["nodes"]
                                                  if n["type"] in ("dataset_input", "incremental_input") and n["config"].get("dataset_id")]
    out = {}
    for i in ids:
        ds = db.get(Dataset, i)
        if ds:
            out[i] = ds.current_version
    return out


def _pending_files(db: Session, p: Pipeline) -> int:
    from fnmatch import fnmatch

    from app.lakehouse.storage import get_storage
    from app.processing.sources import folder_prefix

    n = 0
    nodes = (p.state or {}).get("nodes") or {}
    for node in _graph(db, p)["nodes"]:
        if node["type"] != "file_drop_input":
            continue
        try:
            prefix = folder_prefix(node["config"].get("folder") or "")
        except ValueError:
            continue
        done = set((nodes.get(node["id"]) or {}).get("processed") or [])
        pat = node["config"].get("pattern") or "*"
        n += sum(1 for f in get_storage().list_keys(prefix) if f["key"] not in done and fnmatch(f["key"].rsplit("/", 1)[-1], pat))
    return n


def has_new_data(db: Session, p: Pipeline) -> tuple[bool, str]:
    """Did any input change since the last successful run?"""
    graph = _graph(db, p)
    if any(n["type"] == "subpipeline" for n in graph["nodes"]):
        return True, "sub-pipeline: always read"
    last = db.scalar(select(PipelineRun).where(PipelineRun.pipeline_id == p.id, PipelineRun.status == "succeeded")
                     .order_by(PipelineRun.queued_at.desc()).limit(1))
    if last is None:
        return True, "first run"
    if _pending_files(db, p):
        return True, "new file(s) dropped"
    read = {i["dataset_id"]: i["version"] for i in last.inputs or []}
    for n in graph["nodes"]:
        if n["type"] in ("dataset_input", "incremental_input") and not n["config"].get("version"):
            ds = db.get(Dataset, n["config"].get("dataset_id") or "")
            if ds and ds.current_version != read.get(ds.id):
                return True, f"{ds.qualified_name} v{ds.current_version}"
    return False, "no input changed since the last successful run"


def _quality_ok(db: Session, p: Pipeline, minimum) -> tuple[bool, str]:
    if minimum in (None, ""):
        return True, ""
    for n in _graph(db, p)["nodes"]:
        if n["type"] in ("dataset_input", "incremental_input"):
            ds = db.get(Dataset, n["config"].get("dataset_id") or "")
            if ds and ds.quality_score is not None and ds.quality_score < float(minimum):
                return False, f"{ds.qualified_name} quality {ds.quality_score:.1f} < {float(minimum):g}"
    return True, ""


def acting_user(db: Session, p: Pipeline, run: PipelineRun | None = None) -> User | None:
    """Whose rights an automatic run uses: the person who triggered it, else the pipeline owner, else an admin."""
    for uid in ((run.triggered_by_id if run else None), p.owner_id):
        u = db.get(User, uid) if uid else None
        if u is not None and u.is_active:
            return u
    return db.scalar(select(User).where(User.role == "admin", User.is_active.is_(True)).limit(1))


# ── triggering ───────────────────────────────────────────────────────────
def trigger(db: Session, p: Pipeline, kind: str, *, user: User | None = None, params: dict | None = None,
            retry_of: PipelineRun | None = None, force: bool = False, start_now: bool = True) -> PipelineRun | None:
    """Start a run unless a condition says otherwise. Returns the run, or None (the reason is in the events log)."""
    from app.services import pipelines as pl

    a = settings_of(p)
    if pl.latest_version(db, p) is None:
        event(p, "skipped", f"{kind}: the pipeline was never published")
        return None
    active = db.scalar(select(PipelineRun).where(PipelineRun.pipeline_id == p.id, PipelineRun.status.in_(("queued", "running"))))
    if active is not None:
        if a["conditions"].get("if_running") == "queue" or kind in ("backfill", "retry"):
            _set_state(p, pending={"trigger": kind, "params": params or {}, "user_id": user.id if user else None,
                                   "retry_of": retry_of.id if retry_of else None, "at": utcnow().isoformat(timespec="seconds")})
            event(p, "queued", f"{kind}: a run is active, this one starts right after it")
        else:
            event(p, "skipped", f"{kind}: a run is already active", active.id)
        return None
    if not force and kind in AUTO_TRIGGERS:
        if a["conditions"].get("skip_if_no_new_data"):
            new, why = has_new_data(db, p)
            if not new:
                event(p, "skipped", f"{kind}: {why}")
                return None
        ok, why = _quality_ok(db, p, a["conditions"].get("min_input_quality"))
        if not ok:
            event(p, "skipped", f"{kind}: {why}")
            return None
    run = pl.enqueue(db, p, user=user or acting_user(db, p), retry_of=retry_of, trigger=kind, params=params or {})
    event(p, "started", f"{kind} run queued", run.id)
    if start_now:
        pl.start(db, run)
    return run


def token_new(db: Session, user: User, p: Pipeline) -> str:
    from app.services.audit import audit

    token = secrets.token_urlsafe(32)
    p.trigger_token_hash = hashlib.sha256(token.encode()).hexdigest()
    a = settings_of(p)
    a["api_trigger"]["enabled"] = True
    p.automation = a
    flag_modified(p, "automation")
    audit(db, actor=user, action="pipeline.trigger_token", resource_type="pipeline", resource_id=p.id)
    return token


def token_revoke(db: Session, user: User, p: Pipeline) -> None:
    from app.services.audit import audit

    p.trigger_token_hash = None
    a = settings_of(p)
    a["api_trigger"]["enabled"] = False
    p.automation = a
    flag_modified(p, "automation")
    audit(db, actor=user, action="pipeline.trigger_token_revoked", resource_type="pipeline", resource_id=p.id)


def token_ok(p: Pipeline, token: str | None) -> bool:
    if not token or not p.trigger_token_hash or not settings_of(p)["api_trigger"].get("enabled"):
        return False
    return hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), p.trigger_token_hash)


# ── backfill ─────────────────────────────────────────────────────────────
def _periods(start: date, end: date, step: str) -> list[tuple[date, date]]:
    out, d = [], start
    while d <= end and len(out) <= MAX_BACKFILL_RUNS:
        if step == "day":
            nxt = d + timedelta(days=1)
        elif step == "week":
            nxt = d + timedelta(days=7)
        else:
            nxt = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
        out.append((d, min(nxt - timedelta(days=1), end)))
        d = nxt
    return out


def backfill(db: Session, user: User, p: Pipeline, *, start: date, end: date, step: str, params: dict | None,
             continue_on_failure: bool) -> dict:
    from app.services.audit import audit

    if step not in ("day", "week", "month"):
        raise _bad("step", "Step must be day, week or month")
    if end < start:
        raise _bad("end", "The end is before the start")
    periods = _periods(start, end, step)
    if len(periods) > MAX_BACKFILL_RUNS:
        raise _bad("end", f"At most {MAX_BACKFILL_RUNS} runs per backfill")
    if (p.state or {}).get("backfill"):
        raise Conflict("A backfill is already in progress for this pipeline — cancel it first")
    P.resolve(settings_of(p)["params"], params)  # validate the extra parameters now
    queue = [{"run_date": a.isoformat(), "start_date": a.isoformat(), "end_date": b.isoformat()} for a, b in periods]
    _set_state(p, backfill={"queue": queue, "total": len(queue), "done": 0, "failed": 0, "params": params or {},
                            "user_id": user.id, "continue_on_failure": continue_on_failure, "step": step})
    event(p, "backfill", f"Backfill {start} → {end} by {step}: {len(queue)} run(s)")
    audit(db, actor=user, action="pipeline.backfill", resource_type="pipeline", resource_id=p.id,
          details={"start": start.isoformat(), "end": end.isoformat(), "step": step, "runs": len(queue)})
    _next_backfill(db, p)
    return (p.state or {}).get("backfill") or {}


def _next_backfill(db: Session, p: Pipeline) -> PipelineRun | None:
    bf = (p.state or {}).get("backfill")
    if not bf:
        return None
    if not bf["queue"]:
        event(p, "backfill", f"Backfill finished: {bf['done']} succeeded, {bf['failed']} failed")
        _set_state(p, backfill=None)
        return None
    if db.scalar(select(PipelineRun).where(PipelineRun.pipeline_id == p.id, PipelineRun.status.in_(("queued", "running")))):
        return None
    period = bf["queue"][0]
    user = db.get(User, bf.get("user_id") or "") or acting_user(db, p)
    run = trigger(db, p, "backfill", user=user, params={**bf.get("params", {}), **period}, force=True, start_now=False)
    if run is None:
        return None
    bf = {**bf, "queue": bf["queue"][1:], "current": period["run_date"]}
    _set_state(p, backfill=bf)
    from app.services.pipelines import start

    start(db, run)
    return run


def cancel_backfill(db: Session, user: User, p: Pipeline) -> None:
    bf = (p.state or {}).get("backfill")
    if not bf:
        raise NotFound("No backfill in progress")
    event(p, "backfill", f"Backfill cancelled by {user.full_name}: {len(bf['queue'])} run(s) not started")
    _set_state(p, backfill=None)


# ── after a run ──────────────────────────────────────────────────────────
def _notify(db: Session, p: Pipeline, kind: str, *, title: str, message: str, level: str, run: PipelineRun | None) -> None:
    n = settings_of(p)["notifications"]
    if kind not in (n.get("on") or []):
        return
    res = notify.deliver(db, n, title=title, message=message, level=level, link=f"/runs/{run.id}" if run else f"/pipelines/{p.id}",
                         pipeline_id=p.id, run_id=run.id if run else None, owner_id=p.owner_id)
    failed = [r for r in res if r["status"] != "sent"]
    event(p, "notification", f"{kind} notification: " + ", ".join(f"{r['channel']} {r['status']}" for r in res)
          + (f" ({failed[0]['detail']})" if failed else ""), run.id if run else None)


def on_finished(db: Session, run: PipelineRun) -> None:
    """Called at the end of every run (in the worker): retries, notifications, dependents, queued triggers, backfill."""
    p = db.get(Pipeline, run.pipeline_id)
    if p is None:
        return
    a = settings_of(p)
    st = p.state or {}
    if run.status == "failed":
        r = a["retry"]
        retries = int(r.get("max_retries") or 0)
        if retries and run.attempt <= retries and run.error_class not in ("quality_gate_failed", "ValidationFailed", "validation_failed", "medallion_rule_violation"):
            wait = int(r.get("backoff_seconds") or 60) * float(r.get("backoff_factor") or 2) ** (run.attempt - 1)
            due = utcnow() + timedelta(seconds=wait)
            _set_state(p, retry={"run_id": run.id, "due_at": due.isoformat(timespec="seconds"), "attempt": run.attempt + 1})
            event(p, "retry", f"Automatic retry {run.attempt}/{retries} in {wait:.0f} s", run.id)
            _notify(db, p, "retry", title=f"{p.name}: retry {run.attempt}/{retries} scheduled", level="warning", run=run,
                    message=f"The run failed ({run.error_message}). ORBIT retries in {wait:.0f} s.")
        else:
            _notify(db, p, "failure", title=f"{p.name} failed", level="error", run=run,
                    message=f"{run.error_class}: {run.error_message}" + (f" (after {run.attempt} attempts)" if run.attempt > 1 else ""))
    elif run.status == "succeeded":
        if st.get("retry"):
            _set_state(p, retry=None)
        warnings = [f"{nid}: {q['name']}" for nid, ns in (run.node_status or {}).items() for q in ns.get("quality") or []
                    if not q.get("passed")]
        warnings += [f"{x['label']}: {x['message']}" for x in run.artifacts or [] if x.get("status") == "failed"]
        if warnings:
            _notify(db, p, "quality_warning", title=f"{p.name} succeeded with warnings", level="warning", run=run,
                    message="; ".join(warnings[:5]))
        _notify(db, p, "success", title=f"{p.name} succeeded", level="success", run=run,
                message=f"{run.records_out or 0:,} row(s) written in {(run.duration_ms or 0) / 1000:.1f} s")
    if run.trigger == "backfill" and st.get("backfill"):
        bf = dict(st["backfill"])
        bf["done" if run.status == "succeeded" else "failed"] = bf.get("done" if run.status == "succeeded" else "failed", 0) + 1
        if run.status != "succeeded" and not bf.get("continue_on_failure"):
            event(p, "backfill", f"Backfill stopped: the run for {bf.get('current')} {run.status}", run.id)
            bf = None
        _set_state(p, backfill=bf)
    db.commit()
    # dependents ("run after")
    if run.status in ("succeeded", "failed"):
        for d in db.scalars(select(Pipeline).where(Pipeline.id != p.id)):
            ap = settings_of(d)["after_pipelines"]
            if ap.get("enabled") and p.id in (ap.get("pipeline_ids") or []) and (run.status == "succeeded" or ap.get("on") == "any"):
                trigger(db, d, "after_pipeline", params=None)
                db.commit()
    _start_pending(db, p)
    if (p.state or {}).get("backfill"):
        _next_backfill(db, p)
        db.commit()


def _start_pending(db: Session, p: Pipeline) -> None:
    pend = (p.state or {}).get("pending")
    if not pend:
        return
    if db.scalar(select(PipelineRun).where(PipelineRun.pipeline_id == p.id, PipelineRun.status.in_(("queued", "running")))):
        return
    _set_state(p, pending=None)
    db.commit()
    retry_of = db.get(PipelineRun, pend.get("retry_of") or "") if pend.get("retry_of") else None
    trigger(db, p, pend["trigger"], user=db.get(User, pend.get("user_id") or "") if pend.get("user_id") else None,
            params=pend.get("params"), retry_of=retry_of, force=True)
    db.commit()


# ── scheduler tick ───────────────────────────────────────────────────────
def _check_sla(db: Session, p: Pipeline, now: datetime) -> None:
    sla = settings_of(p)["sla"]
    if not sla.get("enabled"):
        return
    ds = db.get(Dataset, sla.get("dataset_id") or "") if sla.get("dataset_id") else None
    if ds is None:
        last = db.scalar(select(PipelineRun).where(PipelineRun.pipeline_id == p.id, PipelineRun.status == "succeeded")
                         .order_by(PipelineRun.queued_at.desc()).limit(1))
        out = (last.outputs or [None])[0] if last else None
        ds = db.get(Dataset, out["dataset_id"]) if out else None
    updated = aware(ds.last_updated_at) if ds and ds.last_updated_at else None
    st = p.state or {}
    name = ds.qualified_name if ds else "the output dataset"
    if sla.get("max_age_hours"):
        limit = timedelta(hours=float(sla["max_age_hours"]))
        late = updated is None or now - updated > limit
        key = f"age:{updated.isoformat() if updated else 'never'}"
        msg = f"{name} was last updated {updated:%Y-%m-%d %H:%M} UTC, more than {float(sla['max_age_hours']):g} h ago" if updated \
            else f"{name} has never been written"
    else:
        hh, mm = (int(x) for x in sla["deadline"].split(":"))
        deadline = datetime.combine(now.date(), time(hh, mm), tzinfo=UTC)
        late = now >= deadline and (updated is None or updated < datetime.combine(now.date(), time(0, 0), tzinfo=UTC))
        key = f"day:{now.date().isoformat()}"
        msg = f"{name} was not updated today before {sla['deadline']} UTC"
    if late and st.get("sla_alerted") != key:
        _set_state(p, sla_alerted=key)
        event(p, "sla", f"SLA missed: {msg}")
        _notify(db, p, "sla", title=f"SLA missed · {p.name}", message=msg, level="error", run=None)


def tick(db: Session, now: datetime | None = None) -> dict:
    """One scheduler pass over every pipeline. `now` can be given (tests)."""
    now = aware(now) if now else utcnow()
    started = {"schedule": 0, "retry": 0, "new_data": 0, "pending": 0, "backfill": 0}
    for p in list(db.scalars(select(Pipeline))):
        a = settings_of(p)
        st = p.state or {}
        try:
            # 1. automatic retry that is due
            r = st.get("retry")
            if r and datetime.fromisoformat(r["due_at"]) <= now:
                old = db.get(PipelineRun, r["run_id"])
                _set_state(p, retry=None)
                if old is not None and trigger(db, p, "retry", user=db.get(User, old.triggered_by_id or "") or None, retry_of=old,
                                               params=old.params, force=True):
                    started["retry"] += 1
            # 2. schedule
            if a["schedule"]["enabled"] and p.next_run_at and aware(p.next_run_at) <= now:
                p.next_run_at = next_run(a["schedule"], now)
                if trigger(db, p, "schedule"):
                    started["schedule"] += 1
            # 3. new data (datasets with a new version, files dropped)
            if a["on_new_data"]["enabled"]:
                seen = st.get("seen_versions") or {}
                cur = _watched_versions(db, p, a)
                changed = [i for i, v in cur.items() if v != seen.get(i)]
                files = _pending_files(db, p) if a["on_new_data"].get("file_drop", True) else 0
                if changed or files:
                    _set_state(p, seen_versions=cur)
                    if trigger(db, p, "new_data"):
                        started["new_data"] += 1
            # 4. a trigger that waited for the previous run, a backfill step
            if (p.state or {}).get("pending"):
                _start_pending(db, p)
                started["pending"] += 1
            if (p.state or {}).get("backfill") and _next_backfill(db, p):
                started["backfill"] += 1
            # 5. SLA
            _check_sla(db, p, now)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - one bad pipeline must not stop the others
            db.rollback()
            log_event(logger, logging.ERROR, "automation.tick_failed", pipeline_id=p.id, error=str(exc))
    if any(started.values()):
        log_event(logger, logging.INFO, "automation.started", **started)
    return started


# ── calendar ─────────────────────────────────────────────────────────────
def calendar(db: Session, start: datetime, end: datetime) -> dict:
    start, end = aware(start), aware(end)
    if end - start > timedelta(days=62):
        raise _bad("end", "At most 62 days at a time")
    names = {p.id: p.name for p in db.scalars(select(Pipeline))}
    runs = db.scalars(select(PipelineRun).where(PipelineRun.queued_at >= start, PipelineRun.queued_at < end)
                      .order_by(PipelineRun.queued_at)).all()
    upcoming_runs = []
    for p in db.scalars(select(Pipeline)):
        sch = settings_of(p)["schedule"]
        if sch.get("enabled"):
            for t in upcoming(sch, max(start, utcnow()), end, limit=500):
                upcoming_runs.append({"pipeline_id": p.id, "pipeline": p.name, "at": t, "schedule": describe(sch)})
    return {"start": start, "end": end,
            "runs": [{"id": r.id, "pipeline_id": r.pipeline_id, "pipeline": names.get(r.pipeline_id), "status": r.status,
                      "trigger": r.trigger, "queued_at": r.queued_at, "duration_ms": r.duration_ms, "params": r.params} for r in runs],
            "upcoming": sorted(upcoming_runs, key=lambda x: x["at"])[:1000]}
