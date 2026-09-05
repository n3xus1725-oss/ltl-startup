"""Test Happy Path for the 3 high-confidence workflows in Inbox Action Agent."""

from datetime import datetime, timezone
import uuid
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.agent.inbox.service import run_inbox_agent
from packages.domain.models import AgentRun, Shipment
from packages.storage.db import Base
from packages.storage.repositories.agent_runs import AgentRunRepository
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
async def test_workflow_a_pickup_confirmation(test_db, test_org):
    """Workflow A: Pickup confirmation.
    - Parse carrier message
    - Resolve shipment
    - Update status to picked_up with confirmed pickup timestamp
    - Store evidence in shipment event history
    - Send notice/reply to customer/carrier thread
    - Explicit terminal outcome: completed
    """
    shipment_repo = ShipmentRepository(test_db)
    shipment = shipment_repo.create(
        organization_id=test_org.id,
        shipment_number="SHP-5001",
        load_id="5821",
        carrier_name="Swift Freight",
        status="created",
    )

    output = await run_inbox_agent(
        organization_id=str(test_org.id),
        trigger_event_id="evt-pickup-001",
        thread_id="thread-pickup-5821",
        sender="driver@swift.com",
        subject="Load 5821 - Freight Picked Up",
        body_text="Driver has departed shipper. Freight was picked up successfully at 14:00.",
        db=test_db,
    )

    assert output.terminal_outcome == "completed"
    assert output.entity_id == str(shipment.id)
    assert output.approval_state == "auto_approved"
    assert output.confidence >= 0.85
    assert len(output.tool_calls) >= 1

    # Verify database state was updated
    refreshed = shipment_repo.get_by_id(test_org.id, shipment.id)
    assert refreshed.status == "picked_up"
    assert refreshed.pickup_date is not None

    # Verify agent run record was persisted in database with all required fields
    agent_repo = AgentRunRepository(test_db)
    agent_run = agent_repo.get_by_id(test_org.id, uuid.UUID(output.run_id))
    assert agent_run is not None
    assert agent_run.trigger_event_id == "evt-pickup-001"
    assert agent_run.entity_id == str(shipment.id)
    assert agent_run.status == "completed"
    assert agent_run.decision is not None
    assert agent_run.started_at is not None
    assert agent_run.completed_at is not None


@pytest.mark.asyncio
async def test_workflow_b_eta_update(test_db, test_org):
    """Workflow B: ETA update.
    - Identify shipment
    - Update ETA
    - Preserve prior ETA as history
    - Customer notice sent via thread
    - Explicit terminal outcome: completed
    """
    shipment_repo = ShipmentRepository(test_db)
    prior_eta = datetime(2026, 9, 6, 12, 0, 0, tzinfo=timezone.utc)
    shipment = shipment_repo.create(
        organization_id=test_org.id,
        shipment_number="SHP-5002",
        carrier_reference="PRO 9823411",
        status="in_transit",
        eta=prior_eta,
    )

    output = await run_inbox_agent(
        organization_id=str(test_org.id),
        trigger_event_id="evt-eta-002",
        thread_id="thread-eta-9823411",
        sender="dispatch@carrier.com",
        subject="PRO 9823411 - Updated ETA",
        body_text="New ETA is tomorrow at 18:00 due to slight weather delay.",
        db=test_db,
    )

    assert output.terminal_outcome == "completed"
    assert output.entity_id == str(shipment.id)
    assert output.approval_state == "auto_approved"

    # Verify shipment ETA updated and prior ETA preserved in history
    refreshed = shipment_repo.get_by_id(test_org.id, shipment.id)
    assert refreshed.eta is not None
    meta = refreshed.metadata_payload or {}
    assert "eta_history" in meta
    assert "2026-09-06T12:00:00" in meta["eta_history"][0]["prior_eta"]


@pytest.mark.asyncio
async def test_workflow_c_missing_information_request(test_db, test_org):
    """Workflow C: Missing-information request.
    - Identify missing required field (e.g. piece count, weight)
    - Send standardized request to sender
    - Create follow-up task
    - Explicit terminal outcome: completed
    """
    shipment_repo = ShipmentRepository(test_db)
    task_repo = TaskRepository(test_db)

    shipment = shipment_repo.create(
        organization_id=test_org.id,
        shipment_number="SHP-5003",
        load_id="LOAD-5003",
        status="created",
    )

    output = await run_inbox_agent(
        organization_id=str(test_org.id),
        trigger_event_id="evt-missing-info-003",
        thread_id="thread-missing-5003",
        sender="shipper@client.com",
        subject="LOAD-5003 Shipment Intake",
        body_text="We have staged the pallets, but we are missing paperwork. Please provide weight and piece count.",
        db=test_db,
    )

    assert output.terminal_outcome == "completed"
    assert output.approval_state == "auto_approved"

    # Verify follow-up task was created
    tasks = task_repo.list_by_shipment(test_org.id, shipment.id)
    assert len(tasks) >= 1
    assert tasks[0].task_type == "missing_information"
    assert tasks[0].status == "pending"
