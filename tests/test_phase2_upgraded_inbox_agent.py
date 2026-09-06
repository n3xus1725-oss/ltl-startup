"""Phase 2.6 Test Suite — Upgraded Inbox Action Agent (Source-of-Truth & Conflict Verification)."""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.agent.inbox.service import run_inbox_agent
from packages.domain.canonical import (
    CanonicalShipment,
    FreightDetails,
    ShipmentParties,
    ShipmentParty,
    ShipmentPricing,
)
from packages.domain.models import ShipmentConflict
from packages.domain.provenance import FieldProvenanceRecord, ProvenanceLedger
from packages.storage.db import Base
from packages.storage.repositories.organizations import OrganizationRepository
from packages.storage.repositories.shipments import ShipmentRepository
from packages.storage.repositories.tasks import TaskRepository


@pytest.fixture
def db_session():
    """Create fresh isolated in-memory SQLite database."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def test_org(db_session):
    org_repo = OrganizationRepository(db_session)
    return org_repo.create("Apex Logistics", "apex-upgraded-inbox")


@pytest.mark.asyncio
async def test_multi_source_progressive_reconstruction(db_session, test_org):
    """Phase 2 Exit Criterion 1: A shipment can be reconstructed from multiple information sources."""
    shp_repo = ShipmentRepository(db_session)

    # 1. Create baseline shipment
    shp = shp_repo.create(
        organization_id=test_org.id,
        shipment_number="LOAD-9001",
        load_id="9001",
        status="booked",
    )

    # Initialize canonical shipment
    canonical = CanonicalShipment(
        organization_id=str(test_org.id),
        shipment_number="LOAD-9001",
        status="booked",
        parties=ShipmentParties(
            shipper=ShipmentParty(name="Chicago Metals Inc"),
            carrier=ShipmentParty(name="Estes Express"),
        ),
        pricing=ShipmentPricing(agreed_linehaul=1650.0, total_agreed_rate=1650.0),
    )
    ledger = ProvenanceLedger()
    ledger.record_assertion(FieldProvenanceRecord(
        field="pricing.agreed_total",
        value=1650.0,
        source="rate_confirmation",
        source_id="RC-9001",
        authority=100,
        confidence=1.0,
    ))
    shp_repo.update_canonical(test_org.id, shp.id, canonical.model_dump(), ledger.to_dict())

    # 2. Ingest BOL via email with attachment
    bol_text = """
    UNIFORM STRAIGHT BILL OF LADING
    BOL #: BOL-9001-A
    Load #: 9001
    Carrier Name: Estes Express Lines
    Trailer #: TR-9988
    Total Weight: 16,450 lbs
    Pallet Count: 12
    """
    res_bol = await run_inbox_agent(
        organization_id=str(test_org.id),
        trigger_event_id="evt-bol-ingest",
        sender="dispatch@estes-express.com",
        subject="BOL for Load 9001",
        body_text=f"Please find the attached BOL for Load 9001:\n{bol_text}",
        db=db_session,
    )
    assert res_bol.success is True

    # 3. Verify canonical state is enriched from BOL without losing rate confirmation data
    db_session.expire_all()
    updated_canonical = shp_repo.get_canonical(test_org.id, shp.id)
    updated_ledger = shp_repo.get_provenance_ledger(test_org.id, shp.id)

    assert updated_canonical is not None
    # Prior Rate Con pricing preserved
    assert updated_canonical["pricing"]["agreed_total"] == 1650.0
    # New BOL weight & pallets added
    assert updated_canonical["freight_details"]["total_weight_lbs"] == 16450.0
    assert updated_canonical["freight_details"]["pallet_count"] == 12

    # Verify provenance ledger records both sources
    assert updated_ledger.get_active("pricing.agreed_total").source == "rate_confirmation"
    assert updated_ledger.get_active("freight_details.total_weight_lbs").source == "bol"


@pytest.mark.asyncio
async def test_conflict_guardrail_prevents_silent_overwrite(db_session, test_org):
    """Phase 2 Exit Criterion 2: Conflicts are surfaced, never silently overwritten."""
    shp_repo = ShipmentRepository(db_session)
    task_repo = TaskRepository(db_session)

    # Create shipment with verified Rate Confirmation at $1,650
    shp = shp_repo.create(
        organization_id=test_org.id,
        shipment_number="LOAD-3344",
        load_id="3344",
        status="booked",
        total_charges=1650.0,
    )
    canonical = CanonicalShipment(
        organization_id=str(test_org.id),
        shipment_number="LOAD-3344",
        status="booked",
        pricing=ShipmentPricing(agreed_linehaul=1650.0, total_agreed_rate=1650.0),
    )
    ledger = ProvenanceLedger()
    ledger.record_assertion(FieldProvenanceRecord(
        field="pricing.agreed_total",
        value=1650.0,
        source="rate_confirmation",
        source_id="RC-3344",
        authority=100,
        confidence=1.0,
    ))
    shp_repo.update_canonical(test_org.id, shp.id, canonical.model_dump(), ledger.to_dict())

    # Carrier sends an invoice for $1,850 ($200 discrepancy)
    invoice_text = """
    INVOICE
    Invoice #: INV-9988
    Load #: 3344
    Linehaul Rate: $1,850.00
    Total Due: $1,850.00
    """
    res = await run_inbox_agent(
        organization_id=str(test_org.id),
        trigger_event_id="evt-inv-conflict",
        sender="billing@estes-express.com",
        subject="Invoice for Load 3344",
        body_text=f"Please remit payment for Load 3344.\n{invoice_text}",
        db=db_session,
    )

    # 1. Action halted: terminal outcome must be needs_human
    assert res.terminal_outcome == "needs_human"
    assert res.approval_state == "needs_human"
    assert "conflict" in (res.reasoning or "").lower()

    # 2. Canonical shipment pricing is NEVER silently overwritten
    db_session.expire_all()
    preserved_canonical = shp_repo.get_canonical(test_org.id, shp.id)
    assert preserved_canonical["pricing"]["agreed_total"] == 1650.0

    # 3. Conflict row created in shipment_conflicts
    conflict_stmt = select(ShipmentConflict).where(
        ShipmentConflict.organization_id == test_org.id,
        ShipmentConflict.shipment_id == shp.id,
    )
    conflicts = list(db_session.scalars(conflict_stmt).all())
    assert len(conflicts) > 0
    assert conflicts[0].conflict_type == "value_mismatch"
    assert conflicts[0].severity == "critical"
    assert conflicts[0].recommended_workflow == "rate_dispute_agent"

    # 4. Human review task created
    tasks = task_repo.list_by_shipment(test_org.id, shp.id)
    assert len(tasks) > 0
    assert "Conflict" in tasks[0].title


@pytest.mark.asyncio
async def test_later_agents_can_read_canonical_context(db_session, test_org):
    """Phase 2 Exit Criterion 3: All later agents can read the same canonical shipment context."""
    from packages.retrieval.engine import ShipmentRetrievalEngine

    shp_repo = ShipmentRepository(db_session)
    shp = shp_repo.create(
        organization_id=test_org.id,
        shipment_number="LOAD-1122",
        load_id="1122",
        status="picked_up",
    )

    canonical = CanonicalShipment(
        organization_id=str(test_org.id),
        shipment_number="LOAD-1122",
        status="picked_up",
        parties=ShipmentParties(
            shipper=ShipmentParty(name="Detroit Steel"),
            consignee=ShipmentParty(name="Atlanta Auto Works"),
            carrier=ShipmentParty(name="Schneider National"),
        ),
        pricing=ShipmentPricing(agreed_linehaul=2100.0, total_agreed_rate=2100.0),
        freight_details=FreightDetails(total_weight_lbs=24000.0, pallet_count=18),
    )
    ledger = ProvenanceLedger()
    ledger.record_assertion(FieldProvenanceRecord(
        field="pricing.agreed_total",
        value=2100.0,
        source="rate_confirmation",
        source_id="RC-1122",
        authority=100,
    ))
    shp_repo.update_canonical(test_org.id, shp.id, canonical.model_dump(), ledger.to_dict())

    # Any future agent (Billing Audit, Dispute, Risk, Exception) calls ShipmentRetrievalEngine
    engine = ShipmentRetrievalEngine(db_session)
    ctx = engine.get_canonical_context(test_org.id, shp.id)

    assert ctx is not None
    assert ctx.canonical_data["shipment_number"] == "LOAD-1122"
    assert ctx.canonical_data["parties"]["consignee"]["name"] == "Atlanta Auto Works"
    assert ctx.canonical_data["freight_details"]["total_weight_lbs"] == 24000.0
    assert ctx.canonical_data["pricing"]["agreed_total"] == 2100.0
    assert "pricing.agreed_total" in ctx.provenance_trail
