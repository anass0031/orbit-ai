"""Catalogue tools — dataset exploration & documentation, glossary, search."""
import pytest

from tests.conftest import bronze


# ── exploration & documentation ──────────────────────────────────────────
def test_profile_and_version_diff(client, analyst, engineer):
    v1 = "id,city,amount,day\n1,Paris,10,2026-01-01\n2,Lyon,20,2026-01-02\n3,Nice,,2026-02-01\n4,Paris,40,2026-02-03\n"
    b = bronze(client, engineer, "shops", v1)
    p = client.get(f"/api/v1/datasets/{b['id']}/profile", headers=analyst).json()
    cols = {c["name"]: c for c in p["columns"]}
    assert p["rows"] == 4 and cols["amount"]["nulls"] == 1 and cols["amount"]["mean"] == pytest.approx(70 / 3)
    assert sum(h["count"] for h in cols["amount"]["histogram"]) == 3 and cols["id"]["unique"]
    assert cols["city"]["top_values"][0] == {"value": "Paris", "count": 2, "pct": 50.0}
    assert [m["month"] for m in cols["day"]["by_month"]] == ["2026-01", "2026-02"]
    bronze(client, engineer, "shops", "id,city,amount,day,channel\n1,Paris,10,2026-01-01,web\n2,Lyon,25,2026-01-02,shop\n5,Lille,50,2026-03-01,web\n")
    d = client.get(f"/api/v1/datasets/{b['id']}/diff?from=1&to=2&keys=id", headers=analyst).json()
    assert d["counts"] == {"added": 1, "removed": 2, "changed": 1, "unchanged": 1} and d["changed_columns"] == {"amount": 1}
    assert d["schema"]["added"] == ["channel"] and d["samples"]["added"][0]["id"] == 5
    nk = client.get(f"/api/v1/datasets/{b['id']}/diff?from=1&to=2", headers=analyst).json()
    assert nk["mode"] == "rows" and nk["counts"]["added"] == 2 and nk["counts"]["removed"] == 3


def test_documentation_glossary_and_search(client, analyst, engineer, sales):
    g = sales["gold"]
    r = client.put(f"/api/v1/datasets/{g['id']}/documentation", headers=analyst, json={
        "description": "Monthly revenue per sales region", "tags": ["Finance", "kpi"],
        "columns": {"revenue": "Net revenue in EUR, after discounts", "orders": "Number of completed orders"}})
    assert r.status_code == 200 and r.json()["tags"] == ["finance", "kpi"] and r.json()["column_docs"]["revenue"].startswith("Net revenue")
    assert client.put(f"/api/v1/datasets/{g['id']}/documentation", headers=analyst, json={"columns": {"nope": "x"}}).status_code == 422
    assert [d["id"] for d in client.get("/api/v1/datasets?tag=finance", headers=engineer).json()] == [g["id"]]

    t = client.post("/api/v1/glossary", headers=analyst, json={
        "name": "Net revenue", "definition": "Revenue after discounts and refunds.", "synonyms": ["turnover"],
        "links": [{"dataset": g["qualified_name"], "column": "revenue"}]})
    assert t.status_code == 201, t.text
    assert client.post("/api/v1/glossary", headers=analyst, json={"name": "net REVENUE", "definition": "dup"}).status_code == 409
    assert client.post("/api/v1/glossary", headers=analyst, json={"name": "X", "definition": "y", "links": [{"dataset": "gold.nope"}]}).status_code == 422
    s = client.get("/api/v1/datasets/search/all?q=discounts", headers=analyst).json()
    assert s["datasets"][0]["id"] == g["id"] and "column docs" in s["datasets"][0]["matched"] and s["terms"][0]["name"] == "Net revenue"
