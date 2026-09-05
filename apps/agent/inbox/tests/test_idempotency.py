"""Test idempotency guarantees for Inbox Action Agent runs."""

import uuid
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.agent.inbox.service import run_inbox_agent
from packages.domain.models import AgentRun, ShipmentEvent
from packages.storage.db import Base
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
async def test_duplicate_inbound_event_idempotency(test_db, test_org):
    """Rule 5: Every automated action must be idempotent.
    Reprocessing the same event must not duplicate an action or agent run.
    """
    shipment_repo = ShipmentRepository(test_db)
    shipment = shipment_repo.create(
        organization_id=test_org.id,
        shipment_number="SHP-IDEMP-1",
        load_id="LOAD-5500",
        status="created",
    )

    trigger_id = "webhook-inbound-evt-999"

    # First run
    output_1 = await run_inbox_agent(
        organization_id=str(test_org.id),
        trigger_event_id=trigger_id,
        thread_id="thread-idemp",
        sender="carrier@freight.com",
        subject="LOAD-5500 - Pickup Confirmed",
        body_text="Driver has loaded shipment LOAD-5500.",
        db=test_db,
    )

    assert output_1.terminal_outcome == "completed"
    first_run_id = output_1.run_id

    # Count events created in first run
    events_after_first = shipment_repo.get_events(test_org.id, shipment.id)
    first_event_count = len(events_after_first)
    assert first_event_count >= 1

    # Second run with EXACT same trigger_event_id
    output_2 = await run_inbox_agent(
        organization_id=str(test_org.id),
        trigger_event_id=trigger_id,
        thread_id="thread-idemp",
        sender="carrier@freight.com",
        subject="LOAD-5500 - Pickup Confirmed",
        body_text="Driver has loaded shipment LOAD-5500.",
        db=test_db,
    )

    # Must return same run_id and not duplicate actions
    assert output_2.run_id == first_run_id
    assert output_2.terminal_outcome == "completed"

    # Verify no duplicate AgentRun was created in database
    runs = list(
        test_db.execute(
            select(AgentRun).where(
                AgentRun.organization_id == test_org.id,
                AgentRun.trigger_event_id == trigger_id,
            )
        ).scalars().all()
    )
    assert len(runs) == 1

    # Verify no duplicate events were appended
    events_after_second = shipment_repo.get_events(test_org.id, shipment.id)
    assert len(events_after_second) == first_event_count
