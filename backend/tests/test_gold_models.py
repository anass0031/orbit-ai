"""Gold model advisor and business views, from real Silver tables to readable charts."""
from tests.conftest import bronze, cleaned, run_pipeline

CUSTOMERS = "customer_id,customer_name,city,segment\n1,Alice Martin,Paris,Retail\n2,Bob Durand,Lyon,Retail\n3,Sara Benali,Rabat,Corporate\n4,Driss Alaoui,Rabat,Corporate\n"
CATEGORIES = "category_id,category_name\n10,Laptops\n20,Phones\n"
PRODUCTS = "product_id,product_name,category_id,list_price\n100,Atlas 14,10,900\n101,Atlas 16,10,1200\n200,Nova X,20,650\n201,Nova S,20,420\n"
ORDERS = ("order_id,customer_id,order_date,total\n1000,1,2026-01-05,900\n1001,2,2026-01-20,1200\n1002,3,2026-02-03,650\n1003,1,2026-02-14,1070\n"
          "1004,9,2026-03-01,420\n")
ITEMS = ("order_id,product_id,quantity,unit_price\n1000,100,1,900\n1001,101,1,1200\n1002,200,1,650\n1003,200,1,650\n1003,201,1,420\n1004,201,1,420\n")
PAYMENTS = "payment_id,order_id,paid_on,paid_amount\n5000,1000,2026-01-06,900\n5001,1001,2026-01-21,1200\n5002,1002,2026-02-04,650\n"
FLAT = ("order_id,client,ville,pays,order_day,montant\n" + "".join(
    f"{i},{['Alice','Bob','Sara','Driss','Emma'][i % 5]},{['Paris','Lyon','Rabat','Casablanca'][i % 4]},{['France','France','Maroc','Maroc'][i % 4]},2026-0{i % 3 + 1}-1{i % 9},{10 + i}\n" for i in range(1, 25)))
LOOKUP = "code,meaning\nA,Active\nB,Blocked\nC,Closed\n"


def silver(client, h, name: str, csv: str, keys: list[str]) -> dict:
    b = bronze(client, h, f"raw_{name}", csv)
    run_pipeline(client, h, f"silver {name}", cleaned({"nodes": [{"id": "in", "type": "dataset_input", "config": {"dataset_id": b["id"]}},
                                                                 {"id": "out", "type": "output", "config": {"layer": "silver", "name": name}}],
                                                      "edges": [{"source": "in", "target": "out"}]}, keys))
    return next(d for d in client.get("/api/v1/datasets?layer=silver", headers=h).json() if d["name"] == name)


def shop(client, h):
    silver(client, h, "customers", CUSTOMERS, ["customer_id"])
    silver(client, h, "categories", CATEGORIES, ["category_id"])
    silver(client, h, "products", PRODUCTS, ["product_id"])
    silver(client, h, "orders", ORDERS, ["order_id"])
    silver(client, h, "order_items", ITEMS, ["order_id", "product_id"])
    silver(client, h, "payments", PAYMENTS, ["payment_id"])


def analysis(client, h) -> dict:
    r = client.get("/api/v1/gold-models/analysis", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def gold(client, h) -> dict:
    return {d["name"]: d for d in client.get("/api/v1/datasets?layer=gold", headers=h).json()}


def generate(client, h, model_id: str, **selection) -> dict:
    r = client.post("/api/v1/gold-models/generate", headers=h, json={"model_id": model_id, "selection": selection})
    assert r.status_code == 201, r.text
    assert r.json()["run"]["status"] == "succeeded", r.json()
    return r.json()


def test_analysis_finds_facts_dimensions_relationships_and_grain(client, engineer):
    shop(client, engineer)
    a = analysis(client, engineer)
    roles = {t["name"]: t["role"] for t in a["silver_tables"]}
    assert roles == {"customers": "dimension", "categories": "dimension", "products": "dimension", "orders": "fact", "order_items": "fact", "payments": "fact"}
    grains = {t["name"]: t["primary_key"] for t in a["silver_tables"]}
    assert grains["order_items"] == ["order_id", "product_id"] and grains["orders"] == ["order_id"]
    joins = {r["join"]: r for r in a["relationships"]}
    assert joins["orders.customer_id = customers.customer_id"]["orphan_rows"] == 1
    assert "products.category_id = categories.category_id" in joins and "order_items.order_id = orders.order_id" in joins
    assert [m["schema_type"] for m in a["models"]] == ["star", "snowflake"]
    star = a["models"][0]
    assert star["constellation"] and {f["name"] for f in star["facts"]} == {"fact_orders", "fact_order_items", "fact_payments"}
    assert "dim_time" in star["shared_dimensions"] and "dim_customers" in star["shared_dimensions"]


def test_star_model_is_generated_with_readable_business_views(client, engineer):
    shop(client, engineer)
    star = analysis(client, engineer)["models"][0]
    generate(client, engineer, star["id"])
    g = gold(client, engineer)
    assert {"fact_orders", "fact_order_items", "fact_payments", "dim_customers", "dim_products", "dim_time"} <= set(g)
    assert "dim_categories" not in g  # star: the category is folded into the product dimension
    assert g["fact_order_items"]["primary_key"] == ["order_id", "product_id"] and g["fact_order_items"]["row_count"] == 6
    products = client.get(f"/api/v1/datasets/{g['dim_products']['id']}/preview?view=business&limit=20", headers=engineer).json()
    assert "category_name" in products["columns"] and "product_key" not in products["columns"]
    rows = client.get(f"/api/v1/datasets/{g['fact_orders']['id']}/preview?view=business&limit=20", headers=engineer).json()
    assert "customer_key" not in rows["columns"] and "customer" in rows["columns"]
    names = {r["order_id"]: r["customer"] for r in rows["rows"]}
    assert names[1000] == "Alice Martin" and names[1004] == "Unknown"  # customer 9 does not exist
    items = client.get(f"/api/v1/datasets/{g['fact_order_items']['id']}/preview?view=business&limit=20", headers=engineer).json()
    assert {r["customer"] for r in items["rows"]} >= {"Alice Martin", "Bob Durand"}  # inherited through the order header
    charts = client.get(f"/api/v1/datasets/{g['fact_orders']['id']}/charts?measure=total", headers=engineer).json()
    by_customer = next(c for c in charts["charts"] if c["id"] == "by_customer")
    assert by_customer["points"][0] == {"label": "Alice Martin", "value": 1970.0}
    assert all(not str(p["label"]).lstrip("-").isdigit() for c in charts["charts"] for p in c["points"])
    assert any(c["type"] == "line" for c in charts["charts"])
    assert sum(p["value"] for p in by_customer["points"]) == 4240.0
    profile = client.get(f"/api/v1/datasets/{g['fact_orders']['id']}/profile", headers=engineer).json()
    key = next(c for c in profile["columns"] if c["name"] == "customer_key")
    assert key["role"] == "foreign_key" and key["display_name"] == "customer" and "mean" not in key and "histogram" not in key
    assert key["top_values"][0]["value"] == "Alice Martin"
    mart = client.get(f"/api/v1/datasets/{g['mart_orders_kpis']['id']}/preview", headers=engineer).json()
    assert "customer_name" in mart["columns"] and "sum_total" in mart["columns"]


def test_snowflake_keeps_the_parent_dimension(client, engineer):
    shop(client, engineer)
    snow = next(m for m in analysis(client, engineer)["models"] if m["schema_type"] == "snowflake")
    generate(client, engineer, snow["id"])
    g = gold(client, engineer)
    assert g["dim_products"]["relationships"][0]["dimension"] == "dim_categories"
    rows = client.get(f"/api/v1/datasets/{g['fact_order_items']['id']}/preview?view=business&limit=20", headers=engineer).json()
    assert {r["category"] for r in rows["rows"]} == {"Laptops", "Phones"}
    charts = client.get(f"/api/v1/datasets/{g['fact_order_items']['id']}/charts", headers=engineer).json()
    assert any(c["id"] == "by_category" for c in charts["charts"])


def test_selection_measures_attributes_and_preview(client, engineer):
    shop(client, engineer)
    star = analysis(client, engineer)["models"][0]
    selection = {"facts": ["fact_orders"], "dimensions": ["dim_customers"], "measures": {"fact_orders": ["total"]}, "attributes": {"dim_customers": ["customer_name"]}, "marts": False}
    p = client.post("/api/v1/gold-models/preview", headers=engineer, json={"model_id": star["id"], "selection": selection}).json()
    assert p["valid"], p["issues"]
    assert {t["name"] for t in p["tables"]} == {"fact_orders", "dim_customers"}
    assert not gold(client, engineer)  # a preview writes nothing
    fact = next(t for t in p["tables"] if t["name"] == "fact_orders")
    assert [c["name"] for c in fact["columns"]] == ["order_id", "customer_key", "total"] and len(fact["sample"]) == 5
    generate(client, engineer, star["id"], **selection)
    g = gold(client, engineer)
    assert set(g) == {"fact_orders", "dim_customers"}
    assert {c["name"] for c in g["dim_customers"]["schema"]} == {"customer_key", "customer_id", "customer_name"}
    bad = client.post("/api/v1/gold-models/preview", headers=engineer, json={"model_id": star["id"], "selection": {"facts": ["fact_nothing"]}})
    assert bad.status_code == 422


def test_single_flat_table_gives_a_star_with_embedded_hierarchy(client, engineer):
    silver(client, engineer, "ventes", FLAT, ["order_id"])
    a = analysis(client, engineer)
    assert {t["name"]: t["role"] for t in a["silver_tables"]} == {"ventes": "fact"}
    assert {"table": "ventes", "determinant": "ville", "dependent": "pays"} in a["functional_dependencies"]
    types = {m["schema_type"]: m for m in a["models"]}
    assert set(types) == {"star", "snowflake"}
    generate(client, engineer, types["star"]["id"])
    g = gold(client, engineer)
    assert {"fact_ventes", "dim_client", "dim_ville", "dim_time"} <= set(g) and "dim_pays" not in g
    assert "pays" in {c["name"] for c in g["dim_ville"]["schema"]}
    chart = client.get(f"/api/v1/datasets/{g['fact_ventes']['id']}/charts", headers=engineer).json()
    assert {p["label"] for p in next(c for c in chart["charts"] if c["id"] == "by_ville")["points"]} == {"Paris", "Lyon", "Rabat", "Casablanca"}
    generate(client, engineer, types["snowflake"]["id"])
    g = gold(client, engineer)
    assert g["dim_ville"]["relationships"][0]["dimension"] == "dim_pays"


def test_independent_models_and_tables_without_a_model(client, engineer):
    silver(client, engineer, "ventes", FLAT, ["order_id"])
    silver(client, engineer, "tickets", "ticket_id,agent,opened_on,minutes\n1,Lina,2026-01-02,30\n2,Omar,2026-01-03,45\n3,Lina,2026-01-04,20\n4,Omar,2026-01-05,10\n", ["ticket_id"])
    silver(client, engineer, "status_codes", LOOKUP, ["code"])
    a = analysis(client, engineer)
    assert len({m["id"].split("_")[0] for m in a["models"]}) == 2
    assert [s["table"] for s in a["standalone"]] == ["status_codes"]
    for m in [x for x in a["models"] if x["schema_type"] == "star"]:
        generate(client, engineer, m["id"])
    g = gold(client, engineer)
    assert {"fact_ventes", "fact_tickets", "dim_agent", "dim_client"} <= set(g)
    assert "status_codes" not in " ".join(g)


def test_generation_is_repeatable(client, engineer):
    shop(client, engineer)
    star = analysis(client, engineer)["models"][0]
    first = generate(client, engineer, star["id"])
    g1 = gold(client, engineer)
    def content(d):
        return sorted(client.get(f"/api/v1/datasets/{d['id']}/preview?limit=50", headers=engineer).json()["rows"], key=lambda r: r["order_id"])

    before = content(g1["fact_orders"])
    second = generate(client, engineer, star["id"])
    assert first["pipeline_id"] == second["pipeline_id"]
    g2 = gold(client, engineer)
    assert g2["fact_orders"]["current_version"] == 2 and g2["fact_orders"]["row_count"] == g1["fact_orders"]["row_count"]
    assert content(g2["fact_orders"]) == before


def test_analyst_can_read_the_analysis_but_not_generate(client, engineer, analyst):
    silver(client, engineer, "ventes", FLAT, ["order_id"])
    a = analysis(client, analyst)
    r = client.post("/api/v1/gold-models/generate", headers=analyst, json={"model_id": a["models"][0]["id"]})
    assert r.status_code == 403


def test_prefixed_table_is_identified_by_its_own_key(client, engineer):
    silver(client, engineer, "shop_orders", ORDERS, ["order_id"])
    silver(client, engineer, "shop_payments", "order_id,payment_id,paid_on,paid_amount\n1000,5000,2026-01-06,900\n1001,5001,2026-01-21,1200\n1002,5002,2026-02-04,650\n", ["payment_id"])
    a = analysis(client, engineer)
    assert next(t for t in a["silver_tables"] if t["name"] == "shop_payments")["primary_key"] == ["payment_id"]
    assert [r["join"] for r in a["relationships"]] == ["shop_payments.order_id = shop_orders.order_id"]
