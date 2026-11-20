"""Quality rules, scores and anomalies."""
from tests.conftest import bronze

CUSTOMERS = (
    "id,email,age,country,signup\n"
    "1,a@x.com,34,FR,2024-01-01\n"
    "2,b@x.com,17,MA,2024-02-01\n"
    "3,not-an-email,45,FR,2024-03-01\n"
    "3,c@x.com,,ES,2024-04-01\n"
)


def rule(client, h, ds_id, **kw):
    r = client.post("/api/v1/quality/rules", headers=h, json={"dataset_id": ds_id, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_rules_scores_and_issues(client, as_engineer, as_analyst):
    ds = bronze(client, as_engineer, "customers", CUSTOMERS)
    rule(client, as_engineer, ds["id"], rule_type="not_null", column="age")
    rule(client, as_engineer, ds["id"], rule_type="unique", column="id", severity="high")
    rule(client, as_engineer, ds["id"], rule_type="regex", column="email", params={"pattern": r"[^@]+@[^@]+\.[a-z]+"})
    rule(client, as_engineer, ds["id"], rule_type="range", column="age", params={"min": 18})
    rule(client, as_engineer, ds["id"], rule_type="allowed_values", column="country", params={"values": ["FR", "MA", "ES"]})
    rule(client, as_engineer, ds["id"], rule_type="row_count", params={"min": 1})
    rule(client, as_engineer, ds["id"], rule_type="custom_sql", params={"expression": "age IS NULL OR age < 120"})

    results = client.post(f"/api/v1/quality/datasets/{ds['id']}/run", headers=as_engineer).json()
    scores = sorted(r["score"] for r in results)
    assert scores == [50.0, 75.0, 75.0, 75.0, 100.0, 100.0, 100.0]  # unique: 2/4 rows share id 3
    failing = [r for r in results if not r["passed"]]
    assert len(failing) == 4 and all(r["evidence"] for r in failing)

    ov = client.get("/api/v1/quality/overview", headers=as_analyst).json()
    assert ov["score"] == round(sum(scores) / 7, 1)
    assert ov["dimensions"]["uniqueness"] == 50.0 and ov["dimensions"]["completeness"] == 87.5
    assert ov["open_issues"] == 4
    assert client.get(f"/api/v1/datasets/{ds['id']}", headers=as_analyst).json()["quality_score"] == round(sum(scores) / 7, 2)

    # a second run updates the same anomalies instead of opening new ones
    client.post(f"/api/v1/quality/datasets/{ds['id']}/run", headers=as_engineer)
    issues = client.get("/api/v1/quality/issues?status=active", headers=as_analyst).json()["items"]
    assert len(issues) == 4 and all(i["occurrences"] == 2 for i in issues)
    assert {i["dataset_name"] for i in issues} == {"bronze.customers"}

    # the analyst handles anomalies but cannot change rules
    i = issues[0]
    r = client.patch(f"/api/v1/quality/issues/{i['id']}", headers=as_analyst, json={"status": "acknowledged", "note": "on it"})
    assert r.status_code == 200 and r.json()["acknowledged_at"]
    r = client.patch(f"/api/v1/quality/issues/{i['id']}", headers=as_analyst, json={"status": "resolved", "note": "fixed upstream"})
    assert r.json()["status"] == "resolved" and r.json()["resolution_note"] == "fixed upstream"
    assert client.post("/api/v1/quality/rules", headers=as_analyst, json={
        "dataset_id": ds["id"], "rule_type": "not_null", "column": "id"}).status_code == 403
    assert client.post(f"/api/v1/quality/datasets/{ds['id']}/run", headers=as_analyst).status_code == 403


def test_rule_validation(client, as_engineer):
    ds = bronze(client, as_engineer, "customers", CUSTOMERS)

    def bad(**kw):
        r = client.post("/api/v1/quality/rules", headers=as_engineer, json={"dataset_id": ds["id"], **kw})
        assert r.status_code == 422, r.text
        return r.json()["error"]["details"][0]["message"]

    assert "does not exist" in bad(rule_type="not_null", column="nope")
    assert "needs a column" in bad(rule_type="unique")
    assert "regular expression" in bad(rule_type="regex", column="email", params={"pattern": "("})
    assert "min and/or a max" in bad(rule_type="range", column="age")
    assert "boolean expression" in bad(rule_type="custom_sql", params={"expression": "1; DROP TABLE t"})
    assert "boolean expression" in bad(rule_type="custom_sql", params={"expression": "(SELECT 1)"})


def test_disabling_a_rule_updates_the_score(client, as_engineer):
    ds = bronze(client, as_engineer, "customers", CUSTOMERS)
    r1 = rule(client, as_engineer, ds["id"], rule_type="unique", column="id")
    rule(client, as_engineer, ds["id"], rule_type="not_null", column="email")
    client.post(f"/api/v1/quality/datasets/{ds['id']}/run", headers=as_engineer)
    assert client.get(f"/api/v1/datasets/{ds['id']}", headers=as_engineer).json()["quality_score"] == 75.0
    assert client.get("/api/v1/quality/issues?status=open", headers=as_engineer).json()["total"] == 1
    client.patch(f"/api/v1/quality/rules/{r1['id']}", headers=as_engineer, json={"enabled": False})
    assert client.get(f"/api/v1/datasets/{ds['id']}", headers=as_engineer).json()["quality_score"] == 100.0
    resolved = client.get("/api/v1/quality/issues?status=resolved", headers=as_engineer).json()["items"]
    assert len(resolved) == 1 and resolved[0]["resolution_note"] == "Resolved: the rule was disabled"


def test_blocking_rule_protects_bronze_imports(client, as_engineer):
    ds = bronze(client, as_engineer, "customers", CUSTOMERS)
    rule(client, as_engineer, ds["id"], rule_type="not_null", column="age", on_failure="fail")
    imp = client.post("/api/v1/imports", headers=as_engineer,
                      files={"file": ("c.csv", b"id,email,age,country,signup\n9,z@x.com,,FR,2024-05-01\n")}).json()
    r = client.post(f"/api/v1/imports/{imp['id']}/ingest", headers=as_engineer, json={"dataset_name": "customers"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "quality_gate_failed"
    assert client.get(f"/api/v1/datasets/{ds['id']}", headers=as_engineer).json()["current_version"] == 1
    assert client.get("/api/v1/quality/issues?status=open", headers=as_engineer).json()["total"] == 1


def test_quarantine_rule_on_import(client, as_engineer):
    ds = bronze(client, as_engineer, "customers", CUSTOMERS)
    rule(client, as_engineer, ds["id"], rule_type="regex", column="email", params={"pattern": r"[^@]+@[^@]+"},
         on_failure="quarantine")
    imp = client.post("/api/v1/imports", headers=as_engineer, files={"file": ("c.csv", CUSTOMERS.encode())}).json()
    r = client.post(f"/api/v1/imports/{imp['id']}/ingest", headers=as_engineer, json={"dataset_name": "customers"})
    assert r.status_code == 201 and r.json()["version"]["row_count"] == 3  # the bad email was set aside
