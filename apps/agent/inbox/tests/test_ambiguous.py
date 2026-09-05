"""Test handling of ambiguous freight messages by Inbox Action Agent."""

import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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
async def test_ambiguous_email_escalates_to_human_without_wrong_update(test_db, test_org):
    """Rule: When two shipments are plausible, never choose arbitrarily.
    Must mark as ambiguous, escalate with terminal outcome 'needs_human',
    create a review task, and mutate zero shipment states.
    """
    shipment_repo = ShipmentRepository(test_db)
    task_repo = TaskRepository(test_db)

    s1 = shipment_repo.create(
        organization_id=test_org.id,
        shipment_number="SHP-A",
        load_id="LOAD-9001",
        status="created",
    )
    s2 = shipment_repo.create(
        organization_id=test_org.id,
        shipment_number="SHP-B",
        load_id="LOAD-9002",
        status="created",
    )

    output = await run_inbox_agent(
        organization_id=str(test_org.id),
        trigger_event_id="evt-ambig-123",
        thread_id="thread-ambig-1",
        sender="carrier@freightline.com",
        subject="Status on LOAD-9001 and LOAD-9002",
        body_text="Driver has picked up freight for both LOAD-9001 and LOAD-9002.",
        db=test_db,
    )

    # 1. Verification of terminal outcome
    assert output.terminal_outcome == "needs_human"
    assert output.approval_state == "needs_human"

    # 2. Verify neither shipment was updated
    refreshed_s1 = shipment_repo.get_by_id(test_org.id, s1.id)
    refreshed_s2 = shipment_repo.get_by_id(test_org.id, s2.id)
    assert refreshed_s1.status == "created"
    assert refreshed_s2.status == "created"

    # 3. Verify a human review task was created
    pending_tasks = task_repo.list_all(test_org.id)
    assert len(pending_tasks) >= 1
    assert any(t.task_type == "review" for t in pending_tasks)
