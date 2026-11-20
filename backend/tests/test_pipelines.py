"""Visual pipelines executed by DuckDB: Bronze → Silver → Gold, quality gates, run tracking."""
import io

import pyarrow.parquet as pq

from app.lakehouse.storage import get_storage
from tests.conftest import bronze

ORDERS = (
    "order_id,client,amount,status\n"
    "1,Alice,10.5,paid\n"
    "2,Bob,20,paid\n"
    "2,Bob,20,paid\n"        # duplicate order
    "3,,7,paid\n"            # missing client
    "4,Alice,-3,refunded\n"  # negative amount
    "5,Chloe,30,paid\n"
)


def graph(ds_id: str) -> dict:
    """input → filter → dedupe → quality check → silver output"""
    return {
        "nodes": [
            {"id": "in", "type": "dataset_input", "config": {"dataset_id": ds_id}},
            {"id": "f", "type": "filter", "config": {"condition": "amount > 0"}},
            {"id": "d", "type": "dedupe", "config": {"keys": ["order_id"]}},
            {"id": "q", "type": "quality_check", "config": {"rules": [
                {"type": "not_null", "column": "client", "on_failure": "quarantine"}]}},
            {"id": "s", "type": "output", "config": {"layer": "silver", "name": "orders_clean"}},
        ],
        "edges": [{"source": "in", "target": "f"}, {"source": "f", "target": "d"}, {"source": "d", "target": "q"},
                  {"source": "q", "target": "s"}],
    }


def gold_graph(silver_id: str) -> dict:
    """silver input → aggregate → gold output"""
    return {
        "nodes": [
            {"id": "in", "type": "dataset_input", "config": {"dataset_id": silver_id}},
            {"id": "a", "type": "aggregate", "config": {"group_by": ["client"], "aggregations": [
                {"func": "sum", "column": "amount", "alias": "revenue"}, {"func": "count", "alias": "orders"}]}},
            {"id": "g", "type": "output", "config": {"layer": "gold", "name": "revenue_by_client"}},
        ],
        "edges": [{"source": "in", "target": "a"}, {"source": "a", "target": "g"}],
    }


def make(client, h, ds_id, name="orders etl"):
    r = client.post("/api/v1/pipelines", headers=h, json={"name": name, "graph": graph(ds_id)})
    assert r.status_code == 201, r.text
    return r.json()


def make_gold(client, h):
    """Gold pipeline reading the Silver dataset written by `make`."""
    silver = next(d for d in client.get("/api/v1/datasets?layer=silver", headers=h).json() if d["name"] == "orders_clean")
    r = client.post("/api/v1/pipelines", headers=h, json={"name": "orders gold", "graph": gold_graph(silver["id"])})
    assert r.status_code == 201, r.text
    return r.json()


def test_validate_and_preview(client, as_engineer):
    ds = bronze(client, as_engineer, "orders", ORDERS)
    v = client.post("/api/v1/pipelines/validate", headers=as_engineer, json=graph(ds["id"])).json()
    assert v["valid"], v["issues"]
    client.post(f"/api/v1/pipelines/{make(client, as_engineer, ds['id'])['id']}/run", headers=as_engineer)
    gold = gold_graph(next(d for d in client.get("/api/v1/datasets?layer=silver", headers=as_engineer).json())["id"])
    v = client.post("/api/v1/pipelines/validate", headers=as_engineer, json=gold).json()
    assert v["valid"], v["issues"]
    assert [c["name"] for c in v["node_schemas"]["a"]] == ["client", "revenue", "orders"]

    p = client.post("/api/v1/pipelines/preview", headers=as_engineer,
                    json={"graph": graph(ds["id"]), "node_id": "d"}).json()
    assert p["rows_in_sample"] == 4  # 6 rows − 1 negative − 1 duplicate
    assert {r["order_id"] for r in p["rows"]} == {1, 2, 3, 5}

    bad = graph(ds["id"])
    bad["nodes"][1]["config"]["condition"] = "no_such_column > 1"
    v = client.post("/api/v1/pipelines/validate", headers=as_engineer, json=bad).json()
    assert not v["valid"] and v["issues"][0]["node_id"] == "f"

    cyc = graph(ds["id"])
    cyc["edges"].append({"source": "d", "target": "f"})
    v = client.post("/api/v1/pipelines/validate", headers=as_engineer, json=cyc).json()
    assert any("cycle" in i["message"] for i in v["issues"])


def test_end_to_end_run_writes_silver_and_gold(client, as_engineer, as_analyst):
    ds = bronze(client, as_engineer, "orders", ORDERS)
    p = make(client, as_engineer, ds["id"])
    r = client.post(f"/api/v1/pipelines/{p['id']}/run", headers=as_engineer)
    assert r.status_code == 202, r.text
    run = client.get(f"/api/v1/runs/{r.json()['id']}", headers=as_analyst).json()
    assert run["status"] == "succeeded", run["error_message"]
    assert run["pipeline_version"] == 1 and run["records_in"] == 6
    assert all(s["status"] == "succeeded" for s in run["node_status"].values())
    assert run["node_status"]["q"]["message"] == "0/1 assertions passed, 1 row(s) quarantined"
    assert {o["qualified_name"]: o["rows"] for o in run["outputs"]} == {"silver.orders_clean": 3}
    assert any("quarantined" in line["msg"] for line in run["logs"])
    g = make_gold(client, as_engineer)
    r = client.post(f"/api/v1/pipelines/{g['id']}/run", headers=as_engineer)
    gold_run = client.get(f"/api/v1/runs/{r.json()['id']}", headers=as_analyst).json()
    assert gold_run["status"] == "succeeded", gold_run["error_message"]
    assert {o["qualified_name"]: o["rows"] for o in gold_run["outputs"]} == {"gold.revenue_by_client": 3}

    gold = next(d for d in client.get("/api/v1/datasets?layer=gold", headers=as_analyst).json())
    detail = client.get(f"/api/v1/datasets/{gold['id']}", headers=as_analyst).json()
    v1 = detail["versions"][0]
    assert v1["pipeline_run_id"] == gold_run["id"]
    t = pq.read_table(io.BytesIO(get_storage().get_bytes(v1["location"]))).to_pylist()
    assert sorted((x["client"], x["revenue"], x["orders"]) for x in t) == [("Alice", 10.5, 1), ("Bob", 20.0, 1), ("Chloe", 30.0, 1)]

    # pipeline status and run listing
    assert client.get(f"/api/v1/pipelines/{p['id']}", headers=as_analyst).json()["last_run_status"] == "succeeded"
    runs = client.get(f"/api/v1/runs?status=succeeded&pipeline_id={p['id']}", headers=as_analyst).json()
    assert runs["total"] == 1 and runs["items"][0]["pipeline_name"] == "orders etl"

    # a second run writes version 2 of each output
    client.post(f"/api/v1/pipelines/{p['id']}/run", headers=as_engineer)
    client.post(f"/api/v1/pipelines/{g['id']}/run", headers=as_engineer)
    assert client.get(f"/api/v1/datasets/{detail['id']}", headers=as_analyst).json()["current_version"] == 2
    silver = next(d for d in client.get("/api/v1/datasets?layer=silver", headers=as_analyst).json() if d["name"] == "orders_clean")
    assert silver["current_version"] == 2
    assert client.get(f"/api/v1/datasets/{gold['id']}", headers=as_analyst).json()["current_version"] == 2


def test_quality_rule_blocks_the_write_then_retry(client, as_engineer):
    ds = bronze(client, as_engineer, "orders", ORDERS)
    p = make(client, as_engineer, ds["id"])
    client.post(f"/api/v1/pipelines/{p['id']}/run", headers=as_engineer)
    p = make_gold(client, as_engineer)
    client.post(f"/api/v1/pipelines/{p['id']}/run", headers=as_engineer)
    gold = client.get("/api/v1/datasets?layer=gold", headers=as_engineer).json()[0]
    rule = client.post("/api/v1/quality/rules", headers=as_engineer, json={
        "dataset_id": gold["id"], "rule_type": "range", "column": "revenue", "params": {"min": 25},
        "on_failure": "fail", "severity": "high"}).json()

    run = client.post(f"/api/v1/pipelines/{p['id']}/run", headers=as_engineer).json()
    assert run["status"] == "failed" and run["error_class"] == "quality_gate_failed"
    assert "Nothing was written" in run["error_message"]
    assert client.get(f"/api/v1/datasets/{gold['id']}", headers=as_engineer).json()["current_version"] == 1  # untouched
    issues = client.get("/api/v1/quality/issues?status=open", headers=as_engineer).json()["items"]
    assert len(issues) == 1 and issues[0]["severity"] == "high" and issues[0]["failed_count"] == 2

    # relax the rule, retry the failed run (same pipeline version) → succeeds, anomaly auto-resolved
    client.patch(f"/api/v1/quality/rules/{rule['id']}", headers=as_engineer, json={"params": {"min": 0}})
    retry = client.post(f"/api/v1/runs/{run['id']}/retry", headers=as_engineer).json()
    assert retry["status"] == "succeeded" and retry["attempt"] == 2 and retry["retry_of_id"] == run["id"]
    assert client.get(f"/api/v1/datasets/{gold['id']}", headers=as_engineer).json()["current_version"] == 2
    assert client.get("/api/v1/quality/issues?status=resolved", headers=as_engineer).json()["total"] == 1
    # only failed / cancelled runs can be retried, finished runs cannot be cancelled
    assert client.post(f"/api/v1/runs/{retry['id']}/retry", headers=as_engineer).status_code == 409
    assert client.post(f"/api/v1/runs/{retry['id']}/cancel", headers=as_engineer).status_code == 409


def test_publish_versions_and_drafts(client, as_engineer):
    ds = bronze(client, as_engineer, "orders", ORDERS)
    p = make(client, as_engineer, ds["id"])
    assert p["status"] == "draft" and p["current_version"] == 0
    assert client.post(f"/api/v1/pipelines/{p['id']}/publish", headers=as_engineer).json()["version"] == 1
    # moving a node is not a logic change: same version
    g = graph(ds["id"])
    g["nodes"][0]["position"] = {"x": 500, "y": 80}
    assert client.put(f"/api/v1/pipelines/{p['id']}", headers=as_engineer, json={"graph": g}).json()["status"] == "published"
    # changing the filter is: draft, then version 2
    g["nodes"][1]["config"]["condition"] = "amount > 10"
    assert client.put(f"/api/v1/pipelines/{p['id']}", headers=as_engineer, json={"graph": g}).json()["status"] == "draft"
    assert client.post(f"/api/v1/pipelines/{p['id']}/publish", headers=as_engineer).json()["version"] == 2
    assert [v["version"] for v in client.get(f"/api/v1/pipelines/{p['id']}/versions", headers=as_engineer).json()] == [2, 1]
    # an invalid draft cannot be published
    g["nodes"][1]["config"]["condition"] = "amount >"
    client.put(f"/api/v1/pipelines/{p['id']}", headers=as_engineer, json={"graph": g})
    r = client.post(f"/api/v1/pipelines/{p['id']}/publish", headers=as_engineer)
    assert r.status_code == 422 and r.json()["error"]["details"][0]["node_id"] == "f"


def test_sql_node_is_sandboxed(client, as_engineer):
    ds = bronze(client, as_engineer, "orders", ORDERS)

    def sql(query):
        g = {"nodes": [{"id": "in", "type": "dataset_input", "config": {"dataset_id": ds["id"]}},
                       {"id": "s", "type": "sql", "config": {"query": query}},
                       {"id": "d", "type": "dedupe", "config": {"keys": ["client"]}},
                       {"id": "q", "type": "quality_check", "config": {"rules": [{"type": "not_null", "column": "client", "on_failure": "warn"}]}},
                       {"id": "o", "type": "output", "config": {"layer": "silver", "name": "x"}}],
             "edges": [{"source": "in", "target": "s"}, {"source": "s", "target": "d"}, {"source": "d", "target": "q"}, {"source": "q", "target": "o"}]}
        return client.post("/api/v1/pipelines/validate", headers=as_engineer, json=g).json()

    assert sql("SELECT client, count(*) AS n FROM input GROUP BY client")["valid"]
    assert "Table functions" in sql("SELECT * FROM read_csv('/etc/passwd')")["issues"][0]["message"]
    assert "Unknown table" in sql("SELECT * FROM users")["issues"][0]["message"]
    assert "single SELECT" in sql("DROP TABLE input")["issues"][0]["message"]


def test_failed_step_is_reported_on_the_node(client, as_engineer):
    ds = bronze(client, as_engineer, "orders", ORDERS)
    g = {"nodes": [{"id": "in", "type": "dataset_input", "config": {"dataset_id": ds["id"]}},
                   {"id": "q", "type": "quality_check", "config": {"rules": [
                       {"type": "unique", "column": "order_id", "on_failure": "fail"}]}},
                   {"id": "d", "type": "dedupe", "config": {"keys": ["order_id"]}},
                   {"id": "o", "type": "output", "config": {"layer": "silver", "name": "orders_unique"}}],
         "edges": [{"source": "in", "target": "q"}, {"source": "q", "target": "d"}, {"source": "d", "target": "o"}]}
    p = client.post("/api/v1/pipelines", headers=as_engineer, json={"name": "strict", "graph": g}).json()
    run = client.post(f"/api/v1/pipelines/{p['id']}/run", headers=as_engineer).json()
    detail = client.get(f"/api/v1/runs/{run['id']}", headers=as_engineer).json()
    assert detail["status"] == "failed"
    assert detail["node_status"]["q"]["status"] == "failed" and detail["node_status"]["o"]["status"] == "skipped"
    assert client.get("/api/v1/datasets?layer=silver", headers=as_engineer).json() == []


def test_roles(client, as_engineer, as_analyst):
    ds = bronze(client, as_engineer, "orders", ORDERS)
    assert client.post("/api/v1/pipelines", headers=as_analyst, json={"name": "x"}).status_code == 403
    p = make(client, as_engineer, ds["id"])
    assert client.post(f"/api/v1/pipelines/{p['id']}/run", headers=as_analyst).status_code == 403
    assert client.get(f"/api/v1/pipelines/{p['id']}", headers=as_analyst).status_code == 200
    assert client.get("/api/v1/runs", headers=as_analyst).status_code == 200


def test_dashboard(client, as_engineer, as_analyst):
    ds = bronze(client, as_engineer, "orders", ORDERS)
    p = make(client, as_engineer, ds["id"])
    client.post(f"/api/v1/pipelines/{p['id']}/run", headers=as_engineer)
    client.post(f"/api/v1/pipelines/{make_gold(client, as_engineer)['id']}/run", headers=as_engineer)
    d = client.get("/api/v1/dashboard", headers=as_analyst).json()
    assert {k: v["datasets"] for k, v in d["layers"].items()} == {"bronze": 1, "silver": 1, "gold": 1}
    assert d["runs"]["counts"]["succeeded"] == 2 and d["runs"]["success_rate"] == 100.0
    assert d["pipelines"] == {"total": 2, "published": 2, "failing": 0}
    assert sum(day["succeeded"] for day in d["daily"]) == 2 and len(d["daily"]) == 14
    assert d["recent_runs"][0]["status"] == "succeeded"
