"""Phase 4.1 — Data model and repository tests for Dispute Agent."""


import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from packages.domain.models import (
    AuditFindingRecord,
    CarrierInvoice,
    Organization,
    Shipment,
)
from packages.storage.db import Base
from packages.storage.repositories.carrier_contacts import CarrierContactRepository
from packages.storage.repositories.disputes import DisputeRepository
from packages.storage.repositories.recovery import RecoveryIntegrityError, RecoveryRepository


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    SessionFactory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    session = SessionFactory()
    yield session
    session.close()


@pytest.fixture()
def org(db):
    org = Organization(name="Test Broker", slug="test-broker-dispute")
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


@pytest.fixture()
def shipment(db, org):
    s = Shipment(
        organization_id=org.id,
        shipment_number="SHP-DISPUTE-001",
        carrier_name="Old Dominion",
        status="delivered",
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@pytest.fixture()
def invoice(db, org, shipment):
    inv = CarrierInvoice(
        organization_id=org.id,
        shipment_id=shipment.id,
        carrier_name="Old Dominion",
        invoice_number="INV-DISPUTE-001",
        total_billed_amount=1500.00,
        linehaul_amount=1200.00,
        fuel_amount=300.00,
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv


@pytest.fixture()
def finding(db, org, invoice, shipment):
    f = AuditFindingRecord(
        organization_id=org.id,
        invoice_id=invoice.id,
        shipment_id=shipment.id,
        rule_id="RULE_02_LINEHAUL_MISMATCH",
        rule_name="Wrong Linehaul Rate",
        severity="high",
        discrepancy_amount=300.00,  # $300 overcharge — MUST flow to dispute exactly
        reason="Billed linehaul $1200 exceeds contracted rate $900",
        confidence=0.95,
        recommended_action="dispute",
    )
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


def test_carrier_contact_crud(db, org):
    """CarrierContact can be created and retrieved by carrier name."""
    repo = CarrierContactRepository(db)

    # Create
    contact = repo.create_contact(
        organization_id=org.id,
        carrier_name="Old Dominion",
        billing_email="billing@odfl.com",
        contact_name="OD Billing Dept",
    )
    assert contact.id is not None
    assert contact.billing_email == "billing@odfl.com"
    assert contact.is_verified is True

    # Retrieve
    fetched = repo.get_verified_contact(org.id, "Old Dominion")
    assert fetched is not None
    assert fetched.billing_email == "billing@odfl.com"

    # Missing carrier returns None — NEVER guess
    missing = repo.get_verified_contact(org.id, "Unknown Carrier XYZ")
    assert missing is None, "Must return None for unknown carrier — never synthesize"


def test_carrier_dispute_creation_amount_from_finding(db, org, invoice, shipment, finding):
    """CarrierDispute.disputed_amount must equal AuditFindingRecord.discrepancy_amount exactly."""
    repo = DisputeRepository(db)

    # The disputed amount is taken directly from the finding — never from LLM
    disputed_amount = finding.discrepancy_amount  # $300.00
    assert disputed_amount == 300.00

    dispute = repo.create_dispute(
        organization_id=org.id,
        dispute_number="DSP-001-01",
        invoice_id=invoice.id,
        shipment_id=shipment.id,
        audit_finding_ids=[str(finding.id)],
        carrier_name="Old Dominion",
        disputed_amount=disputed_amount,  # COPIED from finding, never from LLM
        expected_amount=900.00,
        billed_amount=1200.00,
        idempotency_key="dispute-inv-dispute-001-finding-001",
    )

    assert dispute.id is not None
    assert float(dispute.disputed_amount) == 300.00  # Must exactly match finding
    assert dispute.status == "draft"
    assert dispute.approval_status == "pending"
    assert dispute.recipient_email is None  # Not yet resolved


def test_dispute_recovery_requires_proof_before_verification(db, org, invoice, shipment, finding):
    """DisputeRecovery.approved_recovery CANNOT be set without proof_type recorded first."""
    dispute_repo = DisputeRepository(db)
    recovery_repo = RecoveryRepository(db)

    dispute = dispute_repo.create_dispute(
        organization_id=org.id,
        dispute_number="DSP-002-01",
        invoice_id=invoice.id,
        shipment_id=shipment.id,
        audit_finding_ids=[str(finding.id)],
        carrier_name="Old Dominion",
        disputed_amount=300.00,
        expected_amount=900.00,
        billed_amount=1200.00,
        idempotency_key="dispute-recovery-proof-test",
    )

    recovery = recovery_repo.create_recovery_entry(
        organization_id=org.id,
        recovery_number="REC-002-01",
        dispute_id=dispute.id,
        invoice_id=invoice.id,
        disputed_amount=300.00,
    )

    # approved_recovery must be NULL initially
    assert recovery.approved_recovery is None
    assert recovery.verified_at is None

    # GUARD: cannot verify without proof
    with pytest.raises(RecoveryIntegrityError, match="no proof_type recorded"):
        recovery_repo.verify_and_mark_recovered(
            organization_id=org.id,
            recovery_id=recovery.id,
            approved_amount=300.00,
            verified_by="human-broker-1",
        )


def test_recovery_revenue_share_calculation(db, org, invoice, shipment, finding):
    """Revenue share (15%) is computed deterministically by code, never by LLM."""
    dispute_repo = DisputeRepository(db)
    recovery_repo = RecoveryRepository(db)

    dispute = dispute_repo.create_dispute(
        organization_id=org.id,
        dispute_number="DSP-003-01",
        invoice_id=invoice.id,
        shipment_id=shipment.id,
        audit_finding_ids=[str(finding.id)],
        carrier_name="Old Dominion",
        disputed_amount=300.00,
        expected_amount=None,
        billed_amount=None,
        idempotency_key="dispute-revenue-share-test",
    )

    recovery = recovery_repo.create_recovery_entry(
        organization_id=org.id,
        recovery_number="REC-003-01",
        dispute_id=dispute.id,
        invoice_id=invoice.id,
        disputed_amount=300.00,
    )

    # Record proof first
    recovery_repo.record_proof(
        organization_id=org.id,
        recovery_id=recovery.id,
        proof_type="credit_memo",
        carrier_response_type="credit_issued",
        proof_reference="CM-OD-2024-99",
    )

    # Now verify
    verified = recovery_repo.verify_and_mark_recovered(
        organization_id=org.id,
        recovery_id=recovery.id,
        approved_amount=300.00,
        verified_by="admin",
    )

    assert float(verified.approved_recovery) == 300.00
    assert verified.verified_at is not None
    # 15% of $300.00 = $45.00 — computed deterministically
    assert float(verified.revenue_share_amount) == pytest.approx(45.00, rel=1e-4)


def test_dispute_idempotency(db, org, invoice, shipment, finding):
    """Same idempotency_key returns existing dispute, no duplicate created."""
    repo = DisputeRepository(db)
    idemp_key = "dispute-idempotency-test-001"

    dispute1 = repo.create_dispute(
        organization_id=org.id,
        dispute_number="DSP-004-01",
        invoice_id=invoice.id,
        shipment_id=shipment.id,
        audit_finding_ids=[str(finding.id)],
        carrier_name="Old Dominion",
        disputed_amount=300.00,
        expected_amount=None,
        billed_amount=None,
        idempotency_key=idemp_key,
    )

    # Second lookup via idempotency key should return the same dispute
    existing = repo.get_by_idempotency_key(org.id, idemp_key)
    assert existing is not None
    assert existing.id == dispute1.id

    # Count disputes for this invoice — must be exactly 1
    disputes = repo.get_disputes_for_invoice(org.id, invoice.id)
    assert len(disputes) == 1
