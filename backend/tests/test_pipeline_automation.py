"""Pipeline automation — schedules, triggers, run conditions, retries, time limit, notifications, SLA, backfill, calendar."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db.base import utcnow
from app.db.session import session_scope
from app.models import Pipeline, PipelineRun
from app.services import automation as A
from tests.conftest import bronze, cleaned
from tests.test_pipeline_destinations import FakeSMTP, Receiver, receiver, smtp  # noqa: F401 - fixtures
from tests.test_pipeline_tools import chain, node, pipeline, rows, run


def T(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def auto(client, h, pid, **cfg):
    r = client.put(f"/api/v1/pipelines/{pid}/automation", headers=h, json=cfg)
    assert r.status_code == 200, r.text
    return r.json()


def tick(now=None):
    with session_scope() as db:
        return A.tick(db, now)


def runs_of(client, h, pid):
    return sorted((r for r in client.get(f"/api/v1/runs?pipeline_id={pid}&limit=100", headers=h).json()["items"]),
                  key=lambda r: r["queued_at"])


def simple(client, h, name="feed", csv="id,v\n1,10\n2,20\n"):
    b = bronze(client, h, name, csv)
    p = pipeline(client, h, cleaned(chain(node("in", "dataset_input", dataset_id=b["id"]), node("o", "output", layer="silver", name=f"{name}_out")), ["id"]))
    client.post(f"/api/v1/pipelines/{p['id']}/publish", headers=h)
    return p, b


def test_schedule_maths():
    s = {"enabled": True}
    assert A.next_run({**s, "kind": "interval", "every_minutes": 15}, T("2026-11-16T10:00:00")) == T("2026-11-16T10:15:00")
    assert A.next_run({**s, "kind": "daily", "time": "06:00"}, T("2026-11-16T06:00:00")) == T("2026-11-17T06:00:00")
    assert A.next_run({**s, "kind": "weekly", "time": "09:30", "weekdays": [1, 3]}, T("2026-11-17T12:00:00")) == T("2026-11-18T09:30:00")
    assert A.next_run({**s, "kind": "monthly", "time": "07:00", "day_of_month": 31}, T("2026-11-16T00:00:00")) == T("2026-11-30T07:00:00")
    cron = {**s, "kind": "cron", "cron": "*/20 8-9 * * 1-5"}
    assert A.next_run(cron, T("2026-11-16T09:45:00")) == T("2026-11-17T08:00:00")  # Monday 09:45 → Tuesday 08:00
    assert A.next_run(cron, T("2026-11-20T09:41:00")) == T("2026-11-23T08:00:00")  # Friday → Monday
    assert len(A.upcoming(cron, T("2026-11-16T00:00:00"), T("2026-11-16T23:59:00"))) == 6
    for bad in ("* * *", "61 * * * *", "*/0 * * * *"):
        with pytest.raises(ValueError):
            A.parse_cron(bad)
    assert A.describe({**s, "kind": "weekly", "time": "09:30", "weekdays": [1, 5]}) == "Mon, Fri at 09:30 UTC"


def test_settings_are_validated(client, engineer, analyst):
    p, _ = simple(client, engineer)
    q, _ = simple(client, engineer, "other")
    put = lambda pid, h=engineer, **c: client.put(f"/api/v1/pipelines/{pid}/automation", headers=h, json=c)  # noqa: E731
    assert put(p["id"], h=analyst, schedule={"enabled": True}).status_code == 403
    for cfg, field in [({"schedule": {"enabled": True, "kind": "cron", "cron": "every day"}}, "schedule.cron"),
                       ({"schedule": {"enabled": True, "kind": "daily", "time": "25:00"}}, "schedule.time"),
                       ({"retry": {"max_retries": 9}}, "retry.max_retries"),
                       ({"timeout_minutes": 0}, "timeout_minutes"),
                       ({"notifications": {"emails": ["nope"]}}, "notifications.emails"),
                       ({"notifications": {"webhook_url": "http://169.254.169.254/x"}}, "notifications.webhook_url"),
                       ({"params": [{"name": "run_date"}]}, "params.0.name"),
                       ({"after_pipelines": {"enabled": True, "pipeline_ids": [p["id"]]}}, "after_pipelines.pipeline_ids")]:
        r = put(p["id"], **cfg)
        assert r.status_code == 422 and r.json()["error"]["details"][0]["field"] == field, (cfg, r.text)
    assert put(q["id"], after_pipelines={"enabled": True, "pipeline_ids": [p["id"]]}).status_code == 200
    r = put(p["id"], after_pipelines={"enabled": True, "pipeline_ids": [q["id"]]})
    assert r.status_code == 422 and "loop" in r.json()["error"]["message"]


def test_schedule_and_skip_if_no_new_data(client, engineer):
    p, b = simple(client, engineer)
    out = auto(client, engineer, p["id"], schedule={"enabled": True, "kind": "interval", "every_minutes": 30},
               conditions={"skip_if_no_new_data": True})
    nxt = datetime.fromisoformat(out["next_run_at"].replace("Z", "+00:00"))
    assert out["schedule_text"] == "every 30 min" and abs((nxt - utcnow()).total_seconds() - 1800) < 60
    assert tick()["schedule"] == 0  # not due yet
    assert tick(nxt + timedelta(seconds=1))["schedule"] == 1
    r = runs_of(client, engineer, p["id"])
    assert len(r) == 1 and r[0]["trigger"] == "schedule" and r[0]["status"] == "succeeded"
    tick(nxt + timedelta(minutes=31))  # nothing changed in bronze.feed → skipped
    assert len(runs_of(client, engineer, p["id"])) == 1
    events = client.get(f"/api/v1/pipelines/{p['id']}/automation", headers=engineer).json()["events"]
    assert any(e["kind"] == "skipped" and "no input changed" in e["message"] for e in events)
    bronze(client, engineer, "feed", "id,v\n1,10\n2,20\n3,30\n")
    tick(nxt + timedelta(minutes=62))
    assert len(runs_of(client, engineer, p["id"])) == 2


def test_new_data_and_file_drop_triggers(client, engineer):
    p, _ = simple(client, engineer)
    auto(client, engineer, p["id"], on_new_data={"enabled": True})
    assert tick()["new_data"] == 0  # current versions are the starting point
    bronze(client, engineer, "feed", "id,v\n9,90\n")
    assert tick()["new_data"] == 1
    r = runs_of(client, engineer, p["id"])
    assert r[-1]["trigger"] == "new_data"
    assert client.get(f"/api/v1/runs/{r[-1]['id']}", headers=engineer).json()["inputs"][0]["version"] == 2

    folder = f"drop-{uuid.uuid4().hex[:8]}"
    f = pipeline(client, engineer, cleaned(chain(node("in", "file_drop_input", folder=folder), node("o", "output", layer="silver", name="dropped", mode="append")), ["x"]))
    client.post(f"/api/v1/pipelines/{f['id']}/publish", headers=engineer)
    auto(client, engineer, f["id"], on_new_data={"enabled": True, "file_drop": True})
    assert tick()["new_data"] == 0
    client.post(f"/api/v1/pipelines/dropzone/{folder}", headers=engineer, files={"file": ("a.csv", b"x\n1\n2\n")})
    assert tick()["new_data"] == 1 and tick()["new_data"] == 0  # the file is processed once
    out, _ = rows(client, engineer, "silver", "dropped")
    assert len(out) == 2


def test_after_pipeline_chain_and_api_trigger(client, engineer):
    a, _ = simple(client, engineer, "upstream")
    b, _ = simple(client, engineer, "downstream")
    auto(client, engineer, b["id"], after_pipelines={"enabled": True, "pipeline_ids": [a["id"]], "on": "success"})
    run(client, engineer, a["id"])
    rb = runs_of(client, engineer, b["id"])
    assert len(rb) == 1 and rb[0]["trigger"] == "after_pipeline" and rb[0]["status"] == "succeeded"
    assert client.get(f"/api/v1/pipelines/{a['id']}/automation", headers=engineer).json()["dependents"][0]["id"] == b["id"]

    url = f"/api/v1/pipelines/{a['id']}/trigger"
    assert client.post(url).status_code == 401
    tok = client.post(f"/api/v1/pipelines/{a['id']}/trigger-token", headers=engineer).json()["token"]
    assert client.post(url, headers={"X-Orbit-Trigger-Token": "wrong"}).status_code == 401
    r = client.post(url, headers={"X-Orbit-Trigger-Token": tok}, json={"params": {"run_date": "2026-02-01"}})
    assert r.status_code == 202 and r.json()["started"]
    assert runs_of(client, engineer, a["id"])[-1]["trigger"] == "api" and runs_of(client, engineer, a["id"])[-1]["params"]["run_date"] == "2026-02-01"
    assert len(runs_of(client, engineer, b["id"])) == 2  # the chain follows API runs too
    client.delete(f"/api/v1/pipelines/{a['id']}/trigger-token", headers=engineer)
    assert client.post(url, headers={"X-Orbit-Trigger-Token": tok}).status_code == 401


def test_retry_with_backoff_and_notifications(client, engineer, as_admin, receiver, smtp):  # noqa: F811
    receiver.status = 503
    b = bronze(client, engineer, "outbox", "id\n1\n")
    p = pipeline(client, engineer, chain(node("in", "dataset_input", dataset_id=b["id"]), node("r", "rest_output", url=f"{receiver.url}/x")))
    client.post(f"/api/v1/pipelines/{p['id']}/publish", headers=engineer)
    hook = Receiver()
    auto(client, engineer, p["id"], retry={"max_retries": 2, "backoff_seconds": 10, "backoff_factor": 3},
         notifications={"on": ["failure", "retry"], "in_app": True, "roles": ["admin"], "emails": ["ops@example.com"],
                        "webhook_url": f"{hook.url}/hook"})
    first = run(client, engineer, p["id"], ok=False)
    st = client.get(f"/api/v1/pipelines/{p['id']}/automation", headers=engineer).json()
    due = datetime.fromisoformat(st["retry"]["due_at"])
    assert first["status"] == "failed" and st["retry"]["attempt"] == 2 and 5 <= (due - utcnow()).total_seconds() <= 11
    assert tick(due - timedelta(seconds=2))["retry"] == 0
    assert tick(due + timedelta(seconds=1))["retry"] == 1
    st = client.get(f"/api/v1/pipelines/{p['id']}/automation", headers=engineer).json()
    due2 = datetime.fromisoformat(st["retry"]["due_at"])
    assert 24 <= (due2 - utcnow()).total_seconds() <= 31  # 10 s × 3
    tick(due2 + timedelta(seconds=1))
    r = runs_of(client, engineer, p["id"])
    assert [x["attempt"] for x in r] == [1, 2, 3] and [x["trigger"] for x in r] == ["manual", "retry", "retry"]
    assert client.get(f"/api/v1/pipelines/{p['id']}/automation", headers=engineer).json()["retry"] is None  # no 4th attempt
    notes = client.get("/api/v1/notifications", headers=as_admin).json()
    titles = [n["title"] for n in notes["items"]]
    assert sum("retry" in t for t in titles) == 2 and sum(t.endswith("failed") for t in titles) == 1
    assert any(m["Subject"].endswith("failed") for m in smtp) and any("failed" in h["json"]["text"] for h in hook.requests)
    assert client.post("/api/v1/notifications/read-all", headers=as_admin).json()["marked"] == notes["unread"]
    assert client.get("/api/v1/notifications?unread=true", headers=as_admin).json()["unread"] == 0
    hook.server.shutdown()

    receiver.status = 201
    second = run(client, engineer, p["id"])
    assert second["status"] == "succeeded"


def test_time_limit(client, engineer, monkeypatch):
    import app.services.pipelines as pl

    p, _ = simple(client, engineer)
    auto(client, engineer, p["id"], timeout_minutes=1)
    start = utcnow()
    calls = {"n": 0}

    def clock():
        calls["n"] += 1
        return start + timedelta(seconds=40 * calls["n"])

    monkeypatch.setattr(pl, "utcnow", clock)
    d = run(client, engineer, p["id"], ok=False)
    assert d["error_class"] == "run_timeout" and "1 min time limit" in d["error_message"]


def test_sla_alert(client, engineer, as_admin):
    p, _ = simple(client, engineer)
    run(client, engineer, p["id"])
    auto(client, engineer, p["id"], sla={"enabled": True, "max_age_hours": 1}, notifications={"on": ["sla"], "roles": ["admin"]})
    tick()
    assert client.get("/api/v1/notifications", headers=as_admin).json()["unread"] == 0
    tick(utcnow() + timedelta(hours=2))
    tick(utcnow() + timedelta(hours=3))  # alerted once per update
    notes = client.get("/api/v1/notifications", headers=as_admin).json()["items"]
    assert [n["title"] for n in notes].count(f"SLA missed · {p['name']}") == 1 and "more than 1 h ago" in notes[0]["message"]


def test_backfill_queue_and_calendar(client, engineer):
    b = bronze(client, engineer, "daily_src", "id\n1\n2\n")
    g = chain(node("in", "dataset_input", dataset_id=b["id"]),
              node("d", "derived_column", columns=[{"name": "day", "expression": "{{run_date}}"}, {"name": "until", "expression": "{{end_date}}"}]),
              node("o", "output", layer="silver", name="daily_snapshots", mode="append"))
    p = pipeline(client, engineer, cleaned(g, ["id"]))
    client.post(f"/api/v1/pipelines/{p['id']}/publish", headers=engineer)
    r = client.post(f"/api/v1/pipelines/{p['id']}/backfill", headers=engineer, json={"start": "2026-01-01", "end": "2026-01-10", "step": "week"})
    assert r.status_code == 202, r.text
    rs = runs_of(client, engineer, p["id"])
    assert [x["params"]["run_date"] for x in rs] == ["2026-01-01", "2026-01-08"] and all(x["trigger"] == "backfill" for x in rs)
    assert rs[1]["params"]["end_date"] == "2026-01-10"  # the last period is cut at the end date
    out, _ = rows(client, engineer, "silver", "daily_snapshots")
    assert sorted({str(r["day"])[:10] for r in out}) == ["2026-01-01", "2026-01-08"] and len(out) == 4
    st = client.get(f"/api/v1/pipelines/{p['id']}/automation", headers=engineer).json()
    assert st["backfill"] is None and any("Backfill finished: 2 succeeded" in e["message"] for e in st["events"])
    assert client.post(f"/api/v1/pipelines/{p['id']}/backfill", headers=engineer,
                       json={"start": "2026-01-01", "end": "2027-02-01", "step": "day"}).status_code == 422  # > 366 runs

    sched = {"enabled": True, "kind": "daily", "time": "06:00"}
    auto(client, engineer, p["id"], schedule=sched)
    start, end = T(f"{(utcnow() - timedelta(days=1)).date()}T00:00:00"), T(f"{(utcnow() + timedelta(days=3)).date()}T00:00:00")
    cal = client.get("/api/v1/pipelines/calendar", params={"start": start.isoformat(), "end": end.isoformat()}, headers=engineer).json()
    mine = [u for u in cal["upcoming"] if u["pipeline_id"] == p["id"]]
    assert {x["trigger"] for x in cal["runs"]} >= {"backfill"} and len(mine) == len(A.upcoming(sched, utcnow(), end)) >= 2
    assert all(u["at"].endswith("06:00:00+00:00") or u["at"].endswith("06:00:00Z") for u in mine)


def test_queue_when_a_run_is_active(client, engineer):
    p, _ = simple(client, engineer)
    auto(client, engineer, p["id"], conditions={"if_running": "queue"})
    with session_scope() as db:
        pp = db.get(Pipeline, p["id"])
        busy = PipelineRun(pipeline_id=pp.id, pipeline_version=1, graph_snapshot=pp.graph, status="running", node_status={})
        db.add(busy)
        db.flush()
        busy_id = busy.id
        assert A.trigger(db, pp, "schedule") is None
        assert (pp.state or {})["pending"]["trigger"] == "schedule"
    with session_scope() as db:
        db.get(PipelineRun, busy_id).status = "succeeded"
    tick()
    rs = runs_of(client, engineer, p["id"])
    assert [x["trigger"] for x in rs if x["id"] != busy_id] == ["schedule"]
    assert client.get(f"/api/v1/pipelines/{p['id']}/automation", headers=engineer).json()["pending"] is None


def test_unpublished_pipelines_are_not_started(client, engineer):
    b = bronze(client, engineer, "draft_src", "id\n1\n")
    p = pipeline(client, engineer, cleaned(chain(node("in", "dataset_input", dataset_id=b["id"]), node("o", "output", layer="silver", name="d")), ["id"]))
    auto(client, engineer, p["id"], schedule={"enabled": True, "kind": "interval", "every_minutes": 5})
    tick(utcnow() + timedelta(minutes=6))
    assert runs_of(client, engineer, p["id"]) == []
    assert any("never published" in e["message"] for e in client.get(f"/api/v1/pipelines/{p['id']}/automation", headers=engineer).json()["events"])
