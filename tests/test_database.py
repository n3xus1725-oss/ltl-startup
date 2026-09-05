"""CRUD and idempotency tests for database models and repositories."""

import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from packages.storage.db import Base
from packages.storage.repositories import (
    OrganizationRepository,
    ShipmentRepository,
    MessageRepository,
    DocumentRepository,
    AuditLogRepository,
    AgentRunRepository,
)


@pytest.fixture(scope="function")
def db_session():
    """Create a fresh in-memory SQLite database for fast, isolated test execution."""
    test_engine = create_engine(
        "sqlite:///:memory:",
        echo=False,
    )
    Base.metadata.create_all(bind=test_engine)
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=test_engine)


def test_organization_crud(db_session):
    """Test creating and retrieving organizations."""
    repo = OrganizationRepository(db_session)
    org = repo.create(name="Acme Logistics", slug="acme-logistics")

    assert org.id is not None
    assert org.name == "Acme Logistics"
    assert org.slug == "acme-logistics"
    assert org.is_active is True

    fetched = repo.get_by_id(org.id)
    assert fetched is not None
    assert fetched.slug == "acme-logistics"

    by_slug = repo.get_by_slug("acme-logistics")
    assert by_slug is not None
    assert by_slug.id == org.id


def test_shipment_crud_and_multi_tenancy(db_session):
    """Test shipment creation, searching by identifiers, and tenant scoping."""
    org_repo = OrganizationRepository(db_session)
    org1 = org_repo.create("Tenant One", "tenant-one")
    org2 = org_repo.create("Tenant Two", "tenant-two")

    ship_repo = ShipmentRepository(db_session)
    shipment1 = ship_repo.create(
        organization_id=org1.id,
        shipment_number="SHP-1001",
        carrier_name="Old Dominion",
        carrier_reference="ODFL-999888",
        bol_number="BOL-777",
        load_id="LOAD-555",
        external_invoice_id="INV-333",
        status="created",
    )

    assert shipment1.id is not None
    assert shipment1.shipment_number == "SHP-1001"

    # Multi-tenant isolation test: org2 cannot query org1 shipment
    assert ship_repo.get_by_id(org2.id, shipment1.id) is None
    assert ship_repo.get_by_shipment_number(org2.id, "SHP-1001") is None

    # Org1 can query by shipment_number
    found = ship_repo.get_by_shipment_number(org1.id, "SHP-1001")
    assert found is not None
    assert found.id == shipment1.id

    # Org1 can find by candidate identifiers (PRO, BOL, load_id, invoice_id)
    matches_pro = ship_repo.find_by_identifier(org1.id, "ODFL-999888")
    assert len(matches_pro) == 1
    assert matches_pro[0].id == shipment1.id

    matches_bol = ship_repo.find_by_identifier(org1.id, "BOL-777")
    assert len(matches_bol) == 1

    matches_invoice = ship_repo.find_by_identifier(org1.id, "INV-333")
    assert len(matches_invoice) == 1

    # Update shipment status and ETA
    now = datetime.now(timezone.utc)
    updated = ship_repo.update_shipment(
        org1.id, shipment1.id, status="in_transit", eta=now
    )
    assert updated.status == "in_transit"
    assert updated.eta is not None


def test_shipment_events_idempotency(db_session):
    """Verify that adding the same event with an idempotency key does not create duplicates."""
    org_repo = OrganizationRepository(db_session)
    org = org_repo.create("Logistics Corp", "logistics-corp")

    ship_repo = ShipmentRepository(db_session)
    shipment = ship_repo.create(org.id, "SHP-2002", carrier_name="Estes")

    idemp_key = "evt-pickup-2002-v1"
    evt1 = ship_repo.add_event(
        organization_id=org.id,
        shipment_id=shipment.id,
        event_type="pickup_confirmed",
        source="email_agent",
        raw_payload={"note": "Driver picked up freight"},
        normalized_payload={"status": "picked_up"},
        idempotency_key=idemp_key,
    )
    assert evt1.id is not None

    # Adding the identical event again with the same idempotency_key
    evt2 = ship_repo.add_event(
        organization_id=org.id,
        shipment_id=shipment.id,
        event_type="pickup_confirmed",
        source="email_agent",
        raw_payload={"note": "Duplicate driver picked up freight"},
        normalized_payload={"status": "picked_up"},
        idempotency_key=idemp_key,
    )

    # Must return the existing record and not create a duplicate
    assert evt1.id == evt2.id
    events = ship_repo.get_events(org.id, shipment.id)
    assert len(events) == 1


def test_message_repository_and_idempotency(db_session):
    """Verify message creation, thread grouping, and external_message_id deduplication."""
    org_repo = OrganizationRepository(db_session)
    org = org_repo.create("Carrier Hub", "carrier-hub")
    msg_repo = MessageRepository(db_session)

    msg1 = msg_repo.create(
        organization_id=org.id,
        external_message_id="msg-gmail-12345",
        thread_id="thread-gmail-999",
        direction="inbound",
        sender="dispatcher@estes-express.com",
        recipients=["ops@carrierhub.com"],
        subject="Shipment SHP-1001 Pickup Confirmation",
        body_text="Picked up at 14:00 today.",
    )
    assert msg1.id is not None
    assert msg1.is_processed is False

    # Idempotent call with duplicate external_message_id
    msg2 = msg_repo.create(
        organization_id=org.id,
        external_message_id="msg-gmail-12345",
        thread_id="thread-gmail-999",
        direction="inbound",
        sender="dispatcher@estes-express.com",
        recipients=["ops@carrierhub.com"],
        subject="Shipment SHP-1001 Pickup Confirmation",
    )
    assert msg1.id == msg2.id

    # Thread lookup
    thread_messages = msg_repo.get_by_thread_id(org.id, "thread-gmail-999")
    assert len(thread_messages) == 1

    # Mark as processed
    marked = msg_repo.mark_as_processed(org.id, msg1.id)
    assert marked.is_processed is True


def test_document_repository_and_checksum_idempotency(db_session):
    """Verify document storage and checksum-based deduplication."""
    org_repo = OrganizationRepository(db_session)
    org = org_repo.create("Shipper Inc", "shipper-inc")
    doc_repo = DocumentRepository(db_session)

    sha = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    doc1 = doc_repo.create(
        organization_id=org.id,
        file_name="bol_123.pdf",
        storage_path="/storage/documents/bol_123.pdf",
        document_type="BOL",
        checksum_sha256=sha,
    )
    assert doc1.id is not None

    doc2 = doc_repo.create(
        organization_id=org.id,
        file_name="bol_duplicate.pdf",
        storage_path="/storage/documents/bol_duplicate.pdf",
        document_type="BOL",
        checksum_sha256=sha,
    )
    assert doc1.id == doc2.id


def test_audit_log_idempotency(db_session):
    """Verify audit log immutability and idempotency key uniqueness."""
    org_repo = OrganizationRepository(db_session)
    org = org_repo.create("Freight Audit Org", "freight-audit-org")
    audit_repo = AuditLogRepository(db_session)

    idemp_key = "audit-inbound-evt-001"
    entry1 = audit_repo.record(
        organization_id=org.id,
        event_id="evt-001",
        action="inbound_event_received",
        actor_type="system",
        actor_id="fastapi_ingestion",
        target_entity_type="event",
        target_entity_id="evt-001",
        payload_after={"status": "queued"},
        idempotency_key=idemp_key,
    )
    assert entry1.id is not None

    entry2 = audit_repo.record(
        organization_id=org.id,
        event_id="evt-001",
        action="inbound_event_received",
        actor_type="system",
        actor_id="fastapi_ingestion",
        target_entity_type="event",
        target_entity_id="evt-001",
        idempotency_key=idemp_key,
    )
    assert entry1.id == entry2.id


def test_agent_run_and_tool_calls(db_session):
    """Verify agent run lifecycle and tool call recording."""
    org_repo = OrganizationRepository(db_session)
    org = org_repo.create("Agent Org", "agent-org")
    run_repo = AgentRunRepository(db_session)

    run = run_repo.create_run(
        organization_id=org.id,
        agent_name="inbox_agent",
        trigger_event_id="evt-msg-123",
        entity_id="SHP-1001",
        model_used="gpt-4o-mini",
        tools_available=["get_shipment", "update_shipment"],
    )
    assert run.id is not None
    assert run.status == "running"

    tool_call = run_repo.record_tool_call(
        organization_id=org.id,
        agent_run_id=run.id,
        tool_name="get_shipment",
        arguments={"shipment_number": "SHP-1001"},
        result={"found": True, "status": "created"},
        status="success",
        idempotency_key="call-1",
        latency_ms=45,
    )
    assert tool_call.id is not None
    assert tool_call.status == "success"

    # Tool call idempotency within the same agent run
    dup_tool_call = run_repo.record_tool_call(
        organization_id=org.id,
        agent_run_id=run.id,
        tool_name="get_shipment",
        arguments={"shipment_number": "SHP-1001"},
        idempotency_key="call-1",
    )
    assert tool_call.id == dup_tool_call.id

    # Update run to completed
    updated_run = run_repo.update_run(
        org.id,
        run.id,
        status="completed",
        decision="shipment_updated",
        confidence=0.98,
        final_result={"success": True},
    )
    assert updated_run.status == "completed"
    assert updated_run.completed_at is not None
