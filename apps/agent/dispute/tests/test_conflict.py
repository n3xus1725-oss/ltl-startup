"""Phase 4.3 — Conflict tests: amount above threshold → human approval required."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from apps.agent.dispute.service import run_dispute_agent
from packages.domain.models import (
    AuditFindingRecord,
    CarrierContact,
    CarrierInvoice,
    Organization,
    Shipment,
)
from packages.storage.db import Base


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionFactory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = SessionFactory()
    yield session
    session.close()


@pytest.fixture()
def setup_data(db):
    org = Organization(name="Conflict Broker", slug="conflict-dispute")
    db.add(org)
    db.commit()
    db.refresh(org)

    shipment = Shipment(
        organization_id=org.id, shipment_number="SHP-CON-001",
        carrier_name="SAIA", status="delivered",
    )
    db.add(shipment)
    db.commit()
    db.refresh(shipment)

    invoice = CarrierInvoice(
        organization_id=org.id, shipment_id=shipment.id,
        carrier_name="SAIA", invoice_number="INV-CON-001",
        total_billed_amount=3000.00, linehaul_amount=2500.00, fuel_amount=500.00,
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    # $500 dispute — ABOVE the $250 auto-threshold
    finding = AuditFindingRecord(
        organization_id=org.id, invoice_id=invoice.id, shipment_id=shipment.id,
        rule_id="RULE_05", rule_name="Weight Discrepancy", severity="high",
        discrepancy_amount=500.00,  # Above $250 threshold → requires_human_approval
        reason="Billed 5800 lbs, BOL says 4200 lbs", confidence=0.92,
        recommended_action="dispute",
    )
    db.add(finding)
    db.commit()
    db.refresh(finding)

    contact = CarrierContact(
        organization_id=org.id, carrier_name="SAIA",
        billing_email="disputes@saia.com", is_verified=True,
    )
    db.add(contact)
    db.commit()

    return {"org": org, "invoice": invoice, "finding": finding}


def test_above_threshold_requires_human_approval(setup_data, db):
    """Disputed amount above $250 threshold → requires_human_approval even with verified contact."""
    data = setup_data
    mock_response = MagicMock()
    mock_response.content = "SUBJECT: Dispute\n\nLETTER:\nWe dispute $500.00."
    mock_response.cost_estimate = 0.002
    mock_llm = MagicMock()
    mock_llm.default_model = "gpt-4o-mini"
    mock_llm.complete = AsyncMock(return_value=mock_response)

    result = run_dispute_agent(
        organization_id=str(data["org"].id),
        invoice_id=str(data["invoice"].id),
        finding_ids=[str(data["finding"].id)],
        db=db,
        llm=mock_llm,
        triggered_by="test",
    )

    assert result.needs_human is True
    assert result.approval_status == "requires_human_approval"
    assert result.status == "pending_approval"
    # Even though recipient was resolved, still needs human due to threshold
    assert result.disputed_amount == pytest.approx(500.00, rel=1e-4)
