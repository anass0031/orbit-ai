"""New pipeline tools — sources, transformations and quality nodes (no external system needed)."""
import io
import uuid

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from tests.conftest import bronze, cleaned


def node(nid, type_, **config):
    return {"id": nid, "type": type_, "config": config}


def chain(*nodes, extra_edges=()):
    """Linear graph n0 → n1 → … (+ extra edges given as (source, target) or (source, target, handle))."""
    edges = [{"source": a["id"], "target": b["id"]} for a, b in zip(nodes, nodes[1:], strict=False)]
    for e in extra_edges:
        edges.append({"source": e[0], "target": e[1], **({"source_handle": e[2]} if len(e) > 2 else {})})
    return {"nodes": list(nodes), "edges": edges}


def pipeline(client, h, graph, name=None):
    r = client.post("/api/v1/pipelines", headers=h, json={"name": name or f"p {uuid.uuid4().hex[:6]}", "graph": graph})
    assert r.status_code == 201, r.text
    return r.json()


def run(client, h, pid, params=None, ok=True):
    r = client.post(f"/api/v1/pipelines/{pid}/run", headers=h, json={"params": params or {}})
    assert r.status_code == 202, r.text
    detail = client.get(f"/api/v1/runs/{r.json()['id']}", headers=h).json()
    if ok:
        assert detail["status"] == "succeeded", (detail["error_message"], detail["logs"][-4:])
    return detail


def rows(client, h, layer, name, limit=500):
    ds = next(d for d in client.get(f"/api/v1/datasets?layer={layer}", headers=h).json() if d["name"] == name)
    return client.get(f"/api/v1/datasets/{ds['id']}/preview?limit={limit}", headers=h).json()["rows"], ds


def validate(client, h, graph):
    return client.post("/api/v1/pipelines/validate", headers=h, json=graph).json()


MESSY = """id,name,city,amount,signup,segment
1,"  alice   MARTIN ",paris,"1 234,50",05/01/2024,ENT
2,bob durand,PARIS,"12,5",2024-02-10,SMB
3,  claire ,lyon,7.25,15.03.2024,
4,dan,,abc,not a date,MID
"""


def test_catalogue_lists_every_tool(client, engineer):
    types = {t["type"]: t["category"] for t in client.get("/api/v1/pipelines/node-types", headers=engineer).json()}
    expected = {"incremental_input", "file_drop_input", "date_dimension", "subpipeline", "clean", "fill_missing",
                "derived_column", "split_column", "merge_columns", "lookup", "sort_limit", "pivot", "unpivot", "window", "scd2",
                "python", "schema_check", "data_compare", "anomaly_detection", "branch", "export_file", "rest_output", "email_report"}
    assert expected <= set(types) and types["branch"] == "QUALITY"
    assert not {"source_input", "database_output", "kafka_output", "ml_scoring", "ml_train", "ai_summary", "saved_query_refresh"} & set(types)


def test_cleaning_chain(client, engineer):
    b = bronze(client, engineer, "customers_raw", MESSY)
    g = chain(node("in", "dataset_input", dataset_id=b["id"]),
              node("c", "clean", columns=["name", "city", "segment"], case="title", number_columns=["amount"], date_columns=["signup"]),
              node("f", "fill_missing", rules=[{"column": "city", "strategy": "value", "value": "Unknown"},
                                               {"column": "amount", "strategy": "median"}]),
              node("d", "derived_column", columns=[{"name": "amount_x2", "expression": "amount * 2"},
                                                   {"name": "big", "expression": "amount_x2 > 1000"}]),
              node("s", "split_column", column="name", delimiter=" ", into=["first_name", "last_name"]),
              node("m", "merge_columns", columns=["city", "segment"], separator=" / ", into="place"),
              node("l", "lookup", column="segment", into="segment_label", mapping={"Ent": "Enterprise", "Smb": "Small business"}),
              node("o", "output", layer="silver", name="customers_clean"))
    g = cleaned(g, ["id"])
    v = validate(client, engineer, g)
    assert v["valid"], v["issues"]
    run(client, engineer, pipeline(client, engineer, g)["id"])
    out, _ = rows(client, engineer, "silver", "customers_clean")
    by = {r["id"]: r for r in out}
    assert by[1]["name"] == "Alice Martin" and by[1]["first_name"] == "Alice" and by[1]["last_name"] == "Martin"
    assert by[1]["amount"] == 1234.5 and str(by[1]["signup"])[:10] == "2024-01-05" and by[1]["big"] is True
    assert by[2]["city"] == "Paris" and by[2]["amount"] == 12.5 and by[2]["segment_label"] == "Small business"
    assert str(by[3]["signup"])[:10] == "2024-03-15" and by[3]["place"] == "Lyon"  # empty segment skipped in the merge
    assert by[4]["city"] == "Unknown" and by[4]["amount"] == 12.5 and by[4]["signup"] is None  # median of 1234.5, 12.5, 7.25
    assert by[4]["segment_label"] == "Mid" and by[1]["segment_label"] == "Enterprise"  # unmatched codes keep their value

    # reference table as 2nd input, numeric code: unmatched codes get an empty label
    ref = bronze(client, engineer, "regions_ref", "rid,label\n1,North\n2,South\n")
    g2 = {"nodes": [node("in", "dataset_input", dataset_id=b["id"]), node("r", "dataset_input", dataset_id=ref["id"]),
                    node("l", "lookup", column="id", into="zone", ref_key="rid", ref_value="label"),
                    node("o", "output", layer="silver", name="customers_zone")],
          "edges": [{"source": "in", "target": "l"}, {"source": "r", "target": "l"}, {"source": "l", "target": "o"}]}
    run(client, engineer, pipeline(client, engineer, cleaned(g2, ["id"]))["id"])
    zones, _ = rows(client, engineer, "silver", "customers_zone")
    assert {r["id"]: r["zone"] for r in zones} == {1: "North", 2: "South", 3: None, 4: None}


def test_window_sort_pivot_unpivot(client, engineer, sales):
    g = sales["gold"]
    graph = chain(node("in", "dataset_input", dataset_id=g["id"]),
                  node("w", "window", functions=[
                      {"func": "running_sum", "column": "revenue", "partition_by": ["region"], "order_by": "month", "alias": "cum"},
                      {"func": "rank", "partition_by": ["month"], "order_by": "revenue", "desc": True, "alias": "rank_in_month"},
                      {"func": "lag", "column": "revenue", "partition_by": ["region"], "order_by": "month", "alias": "prev"},
                      {"func": "percent_of_total", "column": "revenue", "partition_by": ["month"], "alias": "share"}]),
                  node("t", "sort_limit", order_by=[{"column": "revenue", "desc": True}], group_by=["month"], limit=1),
                  node("o", "output", layer="gold", name="best_region_per_month"))
    run(client, engineer, pipeline(client, engineer, graph)["id"])
    best, _ = rows(client, engineer, "gold", "best_region_per_month")
    months, _ = rows(client, engineer, "gold", "revenue_by_region_month")
    assert len(best) == len({r["month"] for r in months}) and all(r["rank_in_month"] == 1 for r in best)
    for r in best:
        same = [x for x in months if x["month"] == r["month"]]
        assert r["revenue"] == max(x["revenue"] for x in same)
        assert abs(r["share"] - 100 * r["revenue"] / sum(x["revenue"] for x in same)) < 1e-6

    pv = chain(node("in", "dataset_input", dataset_id=g["id"]),
               node("p", "pivot", index=["region"], column="month", value="revenue", agg="sum"),
               node("u", "unpivot", columns=[], name_column="month", value_column="revenue"),
               node("o", "output", layer="gold", name="unused"))
    assert not validate(client, engineer, pv)["valid"]  # unpivot needs its columns
    pv["nodes"][2]["config"]["columns"] = sorted({str(r["month"])[:10] for r in months})
    v = validate(client, engineer, pv)
    assert v["valid"] and any("Checked at run time" in i["message"] for i in v["issues"])  # pivot columns come from the data
    pv["nodes"][3]["config"]["name"] = "revenue_long"
    p = pipeline(client, engineer, pv)
    d = run(client, engineer, p["id"])
    assert d["node_status"]["p"]["rows"] == 4 and "new column" in d["node_status"]["p"]["message"]
    long, _ = rows(client, engineer, "gold", "revenue_long")
    assert len(long) == len(months) and abs(sum(r["revenue"] for r in long) - sum(r["revenue"] for r in months)) < 0.01


def test_branch_and_anomalies(client, engineer):
    csv = "id,amount\n" + "\n".join(f"{i},{100 + (i % 7)}" for i in range(1, 60)) + "\n60,99999\n"
    b = bronze(client, engineer, "payments", csv)
    graph = chain(node("in", "dataset_input", dataset_id=b["id"]),
                  node("a", "anomaly_detection", column="amount", method="zscore", threshold=3, action="flag"),
                  node("br", "branch", condition="is_anomaly"))
    graph["nodes"] += [node("o1", "output", layer="silver", name="payments_suspicious"),
                       node("o2", "output", layer="silver", name="payments_ok")]
    graph["edges"] += [{"source": "br", "target": "o1", "source_handle": "true"}, {"source": "br", "target": "o2", "source_handle": "false"}]
    graph = cleaned(graph, ["id"])
    d = run(client, engineer, pipeline(client, engineer, graph)["id"])
    assert "1 anomaly flagged" in d["node_status"]["a"]["message"] and "1 → true · 59 → false" in d["node_status"]["br"]["message"]
    sus, _ = rows(client, engineer, "silver", "payments_suspicious")
    ok, _ = rows(client, engineer, "silver", "payments_ok")
    assert [r["id"] for r in sus] == [60] and len(ok) == 59

    graph["nodes"][1]["config"]["action"] = "fail"
    p = pipeline(client, engineer, graph)
    d = run(client, engineer, p["id"], ok=False)
    assert d["status"] == "failed" and d["error_class"] == "quality_gate_failed" and "99999" in d["error_message"]

    unconnected = chain(node("in", "dataset_input", dataset_id=b["id"]), node("br", "branch", condition="amount > 1"),
                        node("o", "output", layer="silver", name="x"))
    assert any("'true' or 'false' port" in i["message"] for i in validate(client, engineer, unconnected)["issues"])


def test_schema_check_and_row_count_comparison(client, engineer):
    b = bronze(client, engineer, "orders_feed", "id,amount,region\n" + "\n".join(f"{i},{i * 1.5},EU" for i in range(1, 101)) + "\n")
    graph = chain(node("in", "dataset_input", dataset_id=b["id"]),
                  node("s", "schema_check", expected=[{"name": "id", "type": "integer"}, {"name": "amount", "type": "double"}]),
                  node("c", "data_compare", max_drop_pct=50),
                  node("o", "output", layer="silver", name="orders_checked"))
    bad = chain(node("in", "dataset_input", dataset_id=b["id"]), node("s", "schema_check", expected=[{"name": "amount", "type": "string"}]),
                node("o", "output", layer="silver", name="x"))
    assert any("'amount' is double, expected string" in i["message"] for i in validate(client, engineer, bad)["issues"])
    p = pipeline(client, engineer, cleaned(graph, ["id"]))
    first = run(client, engineer, p["id"])
    assert "baseline recorded" in first["node_status"]["c"]["message"]
    bronze(client, engineer, "orders_feed", "id,amount\n" + "\n".join(f"{i},{i}.5" for i in range(1, 21)) + "\n")  # 20 rows, region gone
    second = run(client, engineer, p["id"], ok=False)
    assert second["error_class"] == "quality_gate_failed" and "region' disappeared since the last run" in second["error_message"]

    graph["nodes"][1]["config"]["on_failure"] = "warn"
    client.put(f"/api/v1/pipelines/{p['id']}", headers=engineer, json={"graph": graph})
    third = run(client, engineer, p["id"], ok=False)
    assert third["error_class"] == "quality_gate_failed" and "fell 80.0%" in third["error_message"]  # the row count check still blocks
    graph["nodes"][2]["config"]["on_failure"] = "warn"
    client.put(f"/api/v1/pipelines/{p['id']}", headers=engineer, json={"graph": graph})
    fourth = run(client, engineer, p["id"])
    assert fourth["node_status"]["s"]["message"].startswith("⚠") and fourth["node_status"]["c"]["message"].startswith("⚠")


def test_history_upsert_and_append(client, engineer):
    v1 = "id,name,city\n1,Ana,Paris\n2,Ben,Lyon\n3,Cam,Nice\n"
    b = bronze(client, engineer, "crm_customers", v1)
    graph = {"nodes": [node("in", "dataset_input", dataset_id=b["id"]),
                       node("h", "scd2", keys=["id"], tracked=["city"], target_layer="silver", target_name="customers_history"),
                       node("o1", "output", layer="silver", name="customers_history"),
                       node("o2", "output", layer="silver", name="customers_latest", mode="upsert", keys=["id"]),
                       node("o3", "output", layer="silver", name="customers_log", mode="append")],
             "edges": [{"source": "in", "target": "h"}, {"source": "h", "target": "o1"}, {"source": "in", "target": "o2"},
                       {"source": "in", "target": "o3"}]}
    p = pipeline(client, engineer, cleaned(graph, {"o1": ["id", "valid_from"], "o2": ["id"], "o3": ["id"]}))
    run(client, engineer, p["id"])
    bronze(client, engineer, "crm_customers", "id,name,city\n2,Ben,Marseille\n3,Cam,Nice\n4,Dee,Lille\n")  # 2 moved, 4 new, 1 gone
    d = run(client, engineer, p["id"])
    assert "1 new · 1 changed" in d["node_status"]["h"]["message"]
    hist, _ = rows(client, engineer, "silver", "customers_history")
    ben = sorted((r for r in hist if r["id"] == 2), key=lambda r: r["valid_from"])
    assert [r["city"] for r in ben] == ["Lyon", "Marseille"] and [r["is_current"] for r in ben] == [False, True]
    assert ben[0]["valid_to"] is not None and ben[1]["valid_to"] is None
    assert sum(r["is_current"] for r in hist) == 4 and len(hist) == 5  # Ana stays current (close_missing is off)
    latest, _ = rows(client, engineer, "silver", "customers_latest")
    assert {r["id"]: r["city"] for r in latest} == {1: "Paris", 2: "Marseille", 3: "Nice", 4: "Lille"}
    log, ds = rows(client, engineer, "silver", "customers_log")
    assert len(log) == 6 and ds["current_version"] == 2


def test_incremental_input_and_parameters(client, engineer):
    base = "event_id,ts,value\n" + "\n".join(f"{i},2026-01-{i:02d} 10:00:00,{i}" for i in range(1, 11)) + "\n"
    b = bronze(client, engineer, "events", base)
    graph = chain(node("in", "incremental_input", dataset_id=b["id"], watermark_column="ts"),
                  node("f", "filter", condition="value >= {{min_value}}"),
                  node("o", "output", layer="silver", name="events_inc", mode="append"))
    p = pipeline(client, engineer, cleaned(graph, ["event_id"]))
    assert client.post(f"/api/v1/pipelines/{p['id']}/run", headers=engineer).status_code == 422  # {{min_value}} undeclared
    auto = client.put(f"/api/v1/pipelines/{p['id']}/automation", headers=engineer, json={
        "params": [{"name": "min_value", "type": "integer", "default": 0}], "conditions": {"skip_if_no_new_data": True}})
    assert auto.status_code == 200, auto.text
    first = run(client, engineer, p["id"])
    assert "first run" in first["node_status"]["in"]["message"] and first["params"]["min_value"] == 0
    bronze(client, engineer, "events", base + "11,2026-01-11 10:00:00,11\n12,2026-01-12 10:00:00,12\n13,2026-01-13 09:00:00,1\n")
    second = run(client, engineer, p["id"], params={"min_value": 5})
    assert second["node_status"]["in"]["rows"] == 3 and second["node_status"]["f"]["rows"] == 2
    out, _ = rows(client, engineer, "silver", "events_inc")
    assert len(out) == 12
    third = run(client, engineer, p["id"])
    assert any("nothing written" in line["msg"] for line in third["logs"]) and third["records_out"] == 0
    bad = client.post(f"/api/v1/pipelines/{p['id']}/run", headers=engineer, json={"params": {"min_value": "abc"}})
    assert bad.status_code == 422 and "not a valid integer" in bad.json()["error"]["message"]
    assert client.post(f"/api/v1/pipelines/{p['id']}/run", headers=engineer, json={"params": {"nope": 1}}).status_code == 422


def test_file_drop_input(client, engineer):
    folder = f"drop-{uuid.uuid4().hex[:8]}"
    up = lambda name, data: client.post(f"/api/v1/pipelines/dropzone/{folder}", headers=engineer,  # noqa: E731
                                        files={"file": (name, data)})
    assert up("jan.csv", b"sku,qty\nA,1\nB,2\n").status_code == 201
    buf = io.BytesIO()
    pq.write_table(pa.table({"sku": ["C"], "qty": [3]}), buf)
    assert up("feb.parquet", buf.getvalue()).status_code == 201
    assert [f["name"] for f in client.get(f"/api/v1/pipelines/dropzone/{folder}", headers=engineer).json()] == ["feb.parquet", "jan.csv"]
    graph = chain(node("in", "file_drop_input", folder=folder), node("o", "output", layer="silver", name="stock_moves", mode="append"))
    p = pipeline(client, engineer, cleaned(graph, ["sku"]))
    run(client, engineer, p["id"])
    out, _ = rows(client, engineer, "silver", "stock_moves")
    assert sorted((r["sku"], r["qty"], r["_source_file"]) for r in out) == [("A", 1, "jan.csv"), ("B", 2, "jan.csv"), ("C", 3, "feb.parquet")]
    up("mar.csv", b"sku,qty\nD,4\n")
    d = run(client, engineer, p["id"])
    assert "1 new file(s)" in d["node_status"]["in"]["message"]
    out, _ = rows(client, engineer, "silver", "stock_moves")
    assert len(out) == 4
    assert client.post("/api/v1/pipelines/dropzone/Bad Folder!", headers=engineer, files={"file": ("x.csv", b"a\n1\n")}).status_code == 422


def test_calendar_dimension_and_subpipeline(client, engineer):
    cal = chain(node("d", "date_dimension", start="{{start_date}}", end="{{end_date}}", holidays="fr",
                     custom_holidays=[{"date": "2026-07-15", "name": "Company day"}]),
                node("o", "output", layer="gold", name="calendar"))
    p = pipeline(client, engineer, cal, name="calendar builder")
    run(client, engineer, p["id"], params={"start_date": "2026-07-10", "end_date": "2026-07-19"})
    days, _ = rows(client, engineer, "gold", "calendar")
    by = {str(r["date"])[:10]: r for r in days}
    assert len(days) == 10 and by["2026-07-14"]["holiday_name"] == "Fête nationale" and by["2026-07-15"]["is_holiday"]
    assert by["2026-07-11"]["is_weekend"] and not by["2026-07-13"]["is_weekend"] and by["2026-07-13"]["is_working_day"]
    assert by["2026-07-16"]["is_working_day"] and by["2026-07-14"]["quarter"] == 3 and by["2026-07-14"]["day_name"] == "Tuesday"

    child = chain(node("s", "subpipeline", pipeline_id=p["id"], node_id="d"),
                  node("f", "filter", condition="is_working_day"), node("o", "output", layer="gold", name="working_days"))
    c = pipeline(client, engineer, child)
    run(client, engineer, c["id"], params={"start_date": "2026-07-10", "end_date": "2026-07-19"})
    work, _ = rows(client, engineer, "gold", "working_days")
    assert len(work) == 4  # Fri 10, Thu 16, Fri 17 + Mon 13 (14 holiday, 15 company day, weekends out)

    loop = chain(node("s", "subpipeline", pipeline_id=c["id"], node_id="f"), node("o", "output", layer="gold", name="loop"))
    client.put(f"/api/v1/pipelines/{c['id']}", headers=engineer, json={"graph": loop})
    assert any("cannot include itself" in i["message"] for i in client.post(
        f"/api/v1/pipelines/validate?pipeline_id={c['id']}", headers=engineer, json=loop).json()["issues"])


def test_python_transform(client, engineer):
    b = bronze(client, engineer, "py_in", "a,b\n1,2\n3,4\n")
    code = "import math\n\ndef transform(df):\n    df['c'] = df['a'] * 10 + df['b']\n    df['root'] = df['c'].map(math.sqrt)\n    return df[df['c'] > 20]\n"
    graph = chain(node("in", "dataset_input", dataset_id=b["id"]), node("py", "python", code=code),
                  node("o", "output", layer="silver", name="py_out"))
    graph = cleaned(graph, ["a"])
    assert validate(client, engineer, graph)["valid"]
    run(client, engineer, pipeline(client, engineer, graph)["id"])
    out, _ = rows(client, engineer, "silver", "py_out")
    assert [{k: r[k] for k in ("a", "b", "c", "root")} for r in out] == [{"a": 3, "b": 4, "c": 34, "root": pytest.approx(34 ** 0.5)}]
    for evil, msg in [("import os\ndef transform(df):\n    return df", "'os' is not allowed"),
                      ("def transform(df):\n    open('/etc/passwd')\n    return df", "'open' is not allowed"),
                      ("def transform(df):\n    df.to_csv('x.csv')\n    return df", "'.to_csv' is not allowed"),
                      ("def transform(df):\n    return df.__class__", "'.__class__' is not allowed"),
                      ("def transform(df):\n    return 42", "must return a pandas DataFrame"),
                      ("x = 1", "Define a function named transform")]:
        graph["nodes"][1]["config"]["code"] = evil
        issues = validate(client, engineer, graph)["issues"]
        assert any(msg in i["message"] for i in issues), (evil, issues)


def test_preview_of_new_nodes_uses_parameters(client, engineer, sales):
    g = chain(node("in", "dataset_input", dataset_id=sales["bronze"]["id"]),
              node("f", "filter", condition="region = {{region}}"), node("o", "output", layer="silver", name="eu"))
    p = pipeline(client, engineer, g)
    client.put(f"/api/v1/pipelines/{p['id']}/automation", headers=engineer,
               json={"params": [{"name": "region", "type": "string", "default": "Europe"}]})
    r = client.post("/api/v1/pipelines/preview", headers=engineer, json={"graph": g, "node_id": "f", "pipeline_id": p["id"],
                                                                         "params": {"region": "APAC"}}).json()
    assert r["rows"] and all(x["region"] == "APAC" for x in r["rows"])
    injection = client.post("/api/v1/pipelines/preview", headers=engineer, json={
        "graph": g, "node_id": "f", "pipeline_id": p["id"], "params": {"region": "x' OR '1'='1"}}).json()
    assert injection["rows"] == []  # the value is a quoted literal, never SQL
