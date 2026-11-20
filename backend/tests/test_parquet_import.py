"""File import — Parquet files keep their native types, from the upload to the Bronze layer."""
import datetime as dt
import io
from decimal import Decimal

import pyarrow as pa
import pyarrow.parquet as pq

from app.core.config import get_settings
from app.lakehouse import catalog
from app.models import Dataset


def parquet_bytes(table: pa.Table) -> bytes:
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def sample() -> pa.Table:
    return pa.table({
        "SaleId": pa.array([1, 2, 3], pa.int64()),
        "amount": pa.array([Decimal("10.50"), Decimal("20.25"), None], pa.decimal128(10, 2)),
        "sold_on": pa.array([dt.date(2026, 1, 5), dt.date(2026, 2, 6), dt.date(2026, 3, 7)], pa.date32()),
        "paid_at": pa.array([dt.datetime(2026, 1, 5, 10, 30), None, dt.datetime(2026, 3, 7, 8, 0)], pa.timestamp("us")),
        "paid": pa.array([True, False, True], pa.bool_()),
        "customer": pa.array([{"name": "Ana", "address": {"city": "Paris"}}, {"name": "Ben", "address": {"city": "Lyon"}}, None],
                             pa.struct([("name", pa.string()), ("address", pa.struct([("city", pa.string())]))])),
        "items": pa.array([["a", "b"], [], None], pa.list_(pa.string())),
    })


def upload(client, h, data, name="sales.parquet"):
    r = client.post("/api/v1/imports", headers=h, files={"file": (name, data)})
    assert r.status_code == 201, r.text
    return r.json()


def test_native_types_are_kept_up_to_bronze(client, engineer, db):
    imp = upload(client, engineer, parquet_bytes(sample()))
    assert imp["status"] == "discovered" and imp["file_format"] == "parquet" and imp["row_count"] == 3
    cols = {c["name"]: c for c in imp["schema"]}
    assert {k: cols[k]["type"] for k in ("sale_id", "amount", "sold_on", "paid_at", "paid")} == {
        "sale_id": "integer", "amount": "double", "sold_on": "date", "paid_at": "timestamp", "paid": "boolean"}
    assert cols["amount"]["null_count"] == 1 and cols["sale_id"]["source_name"] == "SaleId"
    assert imp["inferred_primary_key"] == ["sale_id"]

    r = client.post(f"/api/v1/imports/{imp['id']}/ingest", headers=engineer, json={"dataset_name": "parquet_sales"})
    assert r.status_code == 201, r.text
    ds = db.get(Dataset, r.json()["dataset"]["id"])
    t = catalog.read_table(catalog.get_version(db, ds, None))
    assert t.schema.field("sale_id").type == pa.int64() and t.schema.field("amount").type == pa.decimal128(10, 2)
    assert t.schema.field("sold_on").type == pa.date32() and t.schema.field("paid_at").type == pa.timestamp("us")
    assert t.schema.field("paid").type == pa.bool_()
    assert t.column("amount").to_pylist()[0] == Decimal("10.50")
    assert {"_ingested_at", "_source_file", "_import_id"} <= set(t.column_names)
    assert t.column("_source_file").to_pylist() == ["sales.parquet"] * 3
    profile = client.get(f"/api/v1/datasets/{ds.id}/profile", headers=engineer).json()
    assert next(c for c in profile["columns"] if c["name"] == "sale_id")["unique"] is True


def test_nested_columns(client, engineer, db):
    imp = upload(client, engineer, parquet_bytes(sample()))
    names = [c["name"] for c in imp["schema"]]
    assert {"customer_name", "customer_address_city", "items"} <= set(names) and "customer" not in names
    assert any("flattened" in w for w in imp["warnings"]) and any("JSON text" in w for w in imp["warnings"])
    r = client.post(f"/api/v1/imports/{imp['id']}/ingest", headers=engineer, json={"dataset_name": "parquet_nested"})
    t = catalog.read_table(catalog.get_version(db, db.get(Dataset, r.json()["dataset"]["id"]), None))
    assert t.column("customer_address_city").to_pylist() == ["Paris", "Lyon", None]
    assert t.column("items").to_pylist() == ['["a", "b"]', "[]", None] and t.schema.field("items").type == pa.string()


def test_corrupt_file_fails_clearly(client, engineer):
    good = parquet_bytes(sample())
    for data in (b"this is not parquet at all", good[:len(good) // 2]):
        imp = upload(client, engineer, data, "broken.parquet")
        assert imp["status"] == "failed" and "cannot be read" in imp["error_message"]
    r = client.post(f"/api/v1/imports/{imp['id']}/ingest", headers=engineer, json={"dataset_name": "broken"})
    assert r.status_code == 409


def test_empty_file_fails_clearly(client, engineer):
    no_rows = upload(client, engineer, parquet_bytes(sample().slice(0, 0)), "empty.parquet")
    assert no_rows["status"] == "failed" and no_rows["error_message"] == "The Parquet file has no data rows"
    zero = upload(client, engineer, b"", "zero.parquet")
    assert zero["status"] == "failed" and zero["error_message"] == "The file is empty"


def test_size_limit_applies(client, engineer, monkeypatch):
    monkeypatch.setattr(get_settings(), "upload_max_mb", 0)
    r = client.post("/api/v1/imports", headers=engineer, files={"file": ("big.parquet", parquet_bytes(sample()))})
    assert r.status_code == 422 and "limit" in r.json()["error"]["message"]


def test_file_drop_uses_the_same_reader(client, engineer, db):
    folder = "parquet-drop"
    client.post(f"/api/v1/pipelines/dropzone/{folder}", headers=engineer, files={"file": ("a.parquet", parquet_bytes(sample()))})
    graph = {"nodes": [{"id": "in", "type": "file_drop_input", "config": {"folder": folder}},
                       {"id": "d", "type": "dedupe", "config": {"keys": ["sale_id"]}},
                       {"id": "q", "type": "quality_check", "config": {"rules": [{"type": "not_null", "column": "sale_id", "on_failure": "fail"}]}},
                       {"id": "o", "type": "output", "config": {"layer": "silver", "name": "parquet_dropped"}}],
             "edges": [{"source": "in", "target": "d"}, {"source": "d", "target": "q"}, {"source": "q", "target": "o"}]}
    p = client.post("/api/v1/pipelines", headers=engineer, json={"name": "parquet drop", "graph": graph}).json()
    run = client.post(f"/api/v1/pipelines/{p['id']}/run", headers=engineer)
    assert client.get(f"/api/v1/runs/{run.json()['id']}", headers=engineer).json()["status"] == "succeeded", run.text
    ds = next(d for d in client.get("/api/v1/datasets?layer=silver", headers=engineer).json() if d["name"] == "parquet_dropped")
    t = catalog.read_table(catalog.get_version(db, db.get(Dataset, ds["id"]), None))
    assert "customer_address_city" in t.column_names and t.schema.field("amount").type == pa.decimal128(10, 2)
