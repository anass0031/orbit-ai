"""The 80 data processing scenarios of the platform, numbered as in docs/SCENARIOS_DONNEES.md."""
import io
import json

import pyarrow as pa
import pyarrow.parquet as pq

from tests.conftest import bronze, cleaned, run_pipeline
from tests.test_gold_models import CATEGORIES, CUSTOMERS, FLAT, ITEMS, LOOKUP, ORDERS, PAYMENTS, PRODUCTS, analysis, generate, gold, shop, silver

API = "/api/v1"


def upload(client, h, filename, data, **form):
    body = data.encode() if isinstance(data, str) else data
    return client.post(f"{API}/imports", headers=h, files={"file": (filename, body)}, data=form)


def load(client, h, filename, data, dataset, mode="replace", **form):
    imp = upload(client, h, filename, data, **form).json()
    assert imp["status"] == "discovered", imp
    return client.post(f"{API}/imports/{imp['id']}/ingest", headers=h, json={"dataset_name": dataset, "mode": mode})


def batch(client, h, files, **form):
    parts = [("files", (n, d.encode() if isinstance(d, str) else d)) for n, d in files]
    r = client.post(f"{API}/imports/batch", headers=h, files=parts, data=form)
    assert r.status_code == 200, r.text
    return r.json()


def dataset(client, h, layer, name):
    return next(d for d in client.get(f"{API}/datasets?layer={layer}", headers=h).json() if d["name"] == name)


def rows(client, h, layer, name, limit=500):
    return client.get(f"{API}/datasets/{dataset(client, h, layer, name)['id']}/preview?limit={limit}", headers=h).json()["rows"]


def node(i, t, **c):
    return {"id": i, "type": t, "config": c}


def chain(*nodes):
    return {"nodes": list(nodes), "edges": [{"source": a["id"], "target": b["id"]} for a, b in zip(nodes, nodes[1:], strict=False)]}


def to_silver(client, h, name, source_id, keys, *steps, target=None):
    target = target or f"{name}_s"
    graph = chain(node("in", "dataset_input", dataset_id=source_id), *steps, node("out", "output", layer="silver", name=target))
    return run_pipeline(client, h, name, cleaned(graph, keys))


def parquet_bytes():
    buf = io.BytesIO()
    pq.write_table(pa.table({"id": [1, 2], "v": ["a", "b"]}), buf)
    return buf.getvalue()


def excel_bytes():
    import pandas as pd

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame({"id": [1, 2], "name": ["a", "b"]}).to_excel(w, sheet_name="customers", index=False)
        pd.DataFrame({"id": [7], "total": [3.5]}).to_excel(w, sheet_name="orders", index=False)
    return buf.getvalue()


SHOP_JSON = json.dumps({"customers": [{"id": 1, "name": "Ana"}, {"id": 2, "name": "Ben"}], "orders": [{"id": 10, "customer_id": 1, "total": 5}]})
NESTED = json.dumps([{"order_id": 1, "customer": {"name": "Ana", "address": {"city": "Paris"}}, "items": [{"sku": "A", "qty": 2}, {"sku": "B", "qty": 1}]},
                     {"order_id": 2, "customer": {"name": "Ben", "address": {"city": "Lyon"}}, "items": [{"sku": "A", "qty": 5}]}])
XML = """<shop><orders><order id="1"><customer>Ana</customer><item><sku>A</sku><qty>2</qty></item><item><sku>B</sku><qty>1</qty></item></order>
<order id="2"><customer>Ben</customer><item><sku>C</sku><qty>4</qty></item></order></orders></shop>"""
YAML = """orders:
  - order_id: 1
    customer: {name: Ana, city: Paris}
    items:
      - {sku: A, qty: 2}
      - {sku: B, qty: 1}
  - order_id: 2
    customer: {name: Ben, city: Lyon}
    items: []
"""


# ── 1. input and ingestion ───────────────────────────────────────────────
def test_01_single_file_single_dataset(client, engineer):
    assert bronze(client, engineer, "one", "id,v\n1,a\n2,b\n")["row_count"] == 2


def test_02_single_file_with_several_logical_entities(client, engineer):
    out = batch(client, engineer, [("shop.json", SHOP_JSON), ("book.xlsx", excel_bytes())])
    assert {r["dataset"] for r in out["results"]} == {"bronze.shop_customers", "bronze.shop_orders", "bronze.book_customers", "bronze.book_orders"}
    assert out["summary"]["ingested"] == 4


def test_03_multiple_files_with_different_datasets(client, engineer):
    out = batch(client, engineer, [("a.csv", "id,v\n1,x\n"), ("b.csv", "code,amount\nZ,3\n")])
    assert {r["dataset"] for r in out["results"]} == {"bronze.a", "bronze.b"}


def test_04_multiple_files_of_the_same_dataset(client, engineer):
    batch(client, engineer, [("jan.csv", "id,v\n1,x\n2,y\n"), ("feb.csv", "id,v\n3,z\n")], dataset_name="sales_all")
    assert dataset(client, engineer, "bronze", "sales_all")["row_count"] == 3


def test_05_multiple_related_files(client, engineer):
    silver(client, engineer, "customers", CUSTOMERS, ["customer_id"])
    silver(client, engineer, "orders", ORDERS, ["order_id"])
    assert "orders.customer_id = customers.customer_id" in {r["join"] for r in analysis(client, engineer)["relationships"]}


def test_06_multiple_independent_sources(client, engineer):
    silver(client, engineer, "ventes", FLAT, ["order_id"])
    silver(client, engineer, "tickets", "ticket_id,agent,opened_on,minutes\n1,Lina,2026-01-02,30\n2,Omar,2026-01-03,45\n3,Lina,2026-01-04,20\n", ["ticket_id"])
    assert len({m["id"].split("_")[0] for m in analysis(client, engineer)["models"]}) == 2


def test_07_several_formats_in_one_batch(client, engineer):
    out = batch(client, engineer, [("a.csv", "id,v\n1,x\n"), ("b.json", '[{"id":1}]'), ("c.xml", "<r><x><id>1</id></x><x><id>2</id></x></r>"), ("d.yaml", "- id: 1\n- id: 2\n"),
                                   ("e.parquet", parquet_bytes()), ("f.xlsx", excel_bytes())])
    assert out["summary"] == {"ingested": 7, "skipped": 0, "failed": 0}


def test_08_empty_and_header_only_files(client, engineer):
    assert upload(client, engineer, "e.csv", b"").status_code == 422
    r = upload(client, engineer, "h.csv", "id,name\n").json()
    assert r["status"] == "failed" and "no data rows" in r["error_message"]


def test_09_corrupted_files(client, engineer):
    for name, data in [("x.xlsx", b"not a workbook"), ("x.parquet", b"not parquet"), ("x.xml", "<a><b></a>"), ("x.yaml", "a: [1, 2"), ("x.json", "{oops")]:
        r = upload(client, engineer, name, data)
        assert r.status_code == 201 and r.json()["status"] == "failed", name


def test_10_large_file(client, engineer):
    csv = "id,region,amount\n" + "".join(f"{i},r{i % 7},{i % 100}.5\n" for i in range(150_000))
    r = load(client, engineer, "big.csv", csv, "big")
    assert r.status_code == 201 and r.json()["dataset"]["row_count"] == 150_000


def test_11_same_file_loaded_twice(client, engineer):
    files = [("again.csv", "id,v\n1,x\n")]
    assert batch(client, engineer, files)["summary"]["ingested"] == 1
    assert batch(client, engineer, files)["summary"]["skipped"] == 1
    assert dataset(client, engineer, "bronze", "again")["current_version"] == 1
    assert any("already loaded" in w for w in upload(client, engineer, "again.csv", "id,v\n1,x\n").json()["warnings"])


def test_12_incremental_ingestion(client, engineer):
    load(client, engineer, "e1.csv", "id,v\n1,a\n2,b\n", "events")
    ds = dataset(client, engineer, "bronze", "events")
    graph = cleaned(chain(node("in", "incremental_input", dataset_id=ds["id"], watermark_column="id"), node("out", "output", layer="silver", name="events_s", mode="append")), ["id"])
    p = client.post(f"{API}/pipelines", headers=engineer, json={"name": "inc", "graph": graph}).json()
    assert client.post(f"{API}/pipelines/{p['id']}/run", headers=engineer).json()["status"] == "succeeded"
    load(client, engineer, "e2.csv", "id,v\n3,c\n", "events", mode="append")
    assert client.post(f"{API}/pipelines/{p['id']}/run", headers=engineer).json()["records_in"] == 1
    assert sorted(r["id"] for r in rows(client, engineer, "silver", "events_s")) == [1, 2, 3]


# ── 2. schema and structure ──────────────────────────────────────────────
def test_13_identical_schemas(client, engineer):
    load(client, engineer, "a.csv", "id,v\n1,a\n", "same")
    assert load(client, engineer, "b.csv", "id,v\n2,b\n", "same", mode="append").json()["dataset"]["row_count"] == 2


def test_14_compatible_schemas(client, engineer):
    load(client, engineer, "a.csv", "id,v\n1,10\n", "wider")
    r = load(client, engineer, "b.csv", "id,v\n2,1.5\n", "wider", mode="append").json()
    assert {c["name"]: c["type"] for c in r["dataset"]["schema"]}["v"] == "double"


def test_15_incompatible_schemas(client, engineer):
    load(client, engineer, "a.csv", "id,v\n1,10\n", "strict")
    r = load(client, engineer, "b.csv", "id,v\n2,abc\n", "strict", mode="append")
    assert r.status_code == 422 and r.json()["error"]["code"] == "schema_incompatible"
    assert dataset(client, engineer, "bronze", "strict")["current_version"] == 1


def test_16_missing_columns(client, engineer):
    load(client, engineer, "a.csv", "id,v,w\n1,a,b\n", "gaps")
    load(client, engineer, "b.csv", "id,v\n2,c\n", "gaps", mode="append")
    assert {r["id"]: r["w"] for r in rows(client, engineer, "bronze", "gaps")} == {1: "b", 2: None}


def test_17_additional_columns(client, engineer):
    load(client, engineer, "a.csv", "id,v\n1,a\n", "extra")
    load(client, engineer, "b.csv", "id,v,w\n2,c,new\n", "extra", mode="append")
    assert {r["id"]: r["w"] for r in rows(client, engineer, "bronze", "extra")} == {1: None, 2: "new"}


def test_18_different_names_for_the_same_information(client, engineer):
    a, b = bronze(client, engineer, "crm", "client_id,ville\n1,Paris\n"), bronze(client, engineer, "erp", "customer_id,city\n2,Lyon\n")
    g = {"nodes": [node("a", "dataset_input", dataset_id=a["id"]), node("b", "dataset_input", dataset_id=b["id"]),
                   node("ra", "select", columns=[{"name": "client_id", "alias": "customer_id"}, {"name": "ville", "alias": "city"}]),
                   node("rb", "select", columns=[{"name": "customer_id"}, {"name": "city"}]), node("u", "union"), node("out", "output", layer="silver", name="all_customers")],
         "edges": [{"source": x, "target": y} for x, y in [("a", "ra"), ("b", "rb"), ("ra", "u"), ("rb", "u"), ("u", "out")]]}
    run_pipeline(client, engineer, "names", cleaned(g, ["customer_id"]))
    assert {r["city"] for r in rows(client, engineer, "silver", "all_customers")} == {"Paris", "Lyon"}


def test_19_different_types_for_the_same_field(client, engineer):
    a, b = bronze(client, engineer, "t1", "id,amount\n1,10\n"), bronze(client, engineer, "t2", "id,amount\n2,N/D\n")
    g = {"nodes": [node("a", "dataset_input", dataset_id=a["id"]), node("b", "dataset_input", dataset_id=b["id"]),
                   node("ca", "select", columns=[{"name": "id"}, {"name": "amount", "cast": "double"}]), node("cb", "select", columns=[{"name": "id"}, {"name": "amount", "cast": "double"}]),
                   node("u", "union"), node("out", "output", layer="silver", name="typed")],
         "edges": [{"source": x, "target": y} for x, y in [("a", "ca"), ("b", "cb"), ("ca", "u"), ("cb", "u"), ("u", "out")]]}
    run_pipeline(client, engineer, "types", cleaned(g, ["id"]))
    assert {r["id"]: r["amount"] for r in rows(client, engineer, "silver", "typed")} == {1: 10.0, 2: None}


def test_20_nested_json_objects(client, engineer):
    r = upload(client, engineer, "n.json", NESTED).json()
    assert {"customer_name", "customer_address_city"} <= {c["name"] for c in r["schema"]}


def test_21_json_arrays_of_objects(client, engineer):
    out = batch(client, engineer, [("n.json", NESTED)])
    assert {x["dataset"] for x in out["results"]} == {"bronze.n", "bronze.n_items"}
    child = next(x for x in out["results"] if x["dataset"].endswith("items"))
    assert child["rows"] == 3


def test_22_mixed_nested_and_flat_json(client, engineer):
    doc = json.dumps([{"id": 1, "city": "Paris"}, {"id": 2, "address": {"city": "Lyon"}}, {"id": 3, "tags": ["a", "b"]}])
    assert upload(client, engineer, "m.json", doc).json()["row_count"] == 3
    assert {x["entity"] for x in batch(client, engineer, [("m.json", doc)])["results"]} == {"m", "m_tags"}


def test_23_xml_with_nested_and_repeated_nodes(client, engineer):
    out = batch(client, engineer, [("s.xml", XML)])
    by = {x["entity"]: x for x in out["results"]}
    assert by["orders"]["rows"] == 2 and by["orders_item"]["rows"] == 3
    assert {r["order_id"] for r in rows(client, engineer, "bronze", by["orders_item"]["dataset"].split(".")[1])} == {1, 2}
    assert upload(client, engineer, "bad.xml", '<!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>').json()["status"] == "failed"


def test_24_yaml_with_nested_objects_and_arrays(client, engineer):
    by = {x["entity"]: x for x in batch(client, engineer, [("y.yaml", YAML)])["results"]}
    assert by["orders"]["rows"] == 2 and by["orders_items"]["rows"] == 2
    child = rows(client, engineer, "bronze", by["orders_items"]["dataset"].split(".")[1])
    assert {r["order_id"] for r in child} == {1}


def test_25_schema_change_between_batches(client, engineer):
    load(client, engineer, "a.csv", "id,v\n1,a\n", "evolving")
    r = load(client, engineer, "b.csv", "id,v,w\n2,b,c\n", "evolving", mode="append").json()
    assert r["version"]["schema_changes"]["added"] == ["w"]


# ── 3. cleaning ──────────────────────────────────────────────────────────
def cleaned_rows(client, h, csv, *steps, keys=("id",)):
    b = bronze(client, h, "dirty", csv)
    to_silver(client, h, "clean", b["id"], list(keys), *steps)
    return {r["id"]: r for r in rows(client, h, "silver", "clean_s")}


def test_26_null_values(client, engineer):
    out = cleaned_rows(client, engineer, "id,city,n\n1,,\n2,Paris,4\n", node("f", "fill_missing", rules=[{"column": "city", "strategy": "value", "value": "Unknown"}, {"column": "n", "strategy": "median"}]))
    assert out[1]["city"] == "Unknown" and out[1]["n"] == 4


def test_27_empty_strings_and_whitespace(client, engineer):
    assert cleaned_rows(client, engineer, 'id,city\n1,"   "\n2,""\n3,Paris\n', node("c", "clean"))[1]["city"] is None


def test_28_different_representations_of_missing_values(client, engineer):
    out = cleaned_rows(client, engineer, "id,city\n1,-\n2,n/a\n3,NULL\n4,?\n5,Paris\n", node("c", "clean"))
    assert [out[i]["city"] for i in (1, 2, 3, 4, 5)] == [None, None, None, None, "Paris"]


def test_29_exact_duplicate_rows(client, engineer):
    assert len(cleaned_rows(client, engineer, "id,v\n1,a\n1,a\n2,b\n")) == 2


def test_30_partial_duplicates_keep_the_latest(client, engineer):
    b = bronze(client, engineer, "partial", "id,v,updated\n1,old,2026-01-01\n1,new,2026-02-01\n")
    graph = chain(node("in", "dataset_input", dataset_id=b["id"]), node("d", "dedupe", keys=["id"], order_by="updated"),
                  node("q", "quality_check", rules=[{"type": "not_null", "column": "id", "on_failure": "fail"}]), node("out", "output", layer="silver", name="latest"))
    run_pipeline(client, engineer, "latest", graph)
    assert rows(client, engineer, "silver", "latest")[0]["v"] == "new"


def test_31_duplicate_business_identifiers_are_blocked(client, engineer):
    b = bronze(client, engineer, "ids", "id,v\n1,a\n1,b\n")
    graph = chain(node("in", "dataset_input", dataset_id=b["id"]), node("d", "dedupe", keys=["id", "v"]),
                  node("q", "quality_check", rules=[{"type": "unique", "column": "id", "on_failure": "fail"}]), node("out", "output", layer="silver", name="ids_s"))
    p = client.post(f"{API}/pipelines", headers=engineer, json={"name": "ids", "graph": graph}).json()
    assert client.post(f"{API}/pipelines/{p['id']}/run", headers=engineer).json()["status"] == "failed"
    assert not [d for d in client.get(f"{API}/datasets?layer=silver", headers=engineer).json() if d["name"] == "ids_s"]


def test_32_invalid_data_types(client, engineer):
    out = cleaned_rows(client, engineer, "id,amount\n1,12.5\n2,abc\n", node("c", "clean", number_columns=["amount"]))
    assert out[1]["amount"] == 12.5 and out[2]["amount"] is None


def test_33_inconsistent_date_formats(client, engineer):
    out = cleaned_rows(client, engineer, "id,d\n1,2026-03-15\n2,15/03/2026\n3,15.03.2026\n", node("c", "clean", date_columns=["d"]))
    assert {str(r["d"])[:10] for r in out.values()} == {"2026-03-15"}


def test_34_different_decimal_separators(client, engineer):
    out = cleaned_rows(client, engineer, 'id,amount\n1,"1 234,50"\n2,1234.50\n3,"1,234.50"\n', node("c", "clean", number_columns=["amount"]))
    assert {r["amount"] for r in out.values()} == {1234.5}


def test_34_mixed_decimal_separators_in_one_file(client, engineer):
    r = upload(client, engineer, "mixed.csv", "id,amount\n1,12.50\n2,12,50\n".replace("2,12,50", '2,"12,50"'))
    assert r.status_code == 201 and r.json()["status"] == "discovered"
    assert any("decimal commas" in w for w in r.json()["warnings"])
    out = cleaned_rows(client, engineer, 'id,amount\n1,12.50\n2,"12,50"\n', node("c", "clean", number_columns=["amount"]))
    assert {r["amount"] for r in out.values()} == {12.5}


def test_35_inconsistent_capitalization(client, engineer):
    out = cleaned_rows(client, engineer, "id,city\n1,PARIS\n2,paris\n3,pArIs\n", node("c", "clean", case="title"))
    assert {r["city"] for r in out.values()} == {"Paris"}


def test_36_leading_and_trailing_spaces(client, engineer):
    assert cleaned_rows(client, engineer, 'id,name\n1,"  Ana   Martin "\n', node("c", "clean"))[1]["name"] == "Ana Martin"


def test_37_malformed_values_are_set_aside(client, engineer):
    r = upload(client, engineer, "bad.csv", "id,v\n1,a\n2,b,EXTRA\n3,c\n").json()
    assert r["row_count"] == 2 and r["quarantined_count"] == 1 and r["quarantine_location"]


def test_38_outliers(client, engineer):
    csv = "id,amount\n" + "".join(f"{i},{10 + i % 3}\n" for i in range(1, 30)) + "99,9000\n"
    out = cleaned_rows(client, engineer, csv, node("a", "anomaly_detection", column="amount", method="iqr"))
    assert [i for i, r in out.items() if r["is_anomaly"]] == [99]


def test_39_inconsistent_units_and_currencies(client, engineer):
    out = cleaned_rows(client, engineer, "id,amount,currency\n1,100,EUR\n2,110,MAD\n", node("d", "derived_column", columns=[{"name": "amount_eur", "expression": "CASE WHEN currency = 'MAD' THEN amount / 11 ELSE amount END"}]))
    assert out[1]["amount_eur"] == 100 and out[2]["amount_eur"] == 10


def test_40_invalid_categorical_values(client, engineer):
    b = bronze(client, engineer, "cats", "id,status\n1,open\n2,closed\n3,banana\n")
    graph = chain(node("in", "dataset_input", dataset_id=b["id"]), node("d", "dedupe", keys=["id"]),
                  node("q", "quality_check", rules=[{"type": "allowed_values", "column": "status", "params": {"values": ["open", "closed"]}, "on_failure": "quarantine"}]), node("out", "output", layer="silver", name="cats_s"))
    run_pipeline(client, engineer, "cats", graph)
    assert sorted(r["id"] for r in rows(client, engineer, "silver", "cats_s")) == [1, 2]


# ── 4. relationships ─────────────────────────────────────────────────────
def table(a, name):
    return next(t for t in a["silver_tables"] if t["name"] == name)


def relation(a, join):
    return next(r for r in a["relationships"] if r["join"] == join)


def test_41_primary_key_identification(client, engineer):
    shop(client, engineer)
    a = analysis(client, engineer)
    assert table(a, "customers")["primary_key"] == ["customer_id"] and table(a, "order_items")["primary_key"] == ["order_id", "product_id"]


def test_42_foreign_key_identification(client, engineer):
    shop(client, engineer)
    assert relation(analysis(client, engineer), "order_items.product_id = products.product_id")["confidence"] == "high"


def test_43_one_to_one(client, engineer):
    silver(client, engineer, "customers", CUSTOMERS, ["customer_id"])
    silver(client, engineer, "loyalty", "loyalty_id,customer_id,points\n1,1,10\n2,2,20\n3,3,30\n", ["loyalty_id"])
    assert relation(analysis(client, engineer), "loyalty.customer_id = customers.customer_id")["cardinality"] == "1:1"


def test_44_one_to_many(client, engineer):
    shop(client, engineer)
    assert relation(analysis(client, engineer), "orders.customer_id = customers.customer_id")["cardinality"] == "N:1"


def test_45_many_to_many(client, engineer):
    silver(client, engineer, "students", "student_id,student_name\n1,Ana\n2,Ben\n3,Eva\n", ["student_id"])
    silver(client, engineer, "courses", "course_id,course_name\n10,Math\n20,Art\n", ["course_id"])
    silver(client, engineer, "enrollments", "student_id,course_id\n1,10\n1,20\n2,10\n3,20\n", ["student_id", "course_id"])
    a = analysis(client, engineer)
    assert table(a, "enrollments")["role"] == "bridge" and any(r["cardinality"] == "N:M" and "enrollments" in r["join"] for r in a["relationships"])


def test_46_orphan_records_keep_their_measures(client, engineer):
    shop(client, engineer)
    a = analysis(client, engineer)
    assert relation(a, "orders.customer_id = customers.customer_id")["orphan_rows"] == 1
    generate(client, engineer, a["models"][0]["id"])
    g = gold(client, engineer)
    assert g["fact_orders"]["row_count"] == 5 and sum(r["total"] for r in rows(client, engineer, "gold", "fact_orders")) == 4240


def test_47_several_tables_share_an_identifier(client, engineer):
    shop(client, engineer)
    joins = {r["join"] for r in analysis(client, engineer)["relationships"]}
    assert {"order_items.order_id = orders.order_id", "payments.order_id = orders.order_id"} <= joins


def test_48_conflicting_records_across_sources(client, engineer):
    silver(client, engineer, "crm_clients", "client_id,city\n1,Paris\n2,Lyon\n", ["client_id"])
    silver(client, engineer, "erp_clients", "client_id,city\n1,Paris\n2,Nice\n", ["client_id"])
    assert analysis(client, engineer)["conflicts"] == [{"tables": ["crm_clients", "erp_clients"], "key": "client_id", "column": "city", "conflicting_rows": 1}]


def test_49_different_identifiers_for_the_same_entity(client, engineer):
    silver(client, engineer, "crm_people", "crm_id,full_name\n1,Ana Martin\n2,Ben Durand\n3,Eva Costa\n", ["crm_id"])
    silver(client, engineer, "hr_people", "hr_id,full_name\n901,Ana Martin\n902,Ben Durand\n903,Eva Costa\n", ["hr_id"])
    assert analysis(client, engineer)["same_entities"][0]["shared_members"] == 3


def test_50_functional_dependencies(client, engineer):
    silver(client, engineer, "ventes", FLAT, ["order_id"])
    assert {"table": "ventes", "determinant": "ville", "dependent": "pays"} in analysis(client, engineer)["functional_dependencies"]


# ── 5. Bronze → Silver ───────────────────────────────────────────────────
def test_51_one_bronze_to_one_silver(client, engineer):
    b = bronze(client, engineer, "b51", "id,v\n1,a\n")
    to_silver(client, engineer, "p51", b["id"], ["id"])
    assert dataset(client, engineer, "silver", "p51_s")["row_count"] == 1


def test_52_one_bronze_to_several_silver(client, engineer):
    b = bronze(client, engineer, "mixed", "order_id,customer,total\n1,Ana,5\n2,Ana,7\n3,Ben,9\n")
    g = {"nodes": [node("in", "dataset_input", dataset_id=b["id"]), node("c", "sql", query="SELECT DISTINCT customer FROM input"), node("o", "select", columns=[{"name": "order_id"}, {"name": "total"}]),
                   node("c_out", "output", layer="silver", name="the_customers"), node("o_out", "output", layer="silver", name="the_orders")],
         "edges": [{"source": x, "target": y} for x, y in [("in", "c"), ("in", "o"), ("c", "c_out"), ("o", "o_out")]]}
    run_pipeline(client, engineer, "split", cleaned(g, {"c_out": ["customer"], "o_out": ["order_id"]}))
    assert dataset(client, engineer, "silver", "the_customers")["row_count"] == 2 and dataset(client, engineer, "silver", "the_orders")["row_count"] == 3


def test_53_several_bronze_merged_into_one_silver(client, engineer):
    a, b = bronze(client, engineer, "m1", "id,v\n1,a\n"), bronze(client, engineer, "m2", "id,v\n2,b\n")
    g = {"nodes": [node("a", "dataset_input", dataset_id=a["id"]), node("b", "dataset_input", dataset_id=b["id"]), node("u", "union"), node("out", "output", layer="silver", name="merged")],
         "edges": [{"source": "a", "target": "u"}, {"source": "b", "target": "u"}, {"source": "u", "target": "out"}]}
    run_pipeline(client, engineer, "merge", cleaned(g, ["id"]))
    assert dataset(client, engineer, "silver", "merged")["row_count"] == 2


def test_54_several_bronze_to_several_silver(client, engineer):
    a, b = bronze(client, engineer, "n1", "id,v\n1,a\n"), bronze(client, engineer, "n2", "id,w\n2,b\n")
    g = {"nodes": [node("a", "dataset_input", dataset_id=a["id"]), node("b", "dataset_input", dataset_id=b["id"]), node("oa", "output", layer="silver", name="n1_s"), node("ob", "output", layer="silver", name="n2_s")],
         "edges": [{"source": "a", "target": "oa"}, {"source": "b", "target": "ob"}]}
    run_pipeline(client, engineer, "many", cleaned(g, ["id"]))
    assert {dataset(client, engineer, "silver", n)["row_count"] for n in ("n1_s", "n2_s")} == {1}


def test_55_nested_data_becomes_relational_tables(client, engineer):
    out = batch(client, engineer, [("cmd.yaml", YAML)])
    parent = next(x for x in out["results"] if x["entity"] == "orders")
    child = next(x for x in out["results"] if x["entity"] == "orders_items")
    to_silver(client, engineer, "p_orders", parent["dataset_id"], ["order_id"], target="orders")
    to_silver(client, engineer, "p_items", child["dataset_id"], ["order_id", "sku"], target="order_lines")
    assert any(r["join"] == "order_lines.order_id = orders.order_id" for r in analysis(client, engineer)["relationships"])


def test_56_flattening_preserves_relationships(client, engineer):
    by = {x["entity"]: x for x in batch(client, engineer, [("n.json", NESTED)])["results"]}
    items = rows(client, engineer, "bronze", by[next(k for k in by if k.endswith("items"))]["dataset"].split(".")[1])
    assert sorted((r["order_id"], r["sku"], r["qty"]) for r in items) == [(1, "A", 2), (1, "B", 1), (2, "A", 5)]


def test_57_unrelated_datasets_stay_separate(client, engineer):
    silver(client, engineer, "ventes", FLAT, ["order_id"])
    silver(client, engineer, "status_codes", LOOKUP, ["code"])
    a = analysis(client, engineer)
    assert not a["relationships"] and [s["table"] for s in a["standalone"]] == ["status_codes"]


def test_58_partially_valid_records(client, engineer):
    b = bronze(client, engineer, "part", "id,amount\n1,5\n2,-3\n3,8\n")
    graph = chain(node("in", "dataset_input", dataset_id=b["id"]), node("d", "dedupe", keys=["id"]),
                  node("q", "quality_check", rules=[{"type": "range", "column": "amount", "params": {"min": 0}, "on_failure": "quarantine"}]), node("out", "output", layer="silver", name="part_s"))
    run_pipeline(client, engineer, "partial", graph)
    assert sorted(r["id"] for r in rows(client, engineer, "silver", "part_s")) == [1, 3]


def test_59_invalid_records_are_quarantined(client, engineer):
    b = bronze(client, engineer, "quar", "id,amount\n1,5\n2,-3\n")
    graph = chain(node("in", "dataset_input", dataset_id=b["id"]), node("d", "dedupe", keys=["id"]),
                  node("q", "quality_check", rules=[{"type": "range", "column": "amount", "params": {"min": 0}, "on_failure": "quarantine"}]), node("out", "output", layer="silver", name="quar_s"))
    run = run_pipeline(client, engineer, "quar", graph)
    detail = client.get(f"{API}/runs/{run['id']}", headers=engineer).json()
    assert any("quarantine/" in line["msg"] for line in detail["logs"])


def test_60_source_metadata_and_lineage(client, engineer):
    b = bronze(client, engineer, "meta", "id,v\n1,a\n")
    to_silver(client, engineer, "pm", b["id"], ["id"])
    s = dataset(client, engineer, "silver", "pm_s")
    assert rows(client, engineer, "silver", "pm_s")[0]["_source_file"] == "meta.csv"
    assert client.get(f"{API}/datasets/{s['id']}/lineage", headers=engineer).json()["sources"][0]["qualified_name"] == "bronze.meta"


# ── 6. Silver → Gold ─────────────────────────────────────────────────────
def test_61_one_silver_to_one_gold(client, engineer):
    s = silver(client, engineer, "ventes", FLAT, ["order_id"])
    run_pipeline(client, engineer, "g61", chain(node("in", "dataset_input", dataset_id=s["id"]), node("out", "output", layer="gold", name="ventes_copy")))
    assert dataset(client, engineer, "gold", "ventes_copy")["row_count"] == 24


def test_62_several_silver_to_one_gold(client, engineer):
    a, b = silver(client, engineer, "customers", CUSTOMERS, ["customer_id"]), silver(client, engineer, "orders", ORDERS, ["order_id"])
    g = {"nodes": [node("a", "dataset_input", dataset_id=b["id"]), node("b", "dataset_input", dataset_id=a["id"]), node("j", "join", how="left", left_on=["customer_id"], right_on=["customer_id"]), node("out", "output", layer="gold", name="orders_wide")],
         "edges": [{"source": "a", "target": "j"}, {"source": "b", "target": "j"}, {"source": "j", "target": "out"}]}
    run_pipeline(client, engineer, "g62", g)
    assert "customer_name" in rows(client, engineer, "gold", "orders_wide")[0]


def test_63_several_silver_to_several_gold(client, engineer):
    shop(client, engineer)
    generate(client, engineer, analysis(client, engineer)["models"][0]["id"])
    assert len(gold(client, engineer)) >= 7


def test_64_fact_tables_are_identified(client, engineer):
    shop(client, engineer)
    assert {f["source_table"] for f in analysis(client, engineer)["models"][0]["facts"]} == {"orders", "order_items", "payments"}


def test_65_dimension_tables_are_identified(client, engineer):
    shop(client, engineer)
    a = analysis(client, engineer)
    assert {t["name"] for t in a["silver_tables"] if t["role"] == "dimension"} == {"customers", "products", "categories"}


def test_66_star_schema(client, engineer):
    shop(client, engineer)
    star = next(m for m in analysis(client, engineer)["models"] if m["schema_type"] == "star")
    generate(client, engineer, star["id"])
    assert not gold(client, engineer)["dim_products"]["relationships"]


def test_67_snowflake_schema(client, engineer):
    shop(client, engineer)
    snow = next(m for m in analysis(client, engineer)["models"] if m["schema_type"] == "snowflake")
    generate(client, engineer, snow["id"])
    g = gold(client, engineer)
    assert g["dim_products"]["relationships"][0]["dimension"] == "dim_categories" and -1 in {r["category_key"] for r in rows(client, engineer, "gold", "dim_products")}


def test_68_several_facts_share_dimensions(client, engineer):
    shop(client, engineer)
    star = analysis(client, engineer)["models"][0]
    assert star["constellation"] and {"dim_customers", "dim_time"} <= set(star["shared_dimensions"])


def test_69_fact_grain(client, engineer):
    shop(client, engineer)
    grains = {f["name"]: f["grain"] for f in analysis(client, engineer)["models"][0]["facts"]}
    assert grains["fact_order_items"] == "One row per order_id + product_id" and grains["fact_payments"] == "One row per payment_id"


def test_70_aggregation_and_kpis(client, engineer):
    shop(client, engineer)
    generate(client, engineer, analysis(client, engineer)["models"][0]["id"])
    kpis = rows(client, engineer, "gold", "mart_orders_kpis")
    assert round(sum(r["sum_total"] for r in kpis), 2) == 4240 and sum(r["row_count"] for r in kpis) == 5


def test_71_data_without_dimensional_modeling(client, engineer):
    silver(client, engineer, "status_codes", LOOKUP, ["code"])
    a = analysis(client, engineer)
    assert not a["models"] and a["standalone"][0]["table"] == "status_codes"


# ── 7. processing and reliability ────────────────────────────────────────
def rerun(client, h, pid):
    return client.post(f"{API}/pipelines/{pid}/run", headers=h).json()


def test_72_schema_evolution(client, engineer):
    load(client, engineer, "a.csv", "id,v\n1,a\n", "evo")
    b = dataset(client, engineer, "bronze", "evo")
    p = client.post(f"{API}/pipelines", headers=engineer, json={"name": "evo", "graph": cleaned(chain(node("in", "dataset_input", dataset_id=b["id"]), node("out", "output", layer="silver", name="evo_s")), ["id"])}).json()
    rerun(client, engineer, p["id"])
    load(client, engineer, "b.csv", "id,v,w\n2,b,c\n", "evo", mode="append")
    assert rerun(client, engineer, p["id"])["status"] == "succeeded"
    s = client.get(f"{API}/datasets/{dataset(client, engineer, 'silver', 'evo_s')['id']}", headers=engineer).json()
    assert s["versions"][0]["schema_changes"]["added"] == ["w"]


def upsert_graph(ds_id):
    return cleaned(chain(node("in", "dataset_input", dataset_id=ds_id), node("out", "output", layer="silver", name="up_s", mode="upsert", keys=["id"])), ["id"])


def test_73_incremental_updates(client, engineer):
    load(client, engineer, "a.csv", "id,v\n1,a\n2,b\n", "up")
    b = dataset(client, engineer, "bronze", "up")
    p = client.post(f"{API}/pipelines", headers=engineer, json={"name": "up", "graph": upsert_graph(b["id"])}).json()
    rerun(client, engineer, p["id"])
    load(client, engineer, "b.csv", "id,v\n3,c\n", "up")
    rerun(client, engineer, p["id"])
    assert sorted(r["id"] for r in rows(client, engineer, "silver", "up_s")) == [1, 2, 3]


def test_74_new_and_updated_records(client, engineer):
    load(client, engineer, "a.csv", "id,v\n1,a\n2,b\n", "up")
    b = dataset(client, engineer, "bronze", "up")
    p = client.post(f"{API}/pipelines", headers=engineer, json={"name": "up", "graph": upsert_graph(b["id"])}).json()
    rerun(client, engineer, p["id"])
    load(client, engineer, "b.csv", "id,v\n2,CHANGED\n3,new\n", "up")
    rerun(client, engineer, p["id"])
    assert {r["id"]: r["v"] for r in rows(client, engineer, "silver", "up_s")} == {1: "a", 2: "CHANGED", 3: "new"}


def test_75_idempotent_processing(client, engineer):
    b = bronze(client, engineer, "idem", "id,v\n1,a\n2,b\n")
    run = to_silver(client, engineer, "idem", b["id"], ["id"])
    content = lambda: sorted((r["id"], r["v"]) for r in rows(client, engineer, "silver", "idem_s"))
    first = content()
    assert rerun(client, engineer, run["pipeline_id"])["status"] == "succeeded"
    assert content() == first


def test_76_failure_and_recovery(client, engineer):
    load(client, engineer, "a.csv", "id,v\n1,a\n", "rec")
    b = dataset(client, engineer, "bronze", "rec")
    graph = chain(node("in", "dataset_input", dataset_id=b["id"]), node("d", "dedupe", keys=["id"]), node("q", "quality_check", rules=[{"type": "not_null", "column": "v", "on_failure": "fail"}]), node("out", "output", layer="silver", name="rec_s"))
    p = client.post(f"{API}/pipelines", headers=engineer, json={"name": "rec", "graph": graph}).json()
    assert rerun(client, engineer, p["id"])["status"] == "succeeded"
    load(client, engineer, "b.csv", "id,v\n1,\n2,x\n", "rec")
    assert rerun(client, engineer, p["id"])["status"] == "failed"
    assert dataset(client, engineer, "silver", "rec_s")["current_version"] == 1  # the last good version stays in place
    load(client, engineer, "c.csv", "id,v\n1,a\n2,x\n", "rec")
    assert rerun(client, engineer, p["id"])["status"] == "succeeded"
    assert dataset(client, engineer, "silver", "rec_s")["row_count"] == 2


def test_77_partial_batch_failure(client, engineer):
    out = batch(client, engineer, [("good.csv", "id,v\n1,a\n"), ("bad.xlsx", b"broken"), ("empty.csv", b""), ("also_good.csv", "id,v\n2,b\n")])
    assert out["summary"] == {"ingested": 2, "skipped": 0, "failed": 2}
    assert dataset(client, engineer, "bronze", "also_good")["row_count"] == 1


def test_78_rejected_records_are_tracked(client, engineer):
    r = upload(client, engineer, "rej.csv", "id,v\n1,a\n2,b,EXTRA\n").json()
    assert r["quarantined_count"] == 1 and r["quarantine_location"].endswith("quarantine.jsonl")
    assert any("set aside" in w for w in r["warnings"])


def test_79_quality_before_and_after_transformation(client, engineer):
    b = bronze(client, engineer, "qual", "id,v\n1,a\n1,a\n2,\n")
    graph = chain(node("in", "dataset_input", dataset_id=b["id"]), node("before", "quality_check", rules=[{"type": "unique", "column": "id", "on_failure": "warn"}]), node("d", "dedupe", keys=["id"]),
                  node("after", "quality_check", rules=[{"type": "unique", "column": "id", "on_failure": "fail"}]), node("out", "output", layer="silver", name="qual_s"))
    run = run_pipeline(client, engineer, "qual", graph)
    status = client.get(f"{API}/runs/{run['id']}", headers=engineer).json()["node_status"]
    assert status["before"]["quality"][0]["passed"] is False and status["after"]["quality"][0]["passed"] is True


def test_80_lineage_and_transformation_history(client, engineer):
    shop(client, engineer)
    generate(client, engineer, analysis(client, engineer)["models"][0]["id"])
    fact = gold(client, engineer)["fact_order_items"]
    lineage = client.get(f"{API}/datasets/{fact['id']}/lineage", headers=engineer).json()
    assert {"silver.order_items", "silver.orders"} <= {s["qualified_name"] for s in lineage["sources"]} and lineage["pipeline_run_id"]
    assert client.get(f"{API}/runs/{lineage['pipeline_run_id']}", headers=engineer).json()["status"] == "succeeded"
