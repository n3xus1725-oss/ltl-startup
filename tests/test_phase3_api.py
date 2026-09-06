"""Phase 3 Test Suite — REST API Endpoints for Rate Contracts, Invoices, and Billing Audit."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from packages.storage.db import Base, get_db


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_contracts_api_crud(client):
    """Test creating and querying carrier rate contracts via REST API."""
    payload = {
        "carrier_name": "Estes Express Lines",
        "contract_number": "CTR-ESTES-TEST",
        "lane_origin_state": "IL",
        "lane_dest_state": "GA",
        "base_rate": 1650.00,
        "minimum_charge": 500.00,
        "rate_type": "flat",
        "fuel_schedule": {"type": "percent", "base_rate_percent": 15.0},
        "accessorial_schedule": {"DETENTION": {"rate_per_hour": 75.0}},
        "is_active": True,
    }

    res = client.post("/api/v1/contracts", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["contract_number"] == "CTR-ESTES-TEST"
    assert data["base_rate"] == 1650.00
    contract_id = data["id"]

    # List contracts
    res_list = client.get("/api/v1/contracts")
    assert res_list.status_code == 200
    contracts = res_list.json()
    assert len(contracts) >= 1
    assert any(c["id"] == contract_id for c in contracts)

    # Get single contract
    res_get = client.get(f"/api/v1/contracts/{contract_id}")
    assert res_get.status_code == 200
    assert res_get.json()["lane_dest_state"] == "GA"


def test_invoice_ingest_and_audit_api(client):
    """Test invoice intake, automatic shipment linking, duplicate check, and audit execution."""
    # 1. Ingest an invoice
    invoice_text = """
    CARRIER FREIGHT INVOICE
    Carrier: Estes Express Lines
    Invoice #: INV-API-001
    Load #: 9001
    Linehaul Rate: $1,850.00
    Fuel Surcharge: $300.00
    Total Due: $2,150.00
    """
    res_ingest = client.post(
        "/api/v1/invoices/ingest",
        json={"document_text": invoice_text, "carrier_name": "Estes Express Lines", "invoice_number": "INV-API-001"},
    )
    assert res_ingest.status_code == 201
    data = res_ingest.json()
    invoice_id = data["invoice_id"]
    assert data["is_duplicate"] is False
    assert data["total_billed_amount"] == 2150.00
    assert data["linehaul_amount"] == 1850.00

    # 2. Ingest duplicate invoice
    res_dup = client.post(
        "/api/v1/invoices/ingest",
        json={"document_text": invoice_text, "carrier_name": "Estes Express Lines", "invoice_number": "INV-API-001"},
    )
    assert res_dup.status_code == 201
    assert res_dup.json()["is_duplicate"] is True

    # 3. Run Billing Audit on the first invoice
    res_audit = client.post(f"/api/v1/invoices/{invoice_id}/audit")
    assert res_audit.status_code == 200
    report = res_audit.json()
    assert "findings" in report
    assert report["total_discrepancy_amount"] >= 0.0

    # 4. Check findings endpoint
    res_findings = client.get(f"/api/v1/invoices/{invoice_id}/findings")
    assert res_findings.status_code == 200


def test_billing_audit_simulation_api(client):
    """Test the 1-click billing audit simulation endpoint used by the Testing UI."""
    res_scenarios = client.get("/api/v1/test-ui/billing-audit/scenarios")
    assert res_scenarios.status_code == 200
    scenarios = res_scenarios.json()
    assert len(scenarios) >= 8

    # Simulate Linehaul Overcharge Scenario
    res_sim = client.post(
        "/api/v1/test-ui/billing-audit/simulate",
        json={"scenario_id": "scenario-linehaul"},
    )
    assert res_sim.status_code == 200
    sim_data = res_sim.json()
    assert sim_data["is_clean"] is False
    assert sim_data["total_discrepancy"] >= 200.00
    finding_rules = [f["rule_id"] for f in sim_data["report"]["findings"]]
    assert "RULE_02_LINEHAUL_MISMATCH" in finding_rules
