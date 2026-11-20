"""Test setup: throwaway SQLite database + local-folder object storage (no Docker needed)."""
import os
import tempfile
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="orbit_s3_tests_"))
os.environ.update({
    "ORBIT_ENV": "test",
    "ORBIT_DATABASE_URL": f"sqlite:///{(_TMP / 'test.db').as_posix()}",
    "ORBIT_STORAGE_BACKEND": "local",
    "ORBIT_DATA_DIR": str(_TMP / "runtime"),
    "ORBIT_JWT_SECRET": "test-secret-that-is-long-enough-for-hs256-0123456789",
    "ORBIT_DEMO_PASSWORD": "demo-pass-123",
    "ORBIT_LOG_LEVEL": "WARNING",
    "ORBIT_TASKS_EAGER": "true",  # pipeline runs execute inside the test process (no Redis / worker needed)
})

from fastapi.testclient import TestClient  # noqa: E402

from app.core.ratelimit import login_throttle  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.seed import seed  # noqa: E402
from app.db.session import get_engine, session_scope  # noqa: E402
from app.main import app  # noqa: E402

PASSWORD = "demo-pass-123"


@pytest.fixture(autouse=True)
def fresh_db():
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        seed(s)
    login_throttle.reset()
    yield


@pytest.fixture
def client():
    return TestClient(app)


def token_for(client: TestClient, email: str, password: str = PASSWORD) -> str:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture
def as_admin(client):
    return {"Authorization": f"Bearer {token_for(client, 'admin@orbit.local')}"}


@pytest.fixture
def as_engineer(client):
    return {"Authorization": f"Bearer {token_for(client, 'engineer@orbit.local')}"}


@pytest.fixture
def as_analyst(client):
    return {"Authorization": f"Bearer {token_for(client, 'analyst@orbit.local')}"}


@pytest.fixture
def engineer(as_engineer):
    return as_engineer


@pytest.fixture
def analyst(as_analyst):
    return as_analyst


@pytest.fixture
def db():
    with session_scope() as s:
        yield s


def bronze(client, headers, name: str, csv: str) -> dict:
    """Upload a CSV and load it into bronze.<name>; returns the dataset."""
    imp = client.post("/api/v1/imports", headers=headers, files={"file": (f"{name}.csv", csv.encode())}).json()
    assert imp["status"] == "discovered", imp
    r = client.post(f"/api/v1/imports/{imp['id']}/ingest", headers=headers, json={"dataset_name": name})
    assert r.status_code == 201, r.text
    return r.json()["dataset"]


def cleaned(graph: dict, keys) -> dict:
    """Insert the Deduplicate + Data quality steps required before each Silver output (rule R2).

    keys: the key columns, or {output node id: key columns} when the outputs differ."""
    for out in [n for n in graph["nodes"] if n["type"] == "output" and n["config"].get("layer") == "silver"]:
        k = keys[out["id"]] if isinstance(keys, dict) else keys
        d, q = f"{out['id']}__dedupe", f"{out['id']}__quality"
        graph["nodes"] += [{"id": d, "type": "dedupe", "config": {"keys": k}},
                           {"id": q, "type": "quality_check", "config": {"rules": [{"type": "not_null", "column": k[0], "on_failure": "fail"}]}}]
        for e in graph["edges"]:
            if e["target"] == out["id"]:
                e["target"] = d
        graph["edges"] += [{"source": d, "target": q}, {"source": q, "target": out["id"]}]
    return graph


def run_pipeline(client, h, name: str, graph: dict) -> dict:
    p = client.post("/api/v1/pipelines", headers=h, json={"name": name, "graph": graph})
    assert p.status_code == 201, p.text
    run = client.post(f"/api/v1/pipelines/{p.json()['id']}/run", headers=h)
    assert run.status_code == 202, run.text
    assert run.json()["status"] == "succeeded", run.json()
    return run.json()


# ── a deterministic sales dataset (Bronze) and a Gold revenue table built by a pipeline ──
REGIONS = ["Europe", "North America", "APAC", "LATAM"]
SEGMENTS = {"Enterprise": 1.0, "Mid-Market": 0.85, "SMB": 0.7}  # price factor per segment
CATEGORIES = {"Software": 120.0, "Services": 300.0, "Hardware": 75.0}  # unit price per category


def sales_csv(n: int = 400) -> str:
    """amount = quantity × unit_price × segment factor (region has no effect) — so models have something real to learn."""
    import datetime as dt
    import random

    rng = random.Random(42)
    start = dt.date.today().replace(day=1) - dt.timedelta(days=150)
    lines = ["order_id,order_date,region,segment,category,quantity,unit_price,amount"]
    for i in range(1, n + 1):
        seg = rng.choice(list(SEGMENTS))
        cat = rng.choice(list(CATEGORIES))
        q = rng.randint(1, 10)
        price = CATEGORIES[cat] * rng.uniform(0.9, 1.1)
        day = start + dt.timedelta(days=rng.randint(0, 175))
        lines.append(f"{i},{day.isoformat()},{rng.choice(REGIONS)},{seg},{cat},{q},{price:.2f},{q * price * SEGMENTS[seg]:.2f}")
    return "\n".join(lines) + "\n"


def build_sales(client, h) -> dict:
    """bronze.sales → silver.sales_clean → gold.revenue_by_region_month (region, month, revenue, orders)."""
    b = bronze(client, h, "sales", sales_csv())
    run_pipeline(client, h, "sales silver", cleaned({"nodes": [
        {"id": "in", "type": "dataset_input", "config": {"dataset_id": b["id"]}},
        {"id": "out", "type": "output", "config": {"layer": "silver", "name": "sales_clean"}}],
        "edges": [{"source": "in", "target": "out"}]}, ["order_id"]))
    silver = next(d for d in client.get("/api/v1/datasets?layer=silver", headers=h).json() if d["name"] == "sales_clean")
    run_pipeline(client, h, "sales gold", {"nodes": [
        {"id": "in", "type": "dataset_input", "config": {"dataset_id": silver["id"]}},
        {"id": "agg", "type": "sql", "config": {"query": "SELECT region, date_trunc('month', order_date)::DATE AS month, "
                                                         "round(sum(amount), 2) AS revenue, count(*) AS orders FROM input GROUP BY 1, 2"}},
        {"id": "out", "type": "output", "config": {"layer": "gold", "name": "revenue_by_region_month"}}],
        "edges": [{"source": "in", "target": "agg"}, {"source": "agg", "target": "out"}]})
    gold = next(d for d in client.get("/api/v1/datasets?layer=gold", headers=h).json() if d["name"] == "revenue_by_region_month")
    return {"bronze": b, "silver": silver, "gold": gold}


@pytest.fixture
def sales(client, as_engineer):
    return build_sales(client, as_engineer)
