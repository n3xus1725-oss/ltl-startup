"""Test Idempotency for Billing Audit Agent: Repeated triggers cannot duplicate runs."""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.agent.audit.service import run_audit_agent
from packages.domain.models import AgentRun
from packages.storage.db import Base
from packages.storage.repositories.contracts import RateContractRepository
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
async def test_audit_agent_idempotent_trigger_execution(test_db, test_org):
    """Rule 5 & Non-Negotiable Rule 24: Repeated triggers must not execute duplicate actions or runs.
    - Run agent with trigger_event_id: 'evt-idemp-001'.
    - Run agent again with the exact same trigger_event_id.
    - Assert that only ONE AgentRun database row exists.
    - Assert that the second run returns the exact same run_id.
    """
    contract_repo = RateContractRepository(test_db)
    shp_repo = ShipmentRepository(test_db)

    contract_repo.create_contract(
        organization_id=test_org.id,
        carrier_name="Estes Express Lines",
        contract_number="CTR-ESTES-2026",
        base_rate=1650.0,
        rate_type="flat",
    )

    shp = shp_repo.create(
        organization_id=test_org.id,
        shipment_number="LOAD-9004",
        load_id="9004",
        status="delivered",
        carrier_name="Estes Express Lines",
        total_charges=1650.0,
    )
    shp_repo.update_canonical(
        test_org.id,
        shp.id,
        canonical_data={
            "organization_id": str(test_org.id),
            "shipment_number": "LOAD-9004",
            "status": "delivered",
            "pricing": {"agreed_linehaul": 1650.0, "agreed_total": 1650.0},
        },
        provenance_ledger={"entries": {}},
    )

    invoice_text = """CARRIER FREIGHT INVOICE
Invoice #: INV-IDEMP-404
Carrier: Estes Express Lines
Load #: 9004
Linehaul Rate: $1,650.00
Total Due: $1,650.00
"""

    trigger_id = "evt-idemp-001"

    # 1. First execution
    out1 = await run_audit_agent(
        organization_id=str(test_org.id),
        db=test_db,
        invoice_text=invoice_text,
        trigger_event_id=trigger_id,
    )

    # 2. Second execution with identical trigger_id
    out2 = await run_audit_agent(
        organization_id=str(test_org.id),
        db=test_db,
        invoice_text=invoice_text,
        trigger_event_id=trigger_id,
    )

    # 3. Assertions
    assert out1.run_id == out2.run_id
    assert out2.terminal_outcome == "completed"

    # Verify only 1 AgentRun row exists in DB
    runs = test_db.execute(
        select(AgentRun).where(
            AgentRun.organization_id == test_org.id,
            AgentRun.trigger_event_id == trigger_id,
        )
    ).scalars().all()

    assert len(runs) == 1
