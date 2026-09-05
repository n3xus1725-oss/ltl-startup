"""Automated tests for Phase 1 Internal Testing UI and Dashboard."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from packages.storage.db import Base, get_db


@pytest.fixture(scope="function")
def test_db_session():
    """Isolated in-memory SQLite database for testing test-ui endpoints."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(test_db_session):
    """FastAPI TestClient with overridden get_db dependency."""
    def override_get_db():
        try:
            yield test_db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_dashboard_html_and_root_content_negotiation(client):
    """Verify GET /dashboard returns HTML and GET / returns HTML for browser and JSON for API."""
    # Browser client requesting HTML
    html_resp = client.get("/", headers={"Accept": "text/html,application/xhtml+xml"})
    assert html_resp.status_code == 200
    assert "AI Freight Execution Platform" in html_resp.text
    assert "Inbound Test Mailbox" in html_resp.text

    # Explicit /dashboard endpoint
    dash_resp = client.get("/dashboard")
    assert dash_resp.status_code == 200
    assert "AI Freight Execution Platform" in dash_resp.text

    # API client requesting JSON
    json_resp = client.get("/", headers={"Accept": "application/json"})
    assert json_resp.status_code == 200
    data = json_resp.json()
    assert data["status"] == "operational"
    assert "dashboard_url" in data


def test_get_testing_state_and_auto_seed(client):
    """Verify /test-ui/state returns test organization, seed shipments, and preloaded scenarios."""
    resp = client.get("/api/v1/test-ui/state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["organization"]["slug"] == "nexus-freight-demo"
    assert data["shipment_count"] >= 3
    assert len(data["preloaded_emails"]) >= 4


def test_seed_shipments_endpoint(client):
    """Verify /test-ui/seed resets and creates standard demo shipments."""
    resp = client.post("/api/v1/test-ui/seed")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "seeded"
    assert data["count"] >= 3
    load_ids = [s["load_id"] for s in data["shipments"]]
    assert "5821" in load_ids
    assert "9842" in load_ids
    assert "7710" in load_ids


def test_inject_custom_email_endpoint(client):
    """Verify /test-ui/emails/inject allows composing custom email."""
    custom_payload = {
        "subject": "Custom Driver Update - Load 5821",
        "sender": "driver@customfreight.com",
        "body_text": "Driver is on site at destination for Load 5821.",
        "scenario_label": "Custom On-Site Notice",
    }
    resp = client.post("/api/v1/test-ui/emails/inject", json=custom_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "injected"
    assert data["email"]["subject"] == custom_payload["subject"]

    # Verify email appears in state
    state_resp = client.get("/api/v1/test-ui/state")
    emails = state_resp.json()["preloaded_emails"]
    assert any(e["subject"] == custom_payload["subject"] for e in emails)


def test_process_workflow_a_pickup_confirmation(client):
    """Verify processing Workflow A pickup confirmation updates shipment status and logs audit."""
    # Ensure seeded
    client.post("/api/v1/test-ui/seed")

    payload = {
        "subject": "Estes Express: Load 5821 Picked Up",
        "sender": "tracking@estes-express.com",
        "body_text": "Hello Dispatch, Load 5821 (PRO 9823411) has been picked up from Atlanta GA today at 10:30 AM.",
    }
    resp = client.post("/api/v1/test-ui/process", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["terminal_outcome"] == "completed"
    assert data["decision"] is not None
    assert data["confidence"] >= 0.9

    # Verify matched shipment & diff
    matched = data["matched_shipment"]
    assert matched is not None
    assert matched["load_id"] == "5821"
    assert matched["status_before"] == "booked"
    assert matched["status_after"] == "picked_up"
    assert "status" in matched["diff"]

    # Verify tool calls
    tool_names = [tc["tool_name"] for tc in data["tool_calls"]]
    assert "update_shipment" in tool_names

    # Verify audit log entry
    assert data["audit_log"] is not None
    assert "agent_run" in data["audit_log"]["action_taken"] or "update" in data["audit_log"]["action_taken"]


def test_process_workflow_b_eta_update(client):
    """Verify processing Workflow B ETA update within SLA updates ETA date."""
    from datetime import datetime, timedelta, timezone
    client.post("/api/v1/test-ui/seed")

    new_eta = (datetime.now(timezone.utc) + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "subject": "Old Dominion: ETA Update for PRO 9823411 (Load 5821)",
        "sender": "updates@odfl.com",
        "body_text": f"Notification: Shipment PRO 9823411 (Load 5821) minor traffic delay. New revised ETA is {new_eta}.",
    }
    resp = client.post("/api/v1/test-ui/process", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["terminal_outcome"] == "completed"
    matched = data["matched_shipment"]
    assert matched is not None
    assert matched["load_id"] == "5821"
    assert "eta" in matched["diff"]


def test_process_ambiguous_email_escalates(client):
    """Verify processing an ambiguous email (multiple plausible shipments) escalates to human without updating shipments."""
    client.post("/api/v1/test-ui/seed")

    payload = {
        "subject": "Inquiry regarding Load 5821 and Load 9842",
        "sender": "carrier-unknown@dispatchers.com",
        "body_text": "Driver has questions regarding both Load 5821 and Load 9842 dock assignments. Please confirm.",
    }
    resp = client.post("/api/v1/test-ui/process", json=payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["terminal_outcome"] == "needs_human"
    assert data["approval_state"] == "needs_human"
    assert data["matched_shipment"] is None


def test_list_shipments_and_audit_logs_endpoints(client):
    """Verify /test-ui/shipments and /test-ui/audit-logs return database tables."""
    client.post("/api/v1/test-ui/seed")

    # Shipments
    ship_resp = client.get("/api/v1/test-ui/shipments")
    assert ship_resp.status_code == 200
    shipments = ship_resp.json()
    assert len(shipments) >= 3

    # Audit Logs
    audit_resp = client.get("/api/v1/test-ui/audit-logs")
    assert audit_resp.status_code == 200
    assert isinstance(audit_resp.json(), list)
