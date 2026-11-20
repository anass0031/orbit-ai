"""New pipeline tools — destination nodes."""
import csv
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import openpyxl
import pytest

from app.core.config import get_settings
from app.services import notify
from tests.conftest import bronze, cleaned
from tests.test_pipeline_tools import chain, node, pipeline, rows, run, validate


# ── fakes ────────────────────────────────────────────────────────────────
class Receiver:
    """Local HTTP endpoint recording every request (REST output, webhooks)."""

    def __init__(self):
        self.requests, self.status = [], 201
        outer = self

        class H(BaseHTTPRequestHandler):
            def _do(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                outer.requests.append({"method": self.command, "path": self.path, "headers": dict(self.headers),
                                       "json": json.loads(body or b"null")})
                self.send_response(outer.status)
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

            do_POST = do_PUT = do_PATCH = _do  # noqa: N815

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def receiver():
    r = Receiver()
    yield r
    r.server.shutdown()


class FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=None):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, *a):
        pass

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(get_settings(), "smtp_host", "smtp.test")
    return FakeSMTP.sent


def test_export_files_and_download(client, engineer, analyst, sales):
    g = {"nodes": [node("in", "dataset_input", dataset_id=sales["gold"]["id"]),
                   node("c", "export_file", format="csv", filename="revenue_{{run_date}}"),
                   node("x", "export_file", format="xlsx", filename="revenue"),
                   node("j", "export_file", format="json", filename="revenue")],
         "edges": [{"source": "in", "target": t} for t in ("c", "x", "j")]}
    d = run(client, engineer, pipeline(client, engineer, g)["id"], params={"run_date": "2026-03-31"})
    arts = d["artifacts"]
    assert [a["status"] for a in arts] == ["succeeded"] * 3 and arts[0]["details"]["filename"] == "revenue_2026-03-31.csv"
    n = sales["gold"]["row_count"]
    r = client.get(f"/api/v1/pipelines/runs/{d['id']}/artifacts/0/download", headers=analyst)  # analysts read runs
    assert r.status_code == 200 and "revenue_2026-03-31.csv" in r.headers["content-disposition"]
    assert len(list(csv.DictReader(io.StringIO(r.text)))) == n
    wb = openpyxl.load_workbook(io.BytesIO(client.get(f"/api/v1/pipelines/runs/{d['id']}/artifacts/1/download", headers=analyst).content))
    assert wb.active.max_row == n + 1
    assert len(client.get(f"/api/v1/pipelines/runs/{d['id']}/artifacts/2/download", headers=analyst).json()) == n
    assert client.get(f"/api/v1/pipelines/runs/{d['id']}/artifacts/9/download", headers=analyst).status_code == 404


def test_rest_output_batches_errors_and_blocked_hosts(client, engineer, receiver):
    b = bronze(client, engineer, "tickets", "id,title\n" + "\n".join(f"{i},Ticket {i}" for i in range(1, 11)) + "\n")
    g = {"nodes": [node("in", "dataset_input", dataset_id=b["id"]),
                   node("o", "output", layer="silver", name="tickets_sent"),
                   node("r", "rest_output", url=f"{receiver.url}/ingest", batch_size=4, wrapper_key="records",
                        headers=[{"name": "X-Run-Date", "value": "{{run_date}}"}])],
         "edges": [{"source": "in", "target": "o"}, {"source": "in", "target": "r"}]}
    p = pipeline(client, engineer, cleaned(g, ["id"]))
    d = run(client, engineer, p["id"], params={"run_date": "2026-05-01"})
    assert [len(x["json"]["records"]) for x in receiver.requests] == [4, 4, 2]
    assert receiver.requests[0]["headers"]["X-Run-Date"] == "2026-05-01" and receiver.requests[0]["json"]["records"][0]["title"] == "Ticket 1"
    assert d["artifacts"][0]["details"]["requests"] == 3

    receiver.status = 500
    failed = run(client, engineer, p["id"], ok=False)
    assert failed["status"] == "failed" and failed["error_class"] == "destination_failed" and failed["artifacts"][0]["status"] == "failed"
    _, ds = rows(client, engineer, "silver", "tickets_sent")
    assert ds["current_version"] == 2  # the dataset output was written before the delivery failed

    g["nodes"][2]["config"]["on_error"] = "warn"
    client.put(f"/api/v1/pipelines/{p['id']}", headers=engineer, json={"graph": g})
    warned = run(client, engineer, p["id"])
    assert warned["artifacts"][0]["status"] == "failed" and "HTTP 500" in warned["artifacts"][0]["message"]

    g["nodes"][2]["config"].update(url="http://169.254.169.254/latest/meta-data", on_error="fail")
    client.put(f"/api/v1/pipelines/{p['id']}", headers=engineer, json={"graph": g})
    blocked = run(client, engineer, p["id"], ok=False)
    assert "link-local" in blocked["error_message"] and len(receiver.requests) < 10


def test_email_report(client, engineer, sales, smtp, monkeypatch):
    g = chain(node("in", "dataset_input", dataset_id=sales["gold"]["id"]),
              node("e", "email_report", to="boss@example.com, team@example.com", subject="Revenue {{run_date}}", include="both",
                   max_rows=5, message="Monthly revenue by region"))
    assert any("Invalid email" in i["message"] for i in validate(client, engineer, chain(
        node("in", "dataset_input", dataset_id=sales["gold"]["id"]), node("e", "email_report", to="not-an-email")))["issues"])
    d = run(client, engineer, pipeline(client, engineer, g)["id"], params={"run_date": "2026-04-30"})
    assert d["artifacts"][0]["status"] == "succeeded" and len(smtp) == 1
    msg = smtp[0]
    assert msg["Subject"] == "Revenue 2026-04-30" and msg["To"] == "boss@example.com, team@example.com"
    parts = list(msg.iter_attachments())
    assert parts and parts[0].get_filename().endswith(".csv")
    html = next(p for p in msg.walk() if p.get_content_type() == "text/html").get_content()
    assert html.count("<tr>") == 6 and "Monthly revenue by region" in html  # header + 5 rows

    monkeypatch.setattr(get_settings(), "smtp_host", "")
    d = run(client, engineer, pipeline(client, engineer, g)["id"], ok=False)
    assert "E-mail is not configured" in d["error_message"]
