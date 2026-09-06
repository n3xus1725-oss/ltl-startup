"""Phase 4.3 — Happy path: verified contact exists, dispute auto-approved and sent."""

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
    org = Organization(name="Happy Path Broker", slug="happy-path-dispute")
    db.add(org)
    db.commit()
    db.refresh(org)

    shipment = Shipment(
        organization_id=org.id, shipment_number="SHP-HP-001",
        carrier_name="Old Dominion", status="delivered",
    )
    db.add(shipment)
    db.commit()
    db.refresh(shipment)

    invoice = CarrierInvoice(
        organization_id=org.id, shipment_id=shipment.id,
        carrier_name="Old Dominion", invoice_number="INV-HP-001",
        total_billed_amount=1500.00, linehaul_amount=1200.00, fuel_amount=300.00,
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    finding = AuditFindingRecord(
        organization_id=org.id, invoice_id=invoice.id, shipment_id=shipment.id,
        rule_id="RULE_02", rule_name="Wrong Linehaul Rate", severity="high",
        discrepancy_amount=200.00,  # Under auto-threshold of $250
        reason="Billed $1200, contracted $1000", confidence=0.95,
        recommended_action="dispute",
        evidence={"source_documents": ["SIGNED_BOL"]}
    )
    db.add(finding)
    db.commit()
    db.refresh(finding)

    # Verified carrier contact — required for auto-send
    contact = CarrierContact(
        organization_id=org.id, carrier_name="Old Dominion",
        billing_email="billing@odfl.com", contact_name="OD Billing",
        is_verified=True,
    )
    db.add(contact)
    db.commit()

    return {"org": org, "invoice": invoice, "finding": finding}


def test_happy_path_dispute_auto_approved(setup_data, db):
    """Verified contact + under threshold + high confidence → auto_approved and sent.
    Financial amounts MUST equal finding.discrepancy_amount exactly.
    """
    data = setup_data
    mock_response = MagicMock()
    mock_response.content = "SUBJECT: Invoice Dispute — INV-HP-001\n\nLETTER:\nDear OD Billing,\nWe dispute $200.00 on invoice INV-HP-001."
    mock_response.cost_estimate = 0.001
    mock_response.prompt_tokens = 200
    mock_response.completion_tokens = 100

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

    assert result.status == "sent", f"Expected 'sent', got '{result.status}'"
    assert result.approval_status == "auto_approved"
    assert result.recipient_email == "billing@odfl.com"
    assert result.needs_human is False
    assert result.error is None
    # Financial integrity: disputed_amount must equal finding.discrepancy_amount
    assert result.disputed_amount == pytest.approx(200.00, rel=1e-4), \
        f"disputed_amount {result.disputed_amount} != finding.discrepancy_amount 200.00"
    assert result.dispute_id is not None
    assert len(result.trajectory) >= 6
