"""Phase 4.3 — Ambiguous: no verified carrier contact → human escalation, no email sent."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from apps.agent.dispute.service import run_dispute_agent
from packages.domain.models import (
    AuditFindingRecord,
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
    org = Organization(name="Ambiguous Broker", slug="ambiguous-dispute")
    db.add(org)
    db.commit()
    db.refresh(org)

    shipment = Shipment(
        organization_id=org.id, shipment_number="SHP-AMB-001",
        carrier_name="Mystery Carrier", status="delivered",
    )
    db.add(shipment)
    db.commit()
    db.refresh(shipment)

    invoice = CarrierInvoice(
        organization_id=org.id, shipment_id=shipment.id,
        carrier_name="Mystery Carrier", invoice_number="INV-AMB-001",
        total_billed_amount=500.00, linehaul_amount=400.00, fuel_amount=100.00,
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    finding = AuditFindingRecord(
        organization_id=org.id, invoice_id=invoice.id, shipment_id=shipment.id,
        rule_id="RULE_04", rule_name="Unsupported Accessorial", severity="medium",
        discrepancy_amount=150.00, reason="Residential fee not authorized",
        confidence=0.90, recommended_action="dispute",
    )
    db.add(finding)
    db.commit()
    db.refresh(finding)
    # NOTE: No CarrierContact created for "Mystery Carrier" — triggers escalation
    return {"org": org, "invoice": invoice, "finding": finding}


def test_no_carrier_contact_escalates_to_human(setup_data, db):
    """No CarrierContact on file → needs_human=True, recipient_email=None, no email sent."""
    data = setup_data
    mock_llm = MagicMock()
    mock_llm.default_model = "gpt-4o-mini"
    mock_llm.complete = AsyncMock(return_value=MagicMock(content="", cost_estimate=0.0))

    result = run_dispute_agent(
        organization_id=str(data["org"].id),
        invoice_id=str(data["invoice"].id),
        finding_ids=[str(data["finding"].id)],
        db=db,
        llm=mock_llm,
        triggered_by="test",
    )

    assert result.needs_human is True, "Must escalate when no verified contact"
    assert result.recipient_email is None, "recipient_email must be None when no contact found"
    assert result.status == "pending_approval"
    assert result.approval_status == "requires_human_approval"
