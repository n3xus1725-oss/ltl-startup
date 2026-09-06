"""Phase 2.4 Test Suite — Conflict Engine & API."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from packages.domain.provenance import FieldProvenanceRecord
from packages.rules.conflict_engine import ConflictEngine
from packages.storage.db import Base, get_db
from packages.storage.repositories.conflicts import ConflictRepository
from packages.storage.repositories.organizations import OrganizationRepository
from packages.storage.repositories.shipments import ShipmentRepository


@pytest.fixture
def db_session():
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


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_conflict_value_mismatch_pricing():
    """Verify value_mismatch detection on pricing discrepancies."""
    rc_rec = FieldProvenanceRecord(
        field="pricing.agreed_total",
        value=1850.0,
        source="rate_confirmation",
        source_id="rc-1",
        authority=100,
    )
    inv_rec = FieldProvenanceRecord(
        field="pricing.agreed_total",
        value=2450.0,  # $600 discrepancy -> critical severity
        source="invoice",
        source_id="inv-1",
        authority=70,
    )

    conflict = ConflictEngine.evaluate_value_mismatch("pricing.agreed_total", rc_rec, inv_rec)
    assert conflict is not None
    assert conflict.conflict_type == "value_mismatch"
    assert conflict.severity == "critical"
    assert conflict.recommended_workflow == "rate_dispute_agent"
    assert "$600.00" in conflict.explanation


def test_conflict_value_mismatch_weight():
    """Verify value_mismatch detection on weight discrepancies."""
    bol_rec = FieldProvenanceRecord(
        field="freight_details.total_weight_lbs",
        value=14000.0,
        source="bol",
        source_id="doc-bol",
        authority=80,
    )
    scale_rec = FieldProvenanceRecord(
        field="freight_details.total_weight_lbs",
        value=16200.0,  # 2,200 lbs / 15.7% diff -> high severity
        source="scale_ticket",
        source_id="doc-scale",
        authority=100,
    )

    conflict = ConflictEngine.evaluate_value_mismatch("freight_details.total_weight_lbs", bol_rec, scale_rec)
    assert conflict is not None
    assert conflict.conflict_type == "value_mismatch"
    assert conflict.severity == "high"
    assert conflict.recommended_workflow == "reweigh_verification"
    assert "2200.0 lbs" in conflict.explanation


def test_conflict_stale_value():
    """Verify stale_value detection when an older timestamp asserts over newer verified state."""
    now = datetime.now(timezone.utc)
    current = FieldProvenanceRecord(
        field="status",
        value="picked_up",
        source="carrier_edi",
        source_id="edi-1",
        timestamp=now,
    )
    stale = FieldProvenanceRecord(
        field="status",
        value="booked",
        source="carrier_email",
        source_id="msg-old",
        timestamp=now - timedelta(hours=2),
    )

    conflict = ConflictEngine.evaluate_stale_value("status", current, stale)
    assert conflict is not None
    assert conflict.conflict_type == "stale_value"
    assert conflict.severity == "low"


def test_conflict_incompatible_status():
    """Verify incompatible_status flags illegal status transitions."""
    conflict = ConflictEngine.evaluate_incompatible_status("created", "delivered")
    assert conflict is not None
    assert conflict.conflict_type == "incompatible_status"
    assert conflict.severity == "high"
    assert "created" in conflict.explanation
    assert "delivered" in conflict.explanation

    # Valid transition returns None
    assert ConflictEngine.evaluate_incompatible_status("booked", "dispatched") is None


def test_conflict_conflicting_party_identity():
    """Verify conflicting_party_identity flags carrier SCAC/name mismatch."""
    curr = FieldProvenanceRecord(
        field="carrier.carrier_name",
        value="Estes Express Lines",
        source="rate_confirmation",
        source_id="rc-1",
    )
    prop = FieldProvenanceRecord(
        field="carrier.carrier_name",
        value="Knight-Swift Transportation",
        source="bol",
        source_id="bol-1",
    )

    conflict = ConflictEngine.evaluate_conflicting_party_identity("carrier.carrier_name", curr, prop)
    assert conflict is not None
    assert conflict.conflict_type == "conflicting_party_identity"
    assert conflict.severity == "critical"
    assert "Estes Express Lines" in conflict.explanation
    assert "Knight-Swift Transportation" in conflict.explanation


def test_conflict_repository_and_api(client, db_session):
    """Verify ConflictRepository and API endpoints for listing and resolving conflicts."""
    org_repo = OrganizationRepository(db_session)
    shipment_repo = ShipmentRepository(db_session)
    conflict_repo = ConflictRepository(db_session)

    org = org_repo.create("Apex Logistics", "apex-conflict-api")
    shipment = shipment_repo.create(
        organization_id=org.id,
        shipment_number="SHP-CONF-01",
        status="booked",
        total_charges=1850.0,
    )

    # Create conflict
    conflict = conflict_repo.create_conflict(
        organization_id=org.id,
        shipment_id=shipment.id,
        field_name="pricing.agreed_total",
        conflict_type="value_mismatch",
        source_a={"source": "rate_confirmation", "value": 1850.0},
        source_b={"source": "invoice", "value": 2200.0},
        explanation="Carrier invoice billed $2,200.00 exceeding agreed rate of $1,850.00",
        severity="high",
        recommended_workflow="rate_dispute_agent",
        idempotency_key=f"conf-test-{shipment.id}",
    )

    # 1. API: List conflicts
    list_resp = client.get(f"/api/v1/conflicts?organization_id={org.id}")
    assert list_resp.status_code == 200
    conflicts_data = list_resp.json()
    assert len(conflicts_data) >= 1
    assert conflicts_data[0]["field_name"] == "pricing.agreed_total"
    assert conflicts_data[0]["status"] == "open"

    # 2. API: Get detail
    detail_resp = client.get(f"/api/v1/conflicts/{conflict.id}?organization_id={org.id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["recommended_workflow"] == "rate_dispute_agent"

    # 3. API: Resolve conflict
    resolve_resp = client.post(
        f"/api/v1/conflicts/{conflict.id}/resolve",
        json={
            "organization_id": str(org.id),
            "resolved_by": "operator_alice",
            "winning_value": 1850.0,
            "comment": "Confirmed rate confirmation agreed amount with carrier dispatch.",
        },
    )
    assert resolve_resp.status_code == 200
    resolve_data = resolve_resp.json()
    assert resolve_data["status"] == "success"
    assert resolve_data["resolved_status"] == "resolved"
    assert resolve_data["winning_value"] == 1850.0

    # Verify database was updated
    db_session.refresh(conflict)
    assert conflict.status == "resolved"
    assert conflict.resolved_by == "operator_alice"
    assert conflict.resolved_value == 1850.0

    # Verify canonical shipment was updated
    canonical = shipment_repo.get_canonical(org.id, shipment.id)
    assert canonical["pricing"]["agreed_total"] == 1850.0
