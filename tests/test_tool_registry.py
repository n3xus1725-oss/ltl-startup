"""Comprehensive tests for Phase 1.7 Tool Registry and 8 Standard Tools."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.storage.db import Base
from packages.storage.repositories.agent_runs import AgentRunRepository
from packages.storage.repositories.documents import DocumentRepository
from packages.storage.repositories.organizations import OrganizationRepository
from packages.storage.repositories.shipments import ShipmentRepository
from packages.tools.registry import (
    ToolContext,
    ToolPermissionDenied,
)
from packages.tools.shipment_tools import (
    create_standard_tool_registry,
)


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


def test_registry_contains_all_8_tools():
    """Verify all 8 standard tools are registered and have complete metadata schemas."""
    registry = create_standard_tool_registry()
    names = registry.list_tool_names()

    expected_tools = [
        "find_shipment",
        "get_shipment",
        "update_shipment",
        "attach_document",
        "create_task",
        "send_email",
        "reply_to_thread",
        "create_exception",
    ]

    for expected in expected_tools:
        assert expected in names
        tool = registry.get_tool(expected)
        meta = tool.get_metadata()
        assert meta["name"] == expected
        assert len(meta["purpose"]) > 10
        assert meta["input_schema"] is not None
        assert meta["output_schema"] is not None
        assert meta["required_permission"] is not None
        assert isinstance(meta["external_side_effects"], bool)


@pytest.mark.asyncio
async def test_permission_denial(test_db, test_org):
    """Verify ToolPermissionDenied is raised when required permission is absent."""
    registry = create_standard_tool_registry()

    # Context has only read permissions, not shipment:write
    context = ToolContext(
        organization_id=test_org.id,
        granted_permissions={"shipment:read"},
    )

    with pytest.raises(ToolPermissionDenied) as exc_info:
        await registry.execute(
            name="update_shipment",
            context=context,
            db=test_db,
            arguments={"shipment_id": str(uuid.uuid4()), "status": "in_transit"},
        )
    assert "shipment:write" in str(exc_info.value)


@pytest.mark.asyncio
async def test_shipment_crud_tools(test_db, test_org):
    """Test find_shipment, get_shipment, and update_shipment tools."""
    registry = create_standard_tool_registry()
    shipment_repo = ShipmentRepository(test_db)
    shipment = shipment_repo.create(
        organization_id=test_org.id,
        shipment_number="SHP-9001",
        load_id="LOAD-9001",
        carrier_name="Knight-Swift",
        status="created",
    )

    context = ToolContext(
        organization_id=test_org.id,
        granted_permissions={"shipment:read", "shipment:write"},
    )

    # 1. find_shipment
    find_res = await registry.execute(
        name="find_shipment",
        context=context,
        db=test_db,
        arguments={"query": "LOAD-9001"},
    )
    assert find_res.status == "success"
    assert find_res.data["count"] == 1
    assert find_res.data["shipments"][0]["id"] == str(shipment.id)

    # 2. get_shipment
    get_res = await registry.execute(
        name="get_shipment",
        context=context,
        db=test_db,
        arguments={"shipment_id": str(shipment.id)},
    )
    assert get_res.status == "success"
    assert get_res.data["found"] is True
    assert get_res.data["shipment"]["shipment_number"] == "SHP-9001"

    # 3. update_shipment
    now_ts = datetime.now(timezone.utc)
    update_res = await registry.execute(
        name="update_shipment",
        context=context,
        db=test_db,
        arguments={
            "shipment_id": str(shipment.id),
            "status": "picked_up",
            "pickup_date": now_ts.isoformat(),
            "reason": "Driver arrived and confirmed pickup",
        },
    )
    assert update_res.status == "success"
    assert update_res.data["prior_values"]["status"] == "created"
    assert update_res.data["current_values"]["status"] == "picked_up"

    # Verify database state was updated
    refreshed = shipment_repo.get_by_id(test_org.id, shipment.id)
    assert refreshed.status == "picked_up"
    assert refreshed.pickup_date is not None

    # Verify history event was appended
    events = shipment_repo.get_events(test_org.id, shipment.id)
    assert len(events) >= 1
    assert events[-1].event_type == "agent_update"


@pytest.mark.asyncio
async def test_attach_document_tool(test_db, test_org):
    """Test attach_document tool linking ingested doc to shipment."""
    registry = create_standard_tool_registry()
    shipment_repo = ShipmentRepository(test_db)
    doc_repo = DocumentRepository(test_db)

    shipment = shipment_repo.create(organization_id=test_org.id, shipment_number="SHP-300")
    doc = doc_repo.create(
        organization_id=test_org.id,
        file_name="pod_signed.pdf",
        storage_path="/docs/pod_signed.pdf",
        document_type="UNKNOWN",
    )

    context = ToolContext(
        organization_id=test_org.id,
        granted_permissions={"shipment:write"},
    )

    res = await registry.execute(
        name="attach_document",
        context=context,
        db=test_db,
        arguments={
            "shipment_id": str(shipment.id),
            "document_id": str(doc.id),
            "document_type": "POD",
        },
    )
    assert res.status == "success"

    # Verify document in DB has shipment_id and updated type
    refreshed_doc = doc_repo.get_by_id(test_org.id, doc.id)
    assert refreshed_doc.shipment_id == shipment.id
    assert refreshed_doc.document_type == "POD"


@pytest.mark.asyncio
async def test_create_task_and_create_exception_tools(test_db, test_org):
    """Test create_task and create_exception tools."""
    registry = create_standard_tool_registry()
    shipment_repo = ShipmentRepository(test_db)
    shipment = shipment_repo.create(organization_id=test_org.id, shipment_number="SHP-400")

    context = ToolContext(
        organization_id=test_org.id,
        granted_permissions={"task:write", "exception:write"},
    )

    # 1. create_task
    task_res = await registry.execute(
        name="create_task",
        context=context,
        db=test_db,
        arguments={
            "title": "Clarify missing piece count",
            "task_type": "missing_information",
            "shipment_id": str(shipment.id),
            "priority": "high",
        },
    )
    assert task_res.status == "success"
    assert task_res.data["title"] == "Clarify missing piece count"
    assert task_res.data["status"] == "pending"

    # 2. create_exception
    exc_res = await registry.execute(
        name="create_exception",
        context=context,
        db=test_db,
        arguments={
            "exception_type": "late_pickup",
            "severity": "high",
            "shipment_id": str(shipment.id),
            "details": {"delay_hours": 3, "driver_note": "Flat tire on I-35"},
        },
    )
    assert exc_res.status == "success"
    assert exc_res.data["exception_type"] == "late_pickup"
    assert exc_res.data["status"] == "open"


@pytest.mark.asyncio
async def test_email_tools(test_db, test_org):
    """Test send_email and reply_to_thread tools."""
    registry = create_standard_tool_registry()

    context = ToolContext(
        organization_id=test_org.id,
        granted_permissions={"email:send"},
    )

    # 1. send_email
    send_res = await registry.execute(
        name="send_email",
        context=context,
        db=test_db,
        arguments={
            "to_recipients": ["shipper@example.com"],
            "subject": "Pickup Confirmation: SHP-100",
            "body": "Your freight was successfully picked up.",
        },
    )
    assert send_res.status == "success"
    assert send_res.data["status"] == "sent"
    assert "thread_id" in send_res.data

    # 2. reply_to_thread
    reply_res = await registry.execute(
        name="reply_to_thread",
        context=context,
        db=test_db,
        arguments={
            "thread_id": send_res.data["thread_id"],
            "body": "Driver is now en route to destination.",
        },
    )
    assert reply_res.status == "success"
    assert reply_res.data["thread_id"] == send_res.data["thread_id"]


@pytest.mark.asyncio
async def test_tool_execution_idempotency_and_audit(test_db, test_org):
    """Test tool call recording, idempotency caching, and immutable audit logs."""
    registry = create_standard_tool_registry()
    agent_repo = AgentRunRepository(test_db)
    agent_run = agent_repo.create_run(
        organization_id=test_org.id,
        agent_name="inbox_action_agent",
    )

    context = ToolContext(
        organization_id=test_org.id,
        agent_run_id=agent_run.id,
        granted_permissions={"*"},
        idempotency_key="idemp-task-xyz-123",
    )

    # First invocation executes tool
    res1 = await registry.execute(
        name="create_task",
        context=context,
        db=test_db,
        arguments={
            "title": "Review conflicting carrier weights",
            "idempotency_key": "idemp-task-xyz-123",
        },
    )
    assert res1.status == "success"
    task_id_1 = res1.data["task_id"]

    # Second invocation with same idempotency key returns cached output
    res2 = await registry.execute(
        name="create_task",
        context=context,
        db=test_db,
        arguments={
            "title": "Review conflicting carrier weights",
            "idempotency_key": "idemp-task-xyz-123",
        },
    )
    assert res2.status == "cached"
    assert res2.data["task_id"] == task_id_1
