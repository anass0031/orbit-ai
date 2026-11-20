"""The star schema demonstration built from the files of samples/."""
import importlib.util
from pathlib import Path

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def test_star_schema_from_the_sample_files(client, engineer):
    spec = importlib.util.spec_from_file_location("demo_star_schema", SAMPLES / "demo_star_schema.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    out = demo.build(client, engineer, SAMPLES)
    assert out["dim_client"]["row_count"] == 9 and out["dim_date"]["row_count"] == 367  # 8 clients / 366 days + Unknown
    assert out["fact_commandes"]["row_count"] == 6 and out["fact_commandes"]["model_role"] == "fact"
    rows = client.get(f"/api/v1/datasets/{out['fact_commandes']['id']}/preview?limit=50", headers=engineer).json()["rows"]
    unknown = sorted(r["order_id"] for r in rows if r["client_key"] == -1)
    assert unknown == [3, 5, 7] and all(r["date_key"] > 20240000 for r in rows)  # these three clients are not in clients.json
    model = client.get(f"/api/v1/datasets/{out['fact_commandes']['id']}/model", headers=engineer).json()
    assert {r["dimension"] for r in model["relationships"]} == {"gold.dim_client", "gold.dim_date"}
    demo.build(client, engineer, SAMPLES)  # can be replayed
