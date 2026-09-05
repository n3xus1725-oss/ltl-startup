"""Test error handling and resilience when tools encounter failures."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.agent.inbox.service import run_inbox_agent
from packages.storage.db import Base
from packages.storage.repositories.organizations import OrganizationRepository
from packages.storage.repositories.shipments import ShipmentRepository
from packages.tools.registry import BaseTool
from packages.tools.shipment_tools import create_standard_tool_registry


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
async def test_agent_handles_tool_failure_gracefully(test_db, test_org):
    """When a tool fails execution (e.g. underlying service error),
    the agent catches the failure, logs the error in agent state,
    and sets terminal outcome to 'failed' rather than unhandled exception.
    """
    shipment_repo = ShipmentRepository(test_db)
    _ = shipment_repo.create(
        organization_id=test_org.id,
        shipment_number="SHP-FAIL-1",
        load_id="LOAD-FAIL-1",
        status="created",
    )

    # Custom registry with a failing update_shipment tool
    custom_registry = create_standard_tool_registry()

    class BrokenUpdateTool(BaseTool):
        name = "update_shipment"
        purpose = "Simulate failure"
        input_schema = custom_registry.get_tool("update_shipment").input_schema
        output_schema = custom_registry.get_tool("update_shipment").output_schema
        required_permission = "shipment:write"

        async def execute(self, context, db, input_data):
            raise RuntimeError("Database connection suddenly dropped during tool execution")

    custom_registry.register(BrokenUpdateTool())

    output = await run_inbox_agent(
        organization_id=str(test_org.id),
        trigger_event_id="evt-tool-fail-001",
        thread_id="thread-fail",
        sender="driver@carrier.com",
        subject="LOAD-FAIL-1 - Freight Picked Up",
        body_text="Driver has picked up freight.",
        db=test_db,
        tool_registry=custom_registry,
    )

    # Workflow gracefully completes graph with terminal_outcome 'failed'
    assert output.terminal_outcome == "failed"
    assert len(output.errors) >= 1
    assert "Database connection suddenly dropped" in output.errors[0] or "Tool execution verification failed" in output.errors[0]
