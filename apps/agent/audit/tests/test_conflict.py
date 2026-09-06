"""Test Conflict Scenarios for Billing Audit Agent: Rate overcharges and missing receipts."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.agent.audit.service import run_audit_agent
from packages.storage.db import Base
from packages.storage.repositories.contracts import RateContractRepository
from packages.storage.repositories.invoices import InvoiceRepository
from packages.storage.repositories.organizations import OrganizationRepository
from packages.storage.repositories.shipments import ShipmentRepository


@pytest.fixture(scope="function")
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def test_org(test_db):
    org_repo = OrganizationRepository(test_db)
    return org_repo.create("Apex Global Logistics", "apex-global")


@pytest.mark.asyncio
async def test_linehaul_overcharge_dispute_routing(test_db, test_org):
    """Test linehaul overcharge detection and dispute routing.
    - Contract base rate is $1,650.00.
    - Carrier bills $1,850.00 linehaul ($200.00 overcharge).
    - Deterministic rule flags RULE_02_LINEHAUL_MISMATCH.
    - Routed to dispute_review, invoice marked disputed in DB.
    - LLM explanation generated with cited contract reference.
    """
    contract_repo = RateContractRepository(test_db)
    shp_repo = ShipmentRepository(test_db)
    inv_repo = InvoiceRepository(test_db)

    contract_repo.create_contract(
        organization_id=test_org.id,
        carrier_name="Estes Express Lines",
        contract_number="CTR-ESTES-2026",
        base_rate=1650.0,
        minimum_charge=500.0,
        rate_type="flat",
    )

    shp = shp_repo.create(
        organization_id=test_org.id,
        shipment_number="LOAD-9002",
        load_id="9002",
        status="delivered",
        carrier_name="Estes Express Lines",
        total_charges=1650.0,
    )
    shp_repo.update_canonical(
        test_org.id,
        shp.id,
        canonical_data={
            "organization_id": str(test_org.id),
            "shipment_number": "LOAD-9002",
            "status": "delivered",
            "pricing": {"agreed_linehaul": 1650.0, "agreed_total": 1650.0},
        },
        provenance_ledger={"entries": {}},
    )

    overcharge_text = """CARRIER FREIGHT INVOICE
Invoice #: INV-OVER-202
Carrier: Estes Express Lines
Load #: 9002
Linehaul Rate: $1,850.00
Total Due: $1,850.00
"""

    output = await run_audit_agent(
        organization_id=str(test_org.id),
        db=test_db,
        invoice_text=overcharge_text,
        trigger_event_id="evt-conflict-002",
    )

    assert output.is_clean is False
    assert output.total_discrepancy >= 200.0
    assert any(f.get("rule_id") == "RULE_02_LINEHAUL_MISMATCH" for f in output.findings)
    assert output.findings_count >= 1
    assert output.classification == "discrepancies_flagged"
    assert output.next_workflow == "dispute_review"
    assert output.approval_state == "dispute_flagged"
    assert output.explanation is not None

    # Check DB invoice status
    test_db.expire_all()
    inv = inv_repo.get_by_number(test_org.id, "Estes Express Lines", "INV-OVER-202")
    assert inv is not None
    assert inv.status == "disputed"


@pytest.mark.asyncio
async def test_missing_lumper_receipt_hold_routing(test_db, test_org):
    """Test unsupported accessorial holding for documentation (Phase 3.5 quality control).
    - Carrier bills $175.00 lumper fee with no receipt.
    - Deterministic rule flags RULE_04_UNSUPPORTED_ACCESSORIAL.
    - Routes to hold_for_documents with approval_state: held.
    """
    contract_repo = RateContractRepository(test_db)
    shp_repo = ShipmentRepository(test_db)

    contract_repo.create_contract(
        organization_id=test_org.id,
        carrier_name="Estes Express Lines",
        contract_number="CTR-ESTES-2026",
        base_rate=1650.0,
        rate_type="flat",
        accessorial_schedule={},
    )

    shp = shp_repo.create(
        organization_id=test_org.id,
        shipment_number="LOAD-9003",
        load_id="9003",
        status="delivered",
        carrier_name="Estes Express Lines",
        total_charges=1650.0,
    )
    shp_repo.update_canonical(
        test_org.id,
        shp.id,
        canonical_data={
            "organization_id": str(test_org.id),
            "shipment_number": "LOAD-9003",
            "status": "delivered",
            "pricing": {"agreed_linehaul": 1650.0, "agreed_total": 1650.0},
        },
        provenance_ledger={"entries": {}},
    )

    lumper_text = """CARRIER FREIGHT INVOICE
Invoice #: INV-LUMP-303
Carrier: Estes Express Lines
Load #: 9003
Linehaul Rate: $1,650.00
Lumper Fee: $175.00
Total Due: $1,825.00
"""

    output = await run_audit_agent(
        organization_id=str(test_org.id),
        db=test_db,
        invoice_text=lumper_text,
        trigger_event_id="evt-lumper-003",
    )

    assert output.is_clean is False
    assert any(f.get("rule_id") == "RULE_04_UNSUPPORTED_ACCESSORIAL" for f in output.findings)
    assert output.next_workflow == "hold_for_documents"
    assert output.approval_state == "held"
    assert output.terminal_outcome == "needs_human"
