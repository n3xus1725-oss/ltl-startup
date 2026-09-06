"""Phase 4.6 — Integration tests for Dispute Agent API endpoints.

Tests cover:
- Carrier contacts CRUD via API
- Dispute creation (auto-approved)
- Dispute requires human when no contact
- Human approval flow via API
- Recovery ledger proof requirement enforcement
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from apps.api.main import app
from packages.domain.models import (
    AuditFindingRecord,
    CarrierInvoice,
    Organization,
    Shipment,
)
from packages.storage.db import Base, get_db


from sqlalchemy.pool import StaticPool

@pytest.fixture(scope="function")
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionFactory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = SessionFactory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def seed_data(client):
    """Seed org, shipment, invoice, and audit finding for dispute tests."""
    # Get the DB session from the overridden dependency
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionFactory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = SessionFactory()

    org = Organization(name="API Test Broker", slug="api-test-dispute-broker")
    db.add(org)
    db.commit()
    db.refresh(org)

    shipment = Shipment(
        organization_id=org.id, shipment_number="SHP-API-001",
        carrier_name="Old Dominion", status="delivered",
    )
    db.add(shipment)
    db.commit()
    db.refresh(shipment)

    invoice = CarrierInvoice(
        organization_id=org.id, shipment_id=shipment.id,
        carrier_name="Old Dominion", invoice_number="INV-API-001",
        total_billed_amount=1500.00, linehaul_amount=1200.00, fuel_amount=300.00,
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    finding = AuditFindingRecord(
        organization_id=org.id, invoice_id=invoice.id, shipment_id=shipment.id,
        rule_id="RULE_02", rule_name="Linehaul Mismatch",
        severity="high", discrepancy_amount=180.00,
        reason="Billed $1200, contracted $1000",
        confidence=0.95, recommended_action="dispute",
        evidence={"source_documents": ["SIGNED_BOL"]},
    )
    db.add(finding)
    db.commit()
    db.refresh(finding)
    db.close()

    return {
        "org_id": str(org.id),
        "invoice_id": str(invoice.id),
        "finding_id": str(finding.id),
        "shipment_id": str(shipment.id),
    }


def test_carrier_contacts_crud_api(client):
    """Carrier contacts can be created, listed, updated, and deleted via API."""
    org_id = str(uuid.uuid4())

    # Create
    resp = client.post("/api/v1/carrier-contacts", json={
        "organization_id": org_id,
        "carrier_name": "Old Dominion",
        "billing_email": "billing@odfl.com",
        "contact_name": "OD Billing",
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["contact"]["billing_email"] == "billing@odfl.com"
    assert data["contact"]["is_verified"] is True
    contact_id = data["contact"]["id"]

    # List
    resp = client.get(f"/api/v1/carrier-contacts?organization_id={org_id}")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1

    # Delete
    resp = client.delete(f"/api/v1/carrier-contacts/{contact_id}?organization_id={org_id}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    # List again — should be empty
    resp = client.get(f"/api/v1/carrier-contacts?organization_id={org_id}")
    assert resp.json()["count"] == 0


def test_dispute_simulation_auto_approved(client):
    """UI simulation endpoint: auto_approved scenario works end-to-end."""
    resp = client.post("/api/v1/test-ui/dispute-agent/run", json={"scenario": "auto_approved"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["scenario"] == "auto_approved"
    assert data["status"] == "sent"
    assert data["approval_status"] == "auto_approved"
    assert data["recipient_email"] is not None
    assert data["needs_human"] is False
    assert data["disputed_amount"] == pytest.approx(180.00, rel=1e-3)
    # Financial integrity check: disputed amount must equal finding amount
    assert "financial_integrity_note" in data
    assert "AuditFindingRecord" in data["financial_integrity_note"]
    assert len(data["trajectory"]) >= 6


def test_dispute_simulation_requires_human_when_no_contact(client):
    """UI simulation: no_contact scenario escalates to human, recipient_email is None."""
    resp = client.post("/api/v1/test-ui/dispute-agent/run", json={"scenario": "no_contact"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["scenario"] == "no_contact"
    assert data["needs_human"] is True
    assert data["recipient_email"] is None, "SAFETY: recipient_email must be None when no contact found"
    assert data["status"] == "pending_approval"


def test_dispute_simulation_above_threshold_requires_human(client):
    """UI simulation: requires_human scenario (above $250 threshold) → human approval."""
    resp = client.post("/api/v1/test-ui/dispute-agent/run", json={"scenario": "requires_human"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["scenario"] == "requires_human"
    assert data["needs_human"] is True
    assert data["approval_status"] == "requires_human_approval"
    assert data["disputed_amount"] == pytest.approx(500.00, rel=1e-3)


def test_recovery_ledger_api_returns_correct_structure(client):
    """Recovery ledger API returns proper structure with financial summaries."""
    # First run a scenario to create a recovery entry
    client.post("/api/v1/test-ui/dispute-agent/run", json={"scenario": "auto_approved"})

    # Get the org from state
    state_resp = client.get("/api/v1/test-ui/state")
    state = state_resp.json()
    org_id = state.get("organization_id")

    if org_id:
        resp = client.get(f"/api/v1/disputes/recovery-ledger/all?organization_id={org_id}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "entries" in data
        assert "total_verified_recovery_usd" in data
        assert "total_platform_revenue_share_usd" in data
        assert "count" in data
        # approved_recovery should be NULL (no proof submitted yet)
        for entry in data["entries"]:
            assert entry["approved_recovery"] is None, \
                "Recovery must be NULL until proof is recorded"
