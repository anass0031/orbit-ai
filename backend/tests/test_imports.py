"""File import → schema discovery → Bronze layer (Parquet in object storage)."""
import io
import json

import pyarrow.parquet as pq
from openpyxl import Workbook

from app.lakehouse.storage import get_storage

ORDERS_CSV = (
    "Order ID;Customer;Zip Code;Amount;Order Date;Paid\n"
    "1;Alice;01000;12,50;05/01/2024;true\n"
    "2;Bob;75001;7;25/12/2024;false\n"
    "3;Chloé;;3,10;01/02/2024;TRUE\n"
    "4;Dan;13000;8,00;01/02/2024;false;EXTRA\n"
).encode()


def upload(client, headers, name, data, **form):
    return client.post("/api/v1/imports", headers=headers, files={"file": (name, data)}, data=form)


def test_csv_upload_discovers_schema(client, as_engineer):
    r = upload(client, as_engineer, "orders.csv", ORDERS_CSV)
    assert r.status_code == 201, r.text
    imp = r.json()
    assert imp["status"] == "discovered"
    assert imp["file_format"] == "csv"
    assert imp["options"]["delimiter"] == ";"  # detected automatically
    assert imp["row_count"] == 3 and imp["quarantined_count"] == 1
    types = {c["name"]: c["type"] for c in imp["schema"]}
    assert types == {"order_id": "integer", "customer": "string", "zip_code": "string", "amount": "double",
                     "order_date": "date", "paid": "boolean"}
    zip_col = next(c for c in imp["schema"] if c["name"] == "zip_code")
    assert zip_col["source_name"] == "Zip Code" and zip_col["null_count"] == 1
    assert imp["inferred_primary_key"] == ["order_id"]
    assert imp["sample_rows"][0] == {"order_id": 1, "customer": "Alice", "zip_code": "01000", "amount": 12.5,
                                     "order_date": "2024-01-05", "paid": True}
    assert any("leading zeros" in w for w in imp["warnings"])
    assert imp["suggested_dataset_name"] == "orders"
    # the original file is kept unchanged in the landing zone, the rejected line in quarantine
    st = get_storage()
    assert st.get_bytes(imp["raw_location"]) == ORDERS_CSV
    assert "EXTRA" in st.get_bytes(imp["quarantine_location"]).decode()


def test_ingest_writes_versioned_bronze_parquet(client, as_engineer, as_analyst):
    imp = upload(client, as_engineer, "orders.csv", ORDERS_CSV).json()
    r = client.post(f"/api/v1/imports/{imp['id']}/ingest", headers=as_engineer,
                    json={"dataset_name": "orders", "description": "Shop orders"})
    assert r.status_code == 201, r.text
    ds, v = r.json()["dataset"], r.json()["version"]
    assert ds["qualified_name"] == "bronze.orders" and ds["current_version"] == 1
    assert v["location"].startswith("bronze/orders/v1/") and v["location"].endswith(".parquet")
    assert v["row_count"] == 3

    table = pq.read_table(io.BytesIO(get_storage().get_bytes(v["location"])))
    assert table.column_names == ["order_id", "customer", "zip_code", "amount", "order_date", "paid",
                                  "_ingested_at", "_source_file", "_import_id"]
    assert str(table.schema.field("order_date").type) == "date32[day]"
    assert table.column("_import_id").to_pylist() == [imp["id"]] * 3

    # import is now linked to the dataset and cannot be ingested twice
    again = client.post(f"/api/v1/imports/{imp['id']}/ingest", headers=as_engineer, json={"dataset_name": "orders"})
    assert again.status_code == 409
    assert client.get(f"/api/v1/imports/{imp['id']}", headers=as_analyst).json()["status"] == "ingested"

    # a second file into the same dataset → version 2, version 1 untouched
    csv2 = b"Order ID;Customer;Amount;Channel\n10;Zoe;5,5;web\n"
    imp2 = upload(client, as_engineer, "orders_2.csv", csv2).json()
    r2 = client.post(f"/api/v1/imports/{imp2['id']}/ingest", headers=as_engineer, json={"dataset_name": "orders"})
    assert r2.status_code == 201
    v2 = r2.json()["version"]
    assert v2["version"] == 2
    assert v2["schema_changes"]["added"] == ["channel"]
    assert set(v2["schema_changes"]["removed"]) == {"zip_code", "order_date", "paid"}
    assert get_storage().exists(v["location"])

    detail = client.get(f"/api/v1/datasets/{ds['id']}", headers=as_analyst).json()
    assert [x["version"] for x in detail["versions"]] == [2, 1]
    prev = client.get(f"/api/v1/datasets/{ds['id']}/preview?version=1&limit=2", headers=as_analyst).json()
    assert prev["version"] == 1 and prev["total_rows"] == 3 and len(prev["rows"]) == 2
    assert prev["rows"][0]["zip_code"] == "01000"

    listing = client.get("/api/v1/datasets?layer=bronze", headers=as_analyst).json()
    assert [d["name"] for d in listing] == ["orders"]
    stats = client.get("/api/v1/overview", headers=as_analyst).json()
    assert stats["bronze_datasets"] == 1 and stats["imports_by_status"]["ingested"] == 2


def test_excel_upload_with_several_sheets(client, as_engineer):
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append(["note"])
    ws.append(["see other sheet"])
    ws2 = wb.create_sheet("Products")
    ws2.append(["SKU", "Price", "In stock", "Added"])
    ws2.append(["A-1", 9.99, True, "2024-03-01"])
    ws2.append(["A-2", 15, False, "2024-03-02"])
    buf = io.BytesIO()
    wb.save(buf)

    imp = upload(client, as_engineer, "catalog.xlsx", buf.getvalue()).json()
    assert imp["status"] == "discovered" and imp["file_format"] == "excel"
    assert imp["sheets"] == ["Summary", "Products"] and imp["options"]["sheet_name"] == "Summary"

    r = client.post(f"/api/v1/imports/{imp['id']}/discover", headers=as_engineer, json={"sheet_name": "Products"})
    assert r.status_code == 200
    types = {c["name"]: c["type"] for c in r.json()["schema"]}
    assert types == {"sku": "string", "price": "double", "in_stock": "boolean", "added": "date"}
    ing = client.post(f"/api/v1/imports/{imp['id']}/ingest", headers=as_engineer, json={"dataset_name": "products"})
    assert ing.status_code == 201 and ing.json()["version"]["row_count"] == 2


def test_json_nested_and_json_lines_with_bad_line(client, as_engineer):
    doc = {"data": [{"id": "c1", "customer": {"city": "Rabat"}, "tags": ["a"], "total": 10},
                    {"id": "c2", "customer": {"city": "Fes"}, "tags": [], "total": 2.5}]}
    imp = upload(client, as_engineer, "carts.json", json.dumps(doc).encode()).json()
    assert imp["status"] == "discovered"
    types = {c["name"]: c["type"] for c in imp["schema"]}
    assert types == {"id": "string", "tags": "string", "total": "double", "customer_city": "string"}
    assert imp["inferred_primary_key"] == ["id"]

    lines = b'{"event":"click","at":"2024-05-01T10:00:00Z"}\nnot-json\n{"event":"view","at":"2024-05-01T10:05:00Z"}\n'
    imp2 = upload(client, as_engineer, "events.jsonl", lines).json()
    assert imp2["row_count"] == 2 and imp2["quarantined_count"] == 1
    assert {c["name"]: c["type"] for c in imp2["schema"]} == {"event": "string", "at": "timestamp"}


def test_rejected_files(client, as_engineer):
    r = upload(client, as_engineer, "notes.pdf", b"%PDF-1.4")
    assert r.status_code == 422 and "Supported extensions" in r.json()["error"]["message"]
    r = upload(client, as_engineer, "empty.csv", b"")
    assert r.status_code == 422

    # a file that cannot be parsed is recorded as failed, with the reason
    r = upload(client, as_engineer, "broken.json", b"[1, 2,")
    assert r.status_code == 201 and r.json()["status"] == "failed"
    assert "Invalid JSON" in r.json()["error_message"]
    ing = client.post(f"/api/v1/imports/{r.json()['id']}/ingest", headers=as_engineer, json={"dataset_name": "x"})
    assert ing.status_code == 409


def test_options_and_dataset_name_validation(client, as_engineer):
    imp = upload(client, as_engineer, "pipes.csv", b"a|b\n1|2\n3|4\n", delimiter=",").json()
    assert [c["name"] for c in imp["schema"]] == ["a_b"]  # wrong delimiter forced by the user
    fixed = client.post(f"/api/v1/imports/{imp['id']}/discover", headers=as_engineer, json={"delimiter": "|"}).json()
    assert [c["name"] for c in fixed["schema"]] == ["a", "b"]

    no_header = client.post(f"/api/v1/imports/{imp['id']}/discover", headers=as_engineer,
                            json={"delimiter": "|", "has_header": False}).json()
    assert [c["name"] for c in no_header["schema"]] == ["column_1", "column_2"] and no_header["row_count"] == 3

    bad = client.post(f"/api/v1/imports/{imp['id']}/ingest", headers=as_engineer, json={"dataset_name": "Bad Name!"})
    assert bad.status_code == 422


def test_upload_is_audited(client, as_engineer, as_admin):
    imp = upload(client, as_engineer, "a.csv", b"id\n1\n").json()
    client.post(f"/api/v1/imports/{imp['id']}/ingest", headers=as_engineer, json={"dataset_name": "a"})
    items = client.get("/api/v1/audit", headers=as_admin).json()["items"]
    mine = [(e["action"], e["actor_email"]) for e in items if e["action"] in ("import.upload", "dataset.write")]
    assert ("import.upload", "engineer@orbit.local") in mine and ("dataset.write", "engineer@orbit.local") in mine
