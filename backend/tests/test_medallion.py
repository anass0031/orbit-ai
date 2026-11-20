"""Medallion rules R1-R3, Bronze → Silver template, lineage and star schema capabilities."""
import uuid

import pytest

from tests.conftest import bronze, cleaned, run_pipeline
from tests.test_pipeline_tools import chain, node, pipeline, rows, run, validate

CSV = "order_id,customer,amount\n1,Ana,10\n2,Ben,20\n2,Ben,20\n3,Cam,30\n"


def silver(client, h, name="orders_s"):
    b = bronze(client, h, f"{name}_raw", CSV)
    run_pipeline(client, h, name, cleaned(chain(node("in", "dataset_input", dataset_id=b["id"]), node("o", "output", layer="silver", name=name)), ["order_id"]))
    return next(d for d in client.get("/api/v1/datasets?layer=silver", headers=h).json() if d["name"] == name), b


def refused(client, h, graph, text):
    """Refused at validation, at publication and when asked to run."""
    v = validate(client, h, graph)
    assert not v["valid"] and any(text in i["message"] and i["node_id"] for i in v["issues"]), v["issues"]
    p = pipeline(client, h, graph)
    pub = client.post(f"/api/v1/pipelines/{p['id']}/publish", headers=h)
    assert pub.status_code == 422 and any(text in d["message"] for d in pub.json()["error"]["details"])
    assert client.post(f"/api/v1/pipelines/{p['id']}/run", headers=h).status_code == 422


def test_r1_gold_needs_silver(client, engineer):
    b = bronze(client, engineer, "raw_orders", CSV)
    direct = chain(node("in", "dataset_input", dataset_id=b["id"]), node("o", "output", layer="gold", name="g1"))
    refused(client, engineer, direct, "Gold must be built from Silver")
    inc = chain(node("in", "incremental_input", dataset_id=b["id"], watermark_column="order_id"), node("o", "output", layer="gold", name="g2"))
    refused(client, engineer, inc, "Gold must be built from Silver")
    drop = chain(node("in", "file_drop_input", folder="r1-drop"), node("o", "output", layer="gold", name="g3"))
    assert any("Gold must be built from Silver" in i["message"] for i in validate(client, engineer, drop)["issues"])
    # even cleaned, raw data must be saved in Silver before it reaches Gold
    gated = chain(node("in", "dataset_input", dataset_id=b["id"]), node("d", "dedupe", keys=["order_id"]),
                  node("q", "quality_check", rules=[{"type": "not_null", "column": "order_id"}]), node("o", "output", layer="gold", name="g4"))
    refused(client, engineer, gated, "Gold must be built from Silver")


def test_r1_allowed_cases(client, engineer):
    s, _ = silver(client, engineer)
    g = chain(node("in", "dataset_input", dataset_id=s["id"]), node("o", "output", layer="gold", name="from_silver"))
    assert validate(client, engineer, g)["valid"]
    run(client, engineer, pipeline(client, engineer, g)["id"])
    cal = chain(node("d", "date_dimension", start="2026-01-01", end="2026-01-05"), node("o", "output", layer="gold", name="calendar_ok"))
    assert validate(client, engineer, cal)["valid"]


def test_r2_silver_must_be_cleaned(client, engineer):
    b = bronze(client, engineer, "raw_r2", CSV)
    src = node("in", "dataset_input", dataset_id=b["id"])
    out = node("o", "output", layer="silver", name="s2")
    dd, qq = node("d", "dedupe", keys=["order_id"]), node("q", "quality_check", rules=[{"type": "not_null", "column": "order_id"}])
    refused(client, engineer, chain(src, out), "no Data quality and no Deduplicate step")
    refused(client, engineer, chain(src, dd, out), "no Data quality step")
    refused(client, engineer, chain(src, qq, out), "no Deduplicate step")
    assert validate(client, engineer, chain(src, dd, qq, out))["valid"]
    assert validate(client, engineer, chain(src, qq, dd, out))["valid"]  # the order is free
    drop = chain(node("in", "file_drop_input", folder="r2-drop"), out)
    assert any("Silver must be cleaned" in i["message"] for i in validate(client, engineer, drop)["issues"])


def test_r2_follows_every_branch_port(client, engineer):
    b = bronze(client, engineer, "raw_branch", CSV)
    g = {"nodes": [node("in", "dataset_input", dataset_id=b["id"]), node("d", "dedupe", keys=["order_id"]),
                   node("br", "branch", condition="amount > 15"), node("q", "quality_check", rules=[{"type": "not_null", "column": "order_id"}]),
                   node("ok", "output", layer="silver", name="big"), node("bad", "output", layer="silver", name="small")],
         "edges": [{"source": "in", "target": "d"}, {"source": "d", "target": "br"}, {"source": "br", "target": "q", "source_handle": "true"},
                   {"source": "q", "target": "ok"}, {"source": "br", "target": "bad", "source_handle": "false"}]}
    issues = [i for i in validate(client, engineer, g)["issues"] if i["severity"] == "error"]
    assert [i["node_id"] for i in issues] == ["bad"] and "no Data quality step" in issues[0]["message"]
    g["nodes"].append(node("q2", "quality_check", rules=[{"type": "not_null", "column": "order_id"}]))
    g["edges"][-1] = {"source": "br", "target": "q2", "source_handle": "false"}
    g["edges"].append({"source": "q2", "target": "bad"})
    assert validate(client, engineer, g)["valid"]


def test_r3_no_way_back(client, engineer):
    s, _ = silver(client, engineer, "orders_r3")
    run_pipeline(client, engineer, "gold r3", chain(node("in", "dataset_input", dataset_id=s["id"]), node("o", "output", layer="gold", name="kpi_r3")))
    gold = next(d for d in client.get("/api/v1/datasets?layer=gold", headers=engineer).json() if d["name"] == "kpi_r3")
    refused(client, engineer, chain(node("in", "dataset_input", dataset_id=gold["id"]), node("o", "output", layer="silver", name="back")),
            "Silver cannot be built from Gold")


def test_rules_follow_sub_pipelines_and_are_checked_at_run_time(client, engineer):
    s, b = silver(client, engineer, "orders_sub")
    child = chain(node("in", "dataset_input", dataset_id=s["id"]), node("f", "filter", condition="amount > 0"), node("o", "output", layer="silver", name="child_out"))
    c = pipeline(client, engineer, child)
    assert client.post(f"/api/v1/pipelines/{c['id']}/publish", headers=engineer).status_code in (200, 201)
    parent = chain(node("s", "subpipeline", pipeline_id=c["id"], node_id="f"), node("o", "output", layer="gold", name="from_child"))
    p = pipeline(client, engineer, parent)
    assert client.post(f"/api/v1/pipelines/{p['id']}/publish", headers=engineer).status_code in (200, 201)

    # the child now reads Bronze: the published parent is refused when it starts
    raw = cleaned(chain(node("in", "dataset_input", dataset_id=b["id"]), node("f", "filter", condition="amount > 0"), node("o", "output", layer="silver", name="child_out")), ["order_id"])
    client.put(f"/api/v1/pipelines/{c['id']}", headers=engineer, json={"graph": raw})
    assert client.post(f"/api/v1/pipelines/{c['id']}/publish", headers=engineer).status_code in (200, 201)
    assert any("Gold must be built from Silver" in i["message"] for i in validate(client, engineer, parent)["issues"])
    token = client.post(f"/api/v1/pipelines/{p['id']}/trigger-token", headers=engineer).json()["token"]
    started = client.post(f"/api/v1/pipelines/{p['id']}/trigger", headers={"X-Orbit-Trigger-Token": token}).json()
    d = client.get(f"/api/v1/runs/{started['run_id']}", headers=engineer).json()
    assert d["status"] == "failed" and d["error_class"] == "medallion_rule_violation" and "Gold must be built from Silver" in d["error_message"]
    assert d["node_status"]["o"]["status"] == "failed"
    assert not any(x["name"] == "from_child" for x in client.get("/api/v1/datasets?layer=gold", headers=engineer).json())


def test_duplicates_count_once_from_bronze_to_gold(client, engineer):
    s, b = silver(client, engineer, "orders_dup")
    assert b["row_count"] == 4 and s["row_count"] == 3
    run_pipeline(client, engineer, "kpi dup", chain(node("in", "dataset_input", dataset_id=s["id"]),
                                                    node("a", "aggregate", aggregations=[{"func": "sum", "column": "amount", "alias": "revenue"}, {"func": "count", "alias": "orders"}]),
                                                    node("o", "output", layer="gold", name="kpi_dup")))
    out, _ = rows(client, engineer, "gold", "kpi_dup")
    assert out == [{"revenue": 60, "orders": 3}]


# ── template ──
def test_bronze_to_silver_template(client, engineer, analyst):
    b = bronze(client, engineer, "shop_raw", "id,name\n1,  Ana \n1,  Ana \n2,Ben\n")
    url = "/api/v1/pipelines/templates/bronze-to-silver"
    assert client.post(url, headers=analyst, json={"dataset_id": b["id"], "keys": ["id"]}).status_code == 403
    assert client.post(url, headers=engineer, json={"dataset_id": b["id"], "keys": ["nope"]}).status_code == 422
    r = client.post(url, headers=engineer, json={"dataset_id": b["id"], "keys": ["id"]})
    assert r.status_code == 201, r.text
    p = client.get(f"/api/v1/pipelines/{r.json()['id']}", headers=engineer).json()
    assert [n["type"] for n in p["graph"]["nodes"]] == ["dataset_input", "clean", "dedupe", "fill_missing", "quality_check", "output"]
    rules = p["graph"]["nodes"][4]["config"]["rules"]
    assert {(x["type"], x["column"]) for x in rules} == {("not_null", "id"), ("unique", "id"), ("not_null", "name")}
    run(client, engineer, p["id"])
    out, ds = rows(client, engineer, "silver", "shop_raw_clean")
    assert ds["row_count"] == 2 and {x["name"] for x in out} == {"Ana", "Ben"}

    # the pipeline stays editable and still obeys the rules
    g = p["graph"]
    g["nodes"] = [n for n in g["nodes"] if n["type"] != "dedupe"]
    g["edges"] = [{"source": "input", "target": "clean"}, {"source": "clean", "target": "fill"}, {"source": "fill", "target": "quality"}, {"source": "quality", "target": "output"}]
    client.put(f"/api/v1/pipelines/{p['id']}", headers=engineer, json={"graph": g})
    assert client.post(f"/api/v1/pipelines/{p['id']}/publish", headers=engineer).status_code == 422

    s, _ = silver(client, engineer, "orders_tpl")
    assert client.post(url, headers=engineer, json={"dataset_id": s["id"], "keys": ["order_id"]}).status_code == 422  # Bronze only


def test_template_produces_a_clean_silver_dataset(client, engineer):
    csv = ("id,customer,city,amount,signup,paid,zip_code,manager_id\n"
           '1,Ana   Martin,Paris,"1 234,50",05/01/2024,true,01000,7\n'
           '1,Ana   Martin,Paris,"1 234,50",05/01/2024,true,01000,7\n'
           '2,Ben,-,"12,5",2024-02-10,false,75001,\n'
           "3,,Lyon,7.25,15.03.2024,,06000,8\n"
           "4,Dan,Nice,,2024-04-01,true,13000,9\n")
    b = bronze(client, engineer, "messy_raw", csv)
    assert b["row_count"] == 5
    r = client.post("/api/v1/pipelines/templates/bronze-to-silver", headers=engineer, json={"dataset_id": b["id"], "keys": ["id"]})
    assert r.status_code == 201, r.text
    graph = client.get(f"/api/v1/pipelines/{r.json()['id']}", headers=engineer).json()["graph"]
    nodes = {n["type"]: n["config"] for n in graph["nodes"]}
    assert nodes["clean"]["number_columns"] == ["amount"] and nodes["clean"]["date_columns"] == ["signup"]
    assert {f["column"]: f["strategy"] for f in nodes["fill_missing"]["rules"]} == {"customer": "value", "city": "value", "amount": "median", "zip_code": "value"}
    run(client, engineer, r.json()["id"])
    out, ds = rows(client, engineer, "silver", "messy_raw_clean")
    by = {x["id"]: x for x in out}
    assert len(out) == 4  # duplicates removed
    assert by[1]["customer"] == "Ana Martin" and by[3]["customer"] == "Unknown" and by[2]["city"] == "Unknown"  # standardised, missing text filled
    assert [by[i]["amount"] for i in (1, 2, 3, 4)] == [1234.5, 12.5, 7.25, 12.5]  # numbers corrected, missing number = median
    assert [str(by[i]["signup"])[:10] for i in (1, 2, 3, 4)] == ["2024-01-05", "2024-02-10", "2024-03-15", "2024-04-01"]  # one date format
    assert {c["name"]: c["type"] for c in ds["schema"]}["amount"] == "double" and {c["name"]: c["type"] for c in ds["schema"]}["signup"] == "date"
    assert by[1]["zip_code"] == "01000"  # codes keep their leading zero
    assert by[2]["manager_id"] is None and by[3]["paid"] is None  # identifiers and booleans are never guessed


def test_template_with_a_composite_key(client, engineer):
    b = bronze(client, engineer, "lines_raw", "order_id,line,qty\n1,1,5\n1,2,6\n1,2,6\n2,1,7\n")
    r = client.post("/api/v1/pipelines/templates/bronze-to-silver", headers=engineer, json={"dataset_id": b["id"], "keys": ["order_id", "line"], "silver_name": "lines"})
    d = run(client, engineer, r.json()["id"])
    assert d["outputs"][0]["rows"] == 3


# ── lineage ──
def test_lineage(client, engineer, analyst):
    s, b = silver(client, engineer, "orders_lin")
    run_pipeline(client, engineer, "gold lin", chain(node("in", "dataset_input", dataset_id=s["id"]), node("o", "output", layer="gold", name="kpi_lin")))
    gold = next(d for d in client.get("/api/v1/datasets?layer=gold", headers=engineer).json() if d["name"] == "kpi_lin")
    ls = client.get(f"/api/v1/datasets/{s['id']}/lineage", headers=analyst).json()
    assert ls["sources"] == [{"dataset_id": b["id"], "qualified_name": b["qualified_name"], "version": 1}]
    assert [x["qualified_name"] for x in ls["produced"]] == ["gold.kpi_lin"] and ls["pipeline_run_id"]
    lg = client.get(f"/api/v1/datasets/{gold['id']}/lineage", headers=analyst).json()
    assert lg["sources"][0]["qualified_name"] == s["qualified_name"] and lg["produced"] == []
    assert client.get(f"/api/v1/datasets/{b['id']}/lineage", headers=analyst).json()["source_import_id"]
    detail = client.get(f"/api/v1/datasets/{s['id']}", headers=analyst).json()
    assert detail["versions"][0]["lineage"][0]["dataset_id"] == b["id"]


def test_lineage_only_lists_the_inputs_of_each_output(client, engineer):
    a, _ = silver(client, engineer, "orders_a")
    b, _ = silver(client, engineer, "orders_b")
    g = {"nodes": [node("i1", "dataset_input", dataset_id=a["id"]), node("i2", "dataset_input", dataset_id=b["id"]),
                   node("o1", "output", layer="gold", name="only_a"), node("o2", "output", layer="gold", name="only_b")],
         "edges": [{"source": "i1", "target": "o1"}, {"source": "i2", "target": "o2"}]}
    run_pipeline(client, engineer, f"two {uuid.uuid4().hex[:5]}", g)
    only = next(d for d in client.get("/api/v1/datasets?layer=gold", headers=engineer).json() if d["name"] == "only_a")
    assert [x["qualified_name"] for x in client.get(f"/api/v1/datasets/{only['id']}/lineage", headers=engineer).json()["sources"]] == [a["qualified_name"]]


# ── star schema ──
def customers(client, h, csv="customer_id,name\n1,Ana\n2,Ben\n"):
    b = bronze(client, h, "cust_raw", csv)
    run_pipeline(client, h, f"cust {uuid.uuid4().hex[:5]}", cleaned(chain(node("in", "dataset_input", dataset_id=b["id"]), node("o", "output", layer="silver", name="cust")), ["customer_id"]))
    return next(d for d in client.get("/api/v1/datasets?layer=silver", headers=h).json() if d["name"] == "cust")


def facts(client, h, csv="order_id,customer_id,amount\n10,1,5\n11,2,7\n12,99,9\n13,,4\n"):
    b = bronze(client, h, "fact_raw", csv)
    run_pipeline(client, h, f"fact {uuid.uuid4().hex[:5]}", cleaned(chain(node("in", "dataset_input", dataset_id=b["id"]), node("o", "output", layer="silver", name="fact_s")), ["order_id"]))
    return next(d for d in client.get("/api/v1/datasets?layer=silver", headers=h).json() if d["name"] == "fact_s")


def star(cust, fact):
    return {"nodes": [node("c", "dataset_input", dataset_id=cust["id"]), node("ck", "surrogate_key", keys=["customer_id"], into="customer_key"),
                      node("dim", "output", layer="gold", name="dim_customer", model_role="dimension", primary_key=["customer_key"]),
                      node("f", "dataset_input", dataset_id=fact["id"]), node("fk", "surrogate_key", keys=["customer_id"], into="customer_key"),
                      node("fct", "output", layer="gold", name="fact_sales", model_role="fact", primary_key=["order_id"],
                           relationships=[{"column": "customer_key", "dimension": "dim_customer"}])],
            "edges": [{"source": "c", "target": "ck"}, {"source": "ck", "target": "dim"}, {"source": "f", "target": "fk"}, {"source": "fk", "target": "fct"}]}


def test_surrogate_key_is_stable_between_runs(client, engineer):
    cust = customers(client, engineer)
    g = chain(node("in", "dataset_input", dataset_id=cust["id"]), node("k", "surrogate_key", keys=["customer_id"], into="customer_key"),
              node("o", "output", layer="gold", name="keys_a"))
    p = pipeline(client, engineer, g)
    run(client, engineer, p["id"])
    first = {r["customer_id"]: r["customer_key"] for r in rows(client, engineer, "gold", "keys_a")[0]}
    customers(client, engineer, "customer_id,name\n2,Ben\n3,Cam\n1,Ana\n")
    run(client, engineer, p["id"])
    second = {r["customer_id"]: r["customer_key"] for r in rows(client, engineer, "gold", "keys_a")[0]}
    assert first[1] == second[1] and first[2] == second[2] and len(set(second.values())) == 3 and all(v > 0 for v in second.values())


def test_unknown_member_and_foreign_keys(client, engineer):
    run_pipeline(client, engineer, "star", star(customers(client, engineer), facts(client, engineer)))
    dim, ds = rows(client, engineer, "gold", "dim_customer")
    assert ds["model_role"] == "dimension" and ds["primary_key"] == ["customer_key"] and len(dim) == 3
    unknown = next(r for r in dim if r["customer_key"] == -1)
    assert unknown["name"] == "Unknown" and unknown["customer_id"] is None
    fact, fs = rows(client, engineer, "gold", "fact_sales")
    assert len(fact) == 4 and fs["model_role"] == "fact"  # no fact row is lost
    by = {r["order_id"]: r["customer_key"] for r in fact}
    keys = {r["customer_id"]: r["customer_key"] for r in dim}
    assert by[10] == keys[1] and by[11] == keys[2] and by[12] == -1 and by[13] == -1
    m = client.get(f"/api/v1/datasets/{fs['id']}/model", headers=engineer).json()
    assert m["role"] == "fact" and m["relationships"][0] == {"column": "customer_key", "dimension": "gold.dim_customer", "dimension_id": ds["id"], "key": "customer_key"}
    assert client.get(f"/api/v1/datasets/{ds['id']}/model", headers=engineer).json()["used_by"] == [{"fact": "gold.fact_sales", "fact_id": fs["id"], "column": "customer_key"}]


def test_integrity_failure_blocks_every_write(client, engineer):
    cust, fact = customers(client, engineer), facts(client, engineer)
    bad = star(cust, fact)
    bad["nodes"][1] = node("ck", "derived_column", columns=[{"name": "customer_key", "expression": "1"}])  # the same key for everyone
    p = pipeline(client, engineer, bad)
    d = run(client, engineer, p["id"], ok=False)
    assert d["status"] == "failed" and d["error_class"] == "quality_gate_failed" and "duplicate value(s) of the primary key" in d["error_message"]
    assert client.get("/api/v1/datasets?layer=gold", headers=engineer).json() == []


@pytest.mark.parametrize("change, message", [
    ({"model_role": "cube"}, "Model role must be"),
    ({"primary_key": []}, "exactly one primary key"),
    ({"mode": "append"}, "overwrite mode"),
])
def test_model_configuration_is_checked(client, engineer, change, message):
    g = star(customers(client, engineer), facts(client, engineer))
    g["nodes"][2]["config"].update(change)
    assert any(message in i["message"] for i in validate(client, engineer, g)["issues"])


def test_relationship_needs_a_dimension(client, engineer):
    g = star(customers(client, engineer), facts(client, engineer))
    g["nodes"][5]["config"]["relationships"] = [{"column": "customer_key", "dimension": "dim_missing"}]
    assert any("not a dimension of the model" in i["message"] for i in validate(client, engineer, g)["issues"])
    g = star(customers(client, engineer), facts(client, engineer))
    g["nodes"][2]["config"]["primary_key"] = ["name"]
    assert any("must be an integer" in i["message"] for i in validate(client, engineer, g)["issues"])
