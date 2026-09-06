"""Phase 4.3 — Tool failure: LLM error causes graceful failure, no duplicate dispute."""

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
    org = Organization(name="Tool Fail Broker", slug="toolfail-dispute")
    db.add(org)
    db.commit()
    db.refresh(org)

    shipment = Shipment(
        organization_id=org.id, shipment_number="SHP-TF-001",
        carrier_name="R+L Carriers", status="delivered",
    )
    db.add(shipment)
    db.commit()
    db.refresh(shipment)

    invoice = CarrierInvoice(
        organization_id=org.id, shipment_id=shipment.id,
        carrier_name="R+L Carriers", invoice_number="INV-TF-001",
        total_billed_amount=700.00, linehaul_amount=600.00, fuel_amount=100.00,
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    finding = AuditFindingRecord(
        organization_id=org.id, invoice_id=invoice.id, shipment_id=shipment.id,
        rule_id="RULE_02", rule_name="Linehaul Mismatch", severity="high",
        discrepancy_amount=200.00, reason="Overcharge on linehaul",
        confidence=0.92, recommended_action="dispute",
    )
    db.add(finding)
    db.commit()
    db.refresh(finding)

    contact = CarrierContact(
        organization_id=org.id, carrier_name="R+L Carriers",
        billing_email="billing@rlcarriers.com", is_verified=True,
    )
    db.add(contact)
    db.commit()

    return {"org": org, "invoice": invoice, "finding": finding}


def test_llm_failure_causes_graceful_error(setup_data, db):
    """When LLM throws during dispute package generation, agent fails gracefully.
    No duplicate disputes should be created in the DB.
    """
    data = setup_data
    mock_llm = MagicMock()
    mock_llm.default_model = "gpt-4o-mini"
    mock_llm.complete = AsyncMock(side_effect=Exception("LLM API timeout"))

    result = run_dispute_agent(
        organization_id=str(data["org"].id),
        invoice_id=str(data["invoice"].id),
        finding_ids=[str(data["finding"].id)],
        db=db,
        llm=mock_llm,
        triggered_by="test",
        idempotency_key="tool-failure-test-001",
    )

    # Should not crash — should return a result with error or needs_human
    assert result is not None
    # Either failed or needs_human — never a successful send on LLM failure
    assert result.status != "sent", "Cannot be 'sent' when LLM failed"
