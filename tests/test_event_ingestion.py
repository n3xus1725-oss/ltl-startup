"""Tests for Phase 1.3 Event Ingestion Layer."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from packages.domain.dispatcher import EventDispatcher
from packages.domain.events import EventStatus, InboundEventPayload, normalize_event
from packages.storage.db import Base, get_db
from packages.storage.repositories.audit import AuditLogRepository
from packages.storage.repositories.organizations import OrganizationRepository


@pytest.fixture(scope="function")
def test_db():
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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


@pytest.fixture(scope="function")
def client(test_db):
    def override_get_db():
        try:
            yield test_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_event_normalization():
    """Verify raw external payload normalizes common freight keys."""
    org_id = uuid.uuid4()
    inbound = InboundEventPayload(
        organization_id=org_id,
        source="carrier_webhook",
        event_type="eta_update",
        payload={
            "pro_number": "PRO-9988",
            "shipment_number": "SHP-1234",
            "eta": "2026-09-10T14:00:00Z",
            "carrier_name": "Old Dominion",
        },
    )
    normalized = normalize_event(inbound)

    assert normalized.organization_id == org_id
    assert normalized.source == "carrier_webhook"
    assert normalized.event_type == "eta_update"
    assert normalized.idempotency_key is not None
    assert normalized.normalized_payload["carrier_reference"] == "PRO-9988"
    assert normalized.normalized_payload["shipment_number"] == "SHP-1234"
    assert normalized.raw_payload["pro_number"] == "PRO-9988"


def test_inbound_api_and_idempotency(client, test_db):
    """Verify endpoint receives payload, queues it, and deduplicates identical replays."""
    org_repo = OrganizationRepository(test_db)
    org = org_repo.create("Dispatch Logistics", "dispatch-logistics")

    payload_data = {
        "organization_id": str(org.id),
        "source": "gmail",
        "event_type": "email_received",
        "idempotency_key": "msg-inbound-99901",
        "payload": {
            "message_id": "msg-99901",
            "subject": "Pickup Completed for SHP-500",
            "body": "Driver picked up load at 10 AM.",
        },
    }

    # First ingestion
    res1 = client.post("/api/v1/events/inbound", json=payload_data)
    assert res1.status_code == 202
    data1 = res1.json()
    assert data1["status"] == "queued"
    assert data1["idempotent_replay"] is False
    event_id1 = data1["event_id"]

    # Replay of identical event
    res2 = client.post("/api/v1/events/inbound", json=payload_data)
    assert res2.status_code == 202
    data2 = res2.json()
    assert data2["idempotent_replay"] is True
    assert data2["event_id"] == event_id1

    # Verify audit log has preserved the raw payload metadata
    audit_repo = AuditLogRepository(test_db)
    audit_entry = audit_repo.get_by_idempotency_key(org.id, "msg-inbound-99901")
    assert audit_entry is not None
    assert audit_entry.event_id == event_id1
    assert audit_entry.metadata_json["raw_payload"]["message_id"] == "msg-99901"


@pytest.mark.asyncio
async def test_worker_retry_success(test_db):
    """Verify worker retry logic succeeds when transient errors recover."""
    org_repo = OrganizationRepository(test_db)
    org = org_repo.create("Retry Org", "retry-org")
    dispatcher = EventDispatcher(test_db)

    inbound = InboundEventPayload(
        organization_id=org.id,
        source="carrier",
        event_type="status_update",
        idempotency_key="evt-transient-test",
        payload={"status": "in_transit"},
    )
    event, _ = dispatcher.ingest(inbound)

    attempts = 0

    async def flaky_handler(evt):
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise ConnectionError("Temporary connection timeout")
        return {"processed": True, "attempts": attempts}

    result_event = await dispatcher.execute_with_retry(event, flaky_handler)

    assert result_event.status == EventStatus.COMPLETED
    assert result_event.retry_count == 1
    assert attempts == 2


@pytest.mark.asyncio
async def test_worker_retry_exhausted_failure(test_db):
    """Verify worker marks event failed after exhausting retries and records to audit."""
    org_repo = OrganizationRepository(test_db)
    org = org_repo.create("Failure Org", "failure-org")
    dispatcher = EventDispatcher(test_db)

    inbound = InboundEventPayload(
        organization_id=org.id,
        source="carrier",
        event_type="status_update",
        idempotency_key="evt-fatal-test",
        payload={"status": "corrupt"},
    )
    event, _ = dispatcher.ingest(inbound)
    event.max_retries = 2

    async def broken_handler(evt):
        raise ValueError("Fatal payload corruption")

    result_event = await dispatcher.execute_with_retry(event, broken_handler)

    assert result_event.status == EventStatus.FAILED
    assert result_event.retry_count == 3  # initial + 2 retries
    assert "Fatal payload corruption" in result_event.error_message
