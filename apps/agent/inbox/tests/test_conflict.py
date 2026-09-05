"""Test handling of conflicting freight information and policy threshold violations."""

from datetime import datetime, timedelta, timezone
import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.agent.inbox.policies import MAX_ALLOWED_ETA_DELAY_HOURS
from apps.agent.inbox.service import run_inbox_agent
from packages.storage.db import Base
from packages.storage.repositories.organizations import OrganizationRepository
from packages.storage.repositories.shipments import ShipmentRepository
from packages.storage.repositories.tasks import TaskRepository


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
async def test_conflicting_severe_eta_delay_escalates(test_db, test_org):
    """When a carrier reports a delay exceeding MAX_ALLOWED_ETA_DELAY_HOURS (48h),
    policy halts automatic update, tags approval_state as needs_human,
    and creates a review task.
    """
    shipment_repo = ShipmentRepository(test_db)
    task_repo = TaskRepository(test_db)

    now = datetime.now(timezone.utc)
    base_eta = now + timedelta(days=1)
    severe_delayed_eta = now + timedelta(days=6)  # 5 days = 120 hours delay (> 48h)

    shipment = shipment_repo.create(
        organization_id=test_org.id,
        shipment_number="SHP-CONFLICT-1",
        load_id="LOAD-7700",
        status="in_transit",
        eta=base_eta,
    )

    output = await run_inbox_agent(
        organization_id=str(test_org.id),
        trigger_event_id="evt-conflict-eta",
        thread_id="thread-conflict-1",
        sender="carrier@delays.com",
        subject="LOAD-7700 - Major Breakdown & Delay",
        body_text=f"Engine failure in Nebraska. Revised estimated arrival delayed to {severe_delayed_eta.isoformat()}.",
        db=test_db,
    )

    # Must escalate to human due to severe SLA delay
    assert output.approval_state == "needs_human"
    assert output.terminal_outcome == "needs_human"

    # Shipment ETA must NOT have been silently updated
    refreshed = shipment_repo.get_by_id(test_org.id, shipment.id)
    assert refreshed.eta.replace(tzinfo=timezone.utc) == base_eta

    # Human task was created
    tasks = task_repo.list_by_shipment(test_org.id, shipment.id)
    assert len(tasks) >= 1
    assert "review" in tasks[0].task_type.lower()
