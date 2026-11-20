"""Build a small star schema from the sample files of this folder, through the ORBIT AI API.

    bronze.commandes, bronze.clients → silver.commandes_clean, silver.clients_clean
    → gold.dim_client, gold.dim_date, gold.fact_commandes

Usage (the stack must be running):  python samples/demo_star_schema.py --url http://localhost:8000 --password <ORBIT_DEMO_PASSWORD>
"""
from __future__ import annotations

import argparse
from pathlib import Path

API = "/api/v1"


def _ok(r, *codes):
    if r.status_code not in (codes or (200, 201, 202)):
        raise RuntimeError(f"{r.request.method} {r.request.url} → {r.status_code}: {r.text[:400]}")
    return r.json()


def _bronze(client, h, path: Path, name: str) -> dict:
    imp = _ok(client.post(f"{API}/imports", headers=h, files={"file": (path.name, path.read_bytes())}))
    return _ok(client.post(f"{API}/imports/{imp['id']}/ingest", headers=h, json={"dataset_name": name}))["dataset"]


def _run(client, h, pipeline_id: str) -> dict:
    run = _ok(client.post(f"{API}/pipelines/{pipeline_id}/run", headers=h))
    for _ in range(120):
        run = _ok(client.get(f"{API}/runs/{run['id']}", headers=h))
        if run["status"] in ("succeeded", "failed", "cancelled"):
            break
    if run["status"] != "succeeded":
        raise RuntimeError(f"Run failed: {run['error_message']}")
    return run


def _pipeline(client, h, name: str, create) -> str:
    found = next((p for p in _ok(client.get(f"{API}/pipelines", headers=h)) if p["name"] == name), None)
    return found["id"] if found else create()["id"]


def _dataset(client, h, layer: str, name: str) -> dict:
    return next(d for d in _ok(client.get(f"{API}/datasets?layer={layer}", headers=h)) if d["name"] == name)


def _silver(client, h, bronze: dict, keys: list[str], target: str) -> dict:
    name = f"demo: {target}"
    pid = _pipeline(client, h, name, lambda: _ok(client.post(f"{API}/pipelines/templates/bronze-to-silver", headers=h, json={
        "dataset_id": bronze["id"], "keys": keys, "name": name, "silver_name": target})))
    _run(client, h, pid)
    return _dataset(client, h, "silver", target)


def star_graph(clients: dict, orders: dict) -> dict:
    def n(i, t, **config):
        return {"id": i, "type": t, "config": config}

    date_key = "CAST(strftime({}, '%Y%m%d') AS BIGINT)"
    nodes = [
        n("clients", "dataset_input", dataset_id=clients["id"]),
        n("client_key", "surrogate_key", keys=["nom"], into="client_key"),
        n("dim_client", "output", layer="gold", name="dim_client", model_role="dimension", primary_key=["client_key"]),
        n("calendar", "date_dimension", start="2024-01-01", end="2024-12-31", holidays="fr"),
        n("calendar_key", "derived_column", columns=[{"name": "date_key", "expression": date_key.format('"date"')}]),
        n("dim_date", "output", layer="gold", name="dim_date", model_role="dimension", primary_key=["date_key"]),
        n("orders", "dataset_input", dataset_id=orders["id"]),
        n("order_client", "surrogate_key", keys=["client"], into="client_key"),
        n("order_date", "derived_column", columns=[{"name": "date_key", "expression": date_key.format("date_commande")}]),
        n("grain", "select", columns=[{"name": x} for x in ("order_id", "client_key", "date_key", "montant")]),
        n("fact_commandes", "output", layer="gold", name="fact_commandes", model_role="fact", primary_key=["order_id"],
          relationships=[{"column": "client_key", "dimension": "dim_client"}, {"column": "date_key", "dimension": "dim_date"}]),
    ]
    links = [("clients", "client_key"), ("client_key", "dim_client"), ("calendar", "calendar_key"), ("calendar_key", "dim_date"),
             ("orders", "order_client"), ("order_client", "order_date"), ("order_date", "grain"), ("grain", "fact_commandes")]
    return {"nodes": nodes, "edges": [{"source": a, "target": b} for a, b in links]}


def build(client, headers: dict, samples: Path) -> dict:
    clients = _silver(client, headers, _bronze(client, headers, samples / "clients.json", "clients"), ["id"], "clients_clean")
    orders = _silver(client, headers, _bronze(client, headers, samples / "commandes.csv", "commandes"), ["order_id"], "commandes_clean")
    name = "demo: star schema"
    graph = star_graph(clients, orders)
    pid = _pipeline(client, headers, name, lambda: _ok(client.post(f"{API}/pipelines", headers=headers, json={"name": name, "graph": graph})))
    _ok(client.put(f"{API}/pipelines/{pid}", headers=headers, json={"graph": graph}))
    _run(client, headers, pid)
    return {t: _dataset(client, headers, "gold", t) for t in ("dim_client", "dim_date", "fact_commandes")}


if __name__ == "__main__":
    import httpx

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--email", default="engineer@orbit.local")
    ap.add_argument("--password", required=True)
    args = ap.parse_args()
    with httpx.Client(base_url=args.url, timeout=120) as http:
        token = _ok(http.post(f"{API}/auth/login", json={"email": args.email, "password": args.password}))["access_token"]
        for table, ds in build(http, {"Authorization": f"Bearer {token}"}, Path(__file__).parent).items():
            print(f"gold.{table}: {ds['row_count']} rows ({ds['model_role']})")
