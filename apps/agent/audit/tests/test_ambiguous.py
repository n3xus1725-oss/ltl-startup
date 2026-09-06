"""Test Ambiguous Scenarios for Billing Audit Agent: Unknown shipments and missing contracts."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.agent.audit.service import run_audit_agent
from packages.storage.db import Base
from packages.storage.repositories.organizations import OrganizationRepository


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
async def test_unresolvable_shipment_escalation(test_db, test_org):
    """Rule 9 & Non-Negotiable Rule 7: Unknown/ambiguous shipments escalate instead of guessing.
    - An invoice referencing an unknown Load 99999 cannot be deterministically matched.
    - Agent must escalate to human operator (terminal_outcome: needs_human).
    """
    ambiguous_text = """CARRIER FREIGHT INVOICE
Invoice #: INV-UNKNOWN-999
Carrier: Ghost Freight Lines
Load #: 9999999
Total Due: $2,500.00
"""

    output = await run_audit_agent(
        organization_id=str(test_org.id),
        db=test_db,
        invoice_text=ambiguous_text,
        trigger_event_id="evt-ambiguous-001",
    )

    assert output.terminal_outcome == "needs_human"
    assert output.approval_state == "needs_human"
    assert output.next_workflow == "needs_human"
    assert output.confidence < 0.8
    assert "Could not unambiguously link Invoice" in (output.decision or "")
