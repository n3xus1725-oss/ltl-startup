"""Phase 4.3 — Idempotency: same trigger returns existing dispute, no duplicate."""

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
from packages.storage.repositories.disputes import DisputeRepository


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
    org = Organization(name="Idemp Broker", slug="idemp-dispute")
    db.add(org)
    db.commit()
    db.refresh(org)

    shipment = Shipment(
        organization_id=org.id, shipment_number="SHP-IDP-001",
        carrier_name="Estes", status="delivered",
    )
    db.add(shipment)
    db.commit()
    db.refresh(shipment)

    invoice = CarrierInvoice(
        organization_id=org.id, shipment_id=shipment.id,
        carrier_name="Estes", invoice_number="INV-IDP-001",
        total_billed_amount=800.00, linehaul_amount=600.00, fuel_amount=200.00,
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    finding = AuditFindingRecord(
        organization_id=org.id, invoice_id=invoice.id, shipment_id=shipment.id,
        rule_id="RULE_03", rule_name="Fuel Mismatch", severity="medium",
        discrepancy_amount=100.00, reason="Fuel surcharge incorrect",
        confidence=0.88, recommended_action="dispute",
    )
    db.add(finding)
    db.commit()
    db.refresh(finding)

    contact = CarrierContact(
        organization_id=org.id, carrier_name="Estes",
        billing_email="billing@estes.com", is_verified=True,
    )
    db.add(contact)
    db.commit()

    return {"org": org, "invoice": invoice, "finding": finding}


def test_idempotency_same_key_returns_existing(setup_data, db):
    """Running the agent twice with the same idempotency_key returns the same dispute."""
    data = setup_data
    mock_response = MagicMock()
    mock_response.content = "SUBJECT: Dispute\n\nLETTER:\nDispute $100.00."
    mock_response.cost_estimate = 0.001
    mock_llm = MagicMock()
    mock_llm.default_model = "gpt-4o-mini"
    mock_llm.complete = AsyncMock(return_value=mock_response)

    idemp_key = "test-idempotency-key-001"

    result1 = run_dispute_agent(
        organization_id=str(data["org"].id),
        invoice_id=str(data["invoice"].id),
        finding_ids=[str(data["finding"].id)],
        db=db,
        llm=mock_llm,
        idempotency_key=idemp_key,
    )

    result2 = run_dispute_agent(
        organization_id=str(data["org"].id),
        invoice_id=str(data["invoice"].id),
        finding_ids=[str(data["finding"].id)],
        db=db,
        llm=mock_llm,
        idempotency_key=idemp_key,
    )

    # Same dispute returned
    assert result1.dispute_id == result2.dispute_id

    # Only one dispute record in DB
    repo = DisputeRepository(db)
    disputes = repo.get_disputes_for_invoice(data["org"].id, data["invoice"].id)
    assert len(disputes) == 1, f"Expected 1 dispute, got {len(disputes)}"
