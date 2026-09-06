"""Test Happy Path for Billing Audit Agent: Clean invoice auto-approval."""

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
async def test_clean_invoice_auto_approval_zero_cost(test_db, test_org):
    """Happy Path: Clean invoice matches contract and delivery receipts perfectly.
    - All 10 deterministic audit checks pass.
    - LLM call is bypassed ($0.00 cost, 0 tokens).
    - Invoice is auto-approved and scheduled for payment.
    - Explicit terminal outcome: completed.
    """
    shp_repo = ShipmentRepository(test_db)
    contract_repo = RateContractRepository(test_db)
    inv_repo = InvoiceRepository(test_db)

    # 1. Create baseline rate contract
    contract_repo.create_contract(
        organization_id=test_org.id,
        carrier_name="Estes Express Lines",
        contract_number="CTR-ESTES-2026",
        base_rate=1650.0,
        minimum_charge=500.0,
        rate_type="flat",
        fuel_schedule={"type": "percent", "base_rate_percent": 15.0},
    )

    # 2. Create delivered shipment
    shp = shp_repo.create(
        organization_id=test_org.id,
        shipment_number="LOAD-9001",
        load_id="9001",
        status="delivered",
        carrier_name="Estes Express Lines",
        total_charges=1897.50,
    )
    shp_repo.update_canonical(
        test_org.id,
        shp.id,
        canonical_data={
            "organization_id": str(test_org.id),
            "shipment_number": "LOAD-9001",
            "status": "delivered",
            "pricing": {"agreed_linehaul": 1650.0, "fuel_surcharge": 247.50, "agreed_total": 1897.50},
            "freight_details": {"total_weight_lbs": 16450.0, "pallet_count": 12, "freight_class": "70"},
        },
        provenance_ledger={"entries": {}},
    )

    # 3. Ingest clean invoice matching contract (Linehaul $1,650 + Fuel $247.50 = Total $1,897.50)
    clean_text = """CARRIER FREIGHT INVOICE
Invoice #: INV-CLEAN-101
Carrier: Estes Express Lines
Load #: 9001
Linehaul Rate: $1,650.00
Fuel Surcharge: $247.50
Total Due: $1,897.50
"""

    output = await run_audit_agent(
        organization_id=str(test_org.id),
        db=test_db,
        invoice_text=clean_text,
        trigger_event_id="evt-happy-path-101",
    )

    # 4. Assertions
    assert output.terminal_outcome == "completed"
    assert output.is_clean is True
    assert output.total_discrepancy == 0.0
    assert output.findings_count == 0
    assert output.approval_state == "auto_approved"
    assert output.next_workflow == "payment_scheduled"
    assert output.confidence == 1.0

    # Phase 3.6 Cost Optimization assertion: zero LLM cost for clean invoices
    assert output.cost_estimate == 0.0
    assert output.prompt_tokens == 0
    assert output.model_used == "deterministic-bypassed"

    # Verify DB invoice status updated to approved
    test_db.expire_all()
    inv_in_db = inv_repo.get_by_number(test_org.id, "Estes Express Lines", "INV-CLEAN-101")
    assert inv_in_db is not None
    assert inv_in_db.status == "approved"
