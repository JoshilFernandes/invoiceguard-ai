from fastapi.testclient import TestClient

from invoiceguard.api import create_app

from .conftest import sample


def test_upload_review_and_export(settings):
    client = TestClient(create_app(settings))
    assert client.get("/health").json()["status"] == "ok"

    r = client.post("/api/invoices", files={"file": ("06.pdf", sample("06_price_and_quantity_variance.pdf"), "application/pdf")})
    assert r.status_code == 201
    case = r.json()
    assert case["status"] == "awaiting_review"
    assert client.get("/api/invoices", params={"status": "awaiting_review"}).json()[0]["case_id"] == case["case_id"]

    assert client.get(f"/api/invoices/{case['case_id']}/xrechnung").status_code == 404
    decided = client.post(f"/api/invoices/{case['case_id']}/decision", json={"approve": True, "reviewer": "anna"}).json()
    assert decided["status"] == "posted"
    x = client.get(f"/api/invoices/{case['case_id']}/xrechnung")
    assert x.status_code == 200 and "xrechnung_3.0" in x.text
    assert client.post(f"/api/invoices/{case['case_id']}/decision", json={"approve": False}).status_code == 409
    assert client.get("/api/audit/verify").json()["valid"]


def test_rejects_unsupported_files(settings):
    client = TestClient(create_app(settings))
    assert client.post("/api/invoices", files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 415


def test_demo_loader_and_stats(settings):
    client = TestClient(create_app(settings))
    assert len(client.post("/api/demo/load-samples").json()) == 10
    stats = client.get("/api/stats").json()
    assert stats == {**stats, "total": 10, "posted": 3, "awaiting_review": 5, "rejected": 2}
