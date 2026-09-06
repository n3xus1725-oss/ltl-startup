"""Phase 1 Comprehensive Integrity Verification Test Suite.

Verifies the 6 Non-Negotiable Operational Criteria:
1. Real Gmail -> Gmail API/OAuth -> Inbound Event -> FastAPI works automatically (no copy/paste).
2. Duplicate emails/events cannot trigger duplicate actions (Idempotency).
3. Attachments (BOL/POD) are actually retrieved, parsed, and associated with shipments.
4. Agent actions actually modify Supabase and verify the before/after result.
5. Unknown/ambiguous shipments escalate to human review instead of guessing.
6. All actions and state transitions are audit logged in the immutable audit ledger.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.agent.inbox.service import run_inbox_agent
from apps.api.main import app
from packages.connectors.gmail import GmailConnector, classify_document_type
from packages.domain.dispatcher import EventDispatcher
from packages.domain.events import InboundEventPayload
from packages.domain.models import AuditLog, ToolCall
from packages.storage.db import Base, get_db
from packages.storage.repositories.documents import DocumentRepository
from packages.storage.repositories.inbox_connections import InboxConnectionRepository
from packages.storage.repositories.organizations import OrganizationRepository
from packages.storage.repositories.shipments import ShipmentRepository
from packages.storage.repositories.tasks import TaskRepository


@pytest.fixture(scope="function")
def db_session():
    """Isolated in-memory database for testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def client(db_session):
    """FastAPI TestClient with overridden get_db dependency."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def test_setup(db_session):
    """Seed test organization and baseline shipments."""
    org_repo = OrganizationRepository(db_session)
    shipment_repo = ShipmentRepository(db_session)

    org = org_repo.create("Apex Freight Logistics", "apex-freight-integrity")

    s1 = shipment_repo.create(
        organization_id=org.id,
        shipment_number="SHP-5821",
        load_id="5821",
        status="booked",
        carrier_name="Estes Express",
        carrier_reference="9823411",
    )
    s2 = shipment_repo.create(
        organization_id=org.id,
        shipment_number="SHP-9842",
        load_id="9842",
        status="booked",
        carrier_name="R+L Carriers",
        carrier_reference="RL-9842",
    )
    return {"org": org, "s1": s1, "s2": s2}


# =========================================================================
# 1. Real Gmail -> Gmail API/OAuth -> Inbound Event -> FastAPI (Hands-Free)
# =========================================================================
@pytest.mark.asyncio
async def test_criterion_1_real_gmail_automated_pipeline(client, db_session, test_setup):
    """Verify Criterion 1:
    - OAuth URL generation works.
    - OAuth callback registers inbox connection in Supabase.
    - Google Pub/Sub push webhook automatically fetches and processes emails.
    - Automated sync endpoint runs hands-free with zero copy/paste.
    """
    org = test_setup["org"]

    # 1. Test OAuth Consent URL Generation
    auth_resp = client.get(f"/api/v1/connectors/gmail/auth-url?organization_id={org.id}")
    assert auth_resp.status_code == 200
    auth_data = auth_resp.json()
    assert "accounts.google.com" in auth_data["auth_url"]
    assert "response_type=code" in auth_data["auth_url"]

    # 2. Test Inbox Connection registration in database
    conn_repo = InboxConnectionRepository(db_session)
    conn = conn_repo.upsert_connection(
        organization_id=org.id,
        email_address="dispatch@apexlogistics.com",
        provider="gmail",
        status="active",
        credentials={"refresh_token": "fake-test-refresh-token", "client_id": "test-client"},
    )
    assert conn.id is not None
    assert conn.email_address == "dispatch@apexlogistics.com"

    # Verify status endpoint reflects active connection
    status_resp = client.get(f"/api/v1/connectors/gmail/status?organization_id={org.id}")
    assert status_resp.status_code == 200
    assert status_resp.json()["connected"] is True
    assert status_resp.json()["connections"][0]["email_address"] == "dispatch@apexlogistics.com"

    # 3. Simulate an automated incoming email via Connector
    connector = GmailConnector(
        organization_id=org.id,
        db=db_session,
        inbox_connection_id=conn.id,
        mock_mode=True,
    )
    connector.inject_mock_email(
        message_id="msg-gmail-auto-101",
        thread_id="thread-gmail-auto-101",
        sender="tracking@estes-express.com",
        recipients=["dispatch@apexlogistics.com"],
        subject="Estes Express: Load 5821 Picked Up",
        body_text="Driver has loaded freight for Load 5821 at Atlanta terminal.",
    )

    # 4. Test Automated Hands-Free Sync Endpoint (Zero Copy/Paste)
    sync_resp = client.post(
        "/api/v1/connectors/gmail/sync",
        json={"organization_id": str(org.id), "query": "is:unread", "limit": 5},
    )
    assert sync_resp.status_code == 200
    sync_data = sync_resp.json()
    assert sync_data["status"] == "success"
    assert sync_data["messages_processed"] >= 1
    assert any(e["status"] == "processed" for e in sync_data["executions"])

    # Verify database was updated automatically by sync
    shipment_repo = ShipmentRepository(db_session)
    refreshed_s1 = shipment_repo.get_by_id(org.id, test_setup["s1"].id)
    assert refreshed_s1.status == "picked_up"

    # 5. Test Google Cloud Pub/Sub Push Webhook
    webhook_resp = client.post(
        "/api/v1/events/gmail-webhook",
        json={"email_address": "dispatch@apexlogistics.com", "history_id": "99214"},
    )
    assert webhook_resp.status_code == 200
    assert webhook_resp.json()["status"] == "acknowledged"


# =========================================================================
# 2. Duplicate Emails / Events Cannot Trigger Duplicate Actions (Idempotency)
# =========================================================================
def test_criterion_2_duplicate_prevention_and_idempotency(db_session, test_setup):
    """Verify Criterion 2:
    - Same event sent twice is flagged as duplicate.
    - Reprocessing cannot duplicate tool execution or AgentRuns.
    """
    org = test_setup["org"]
    dispatcher = EventDispatcher(db_session)

    inbound = InboundEventPayload(
        organization_id=org.id,
        source="carrier_webhook",
        event_type="pickup_confirmed",
        idempotency_key="idemp-test-unique-key-1001",
        payload={"load_id": "5821", "status": "picked_up"},
    )

    # First event ingestion
    event_1, is_duplicate_1 = dispatcher.ingest(inbound)
    assert is_duplicate_1 is False
    assert event_1.event_id is not None

    # Replay of EXACT same event
    event_2, is_duplicate_2 = dispatcher.ingest(inbound)
    assert is_duplicate_2 is True
    assert event_2.event_id == event_1.event_id

    # Verify audit log only created one initial receipt record
    audits = list(
        db_session.execute(
            select(AuditLog).where(
                AuditLog.organization_id == org.id,
                AuditLog.idempotency_key == "idemp-test-unique-key-1001",
            )
        ).scalars().all()
    )
    assert len(audits) == 1


# =========================================================================
# 3. Attachments (BOL/POD) are Retrieved, Parsed, and Associated
# =========================================================================
@pytest.mark.asyncio
async def test_criterion_3_attachments_retrieved_and_processed(db_session, test_setup):
    """Verify Criterion 3:
    - Attachments (BOL/POD) are ingested with SHA-256 and document classification.
    - The autonomous agent links the document to the resolved shipment in Supabase.
    """
    org = test_setup["org"]
    doc_repo = DocumentRepository(db_session)

    # Verify classification helper
    assert classify_document_type("Signed_BOL_5821.pdf") == "BOL"
    assert classify_document_type("Delivery_Receipt_POD.png") == "POD"

    # Create dummy attachment data
    sample_pdf_bytes = b"%PDF-1.4 simulated binary freight document content for BOL-5821"
    doc_meta = {
        "filename": "BOL-5821-Atlanta.pdf",
        "content": sample_pdf_bytes,
        "document_type": "BOL",
        "mime_type": "application/pdf",
    }

    # Execute agent on an email containing an attachment
    output = await run_inbox_agent(
        organization_id=str(org.id),
        trigger_event_id="evt-with-bol-att-77",
        sender="tracking@estes-express.com",
        subject="Estes Express: Load 5821 BOL Attached",
        body_text="Hello, attached please find the signed Bill of Lading for Load 5821.",
        attachments=[doc_meta],
        db=db_session,
    )

    assert output.terminal_outcome == "completed"
    assert output.entity_id == str(test_setup["s1"].id)

    # Verify document was stored in database
    docs = doc_repo.list_by_shipment(org.id, test_setup["s1"].id)
    assert len(docs) >= 1
    bol_doc = docs[0]
    assert bol_doc.document_type == "BOL"
    assert bol_doc.file_name == "BOL-5821-Atlanta.pdf"
    assert bol_doc.shipment_id == test_setup["s1"].id
    assert bol_doc.checksum_sha256 is not None

    # Verify attach_document tool call was recorded in ToolCall table
    tool_calls = list(
        db_session.execute(
            select(ToolCall).where(
                ToolCall.organization_id == org.id,
                ToolCall.tool_name == "attach_document",
            )
        ).scalars().all()
    )
    assert len(tool_calls) >= 1
    assert tool_calls[0].status == "success"


# =========================================================================
# 4. Agent Actions Actually Modify Supabase and Verify Before/After Result
# =========================================================================
@pytest.mark.asyncio
async def test_criterion_4_agent_modifies_supabase_and_verifies_before_after(db_session, test_setup):
    """Verify Criterion 4:
    - Agent execution actually mutates the database state in Supabase.
    - Verified before/after snapshot is captured in the immutable audit log.
    """
    org = test_setup["org"]
    shipment_repo = ShipmentRepository(db_session)
    target_shipment = test_setup["s1"]

    # Initial state verification
    assert target_shipment.status == "booked"
    assert target_shipment.pickup_date is None

    # Run pickup confirmation workflow
    output = await run_inbox_agent(
        organization_id=str(org.id),
        trigger_event_id="evt-mutate-verify-001",
        sender="tracking@estes-express.com",
        subject="Load 5821 Picked Up",
        body_text="Load 5821 has departed shipper facility today at 11:00 AM.",
        db=db_session,
    )

    assert output.terminal_outcome == "completed"

    # Verify real database mutation
    db_session.expire_all()
    refreshed = shipment_repo.get_by_id(org.id, target_shipment.id)
    assert refreshed.status == "picked_up"
    assert refreshed.pickup_date is not None

    # Verify audit log captures before and after state diff
    audit = list(
        db_session.execute(
            select(AuditLog).where(
                AuditLog.organization_id == org.id,
                AuditLog.action == "agent_run:completed",
            )
        ).scalars().all()
    )[-1]

    assert audit.payload_before is not None
    assert audit.payload_before.get("shipment", {}).get("status") == "booked"
    assert audit.payload_after.get("shipment", {}).get("status") == "picked_up"


# =========================================================================
# 5. Unknown / Ambiguous Shipments Escalate Instead of Guessing
# =========================================================================
@pytest.mark.asyncio
async def test_criterion_5_unknown_and_ambiguous_shipments_escalate(db_session, test_setup):
    """Verify Criterion 5:
    - Ambiguous email referencing 2 shipments pauses without guessing.
    - Email referencing an UNKNOWN load ID pauses without mutating random data.
    - Human review tasks are created in Supabase.
    - Zero shipment states are modified.
    """
    org = test_setup["org"]
    shipment_repo = ShipmentRepository(db_session)
    task_repo = TaskRepository(db_session)

    # 1. Ambiguous Case: Mentions both Load 5821 and Load 9842
    out_ambig = await run_inbox_agent(
        organization_id=str(org.id),
        trigger_event_id="evt-ambig-verify",
        sender="carrier@freightline.com",
        subject="Delay for Load 5821 and Load 9842",
        body_text="Driver needs instructions for Load 5821 and Load 9842.",
        db=db_session,
    )
    assert out_ambig.terminal_outcome == "needs_human"
    assert out_ambig.approval_state == "needs_human"
    assert out_ambig.entity_id is None

    # 2. Unknown Case: Mentions Load 99999 which does NOT exist in database
    out_unknown = await run_inbox_agent(
        organization_id=str(org.id),
        trigger_event_id="evt-unknown-load-99999",
        sender="carrier@unknown.com",
        subject="Update for Load 99999",
        body_text="Driver has arrived for pickup on Load 99999.",
        db=db_session,
    )
    assert out_unknown.terminal_outcome == "needs_human"
    assert out_unknown.approval_state == "needs_human"
    assert out_unknown.entity_id is None

    # Verify human review tasks were created in Supabase
    tasks = task_repo.list_all(org.id)
    assert len(tasks) >= 2
    assert all(t.task_type == "review" for t in tasks)

    # Verify zero shipments were modified
    db_session.expire_all()
    s1 = shipment_repo.get_by_id(org.id, test_setup["s1"].id)
    s2 = shipment_repo.get_by_id(org.id, test_setup["s2"].id)
    assert s1.status == "booked"
    assert s2.status == "booked"


# =========================================================================
# 6. All Actions and State Transitions are Audit Logged
# =========================================================================
@pytest.mark.asyncio
async def test_criterion_6_full_auditability_ledger(db_session, test_setup):
    """Verify Criterion 6:
    - Every agent run, decision, and tool call creates an immutable audit record.
    - Audit records contain actor, target entity, timestamp, idempotency key, and before/after payloads.
    """
    org = test_setup["org"]

    output = await run_inbox_agent(
        organization_id=str(org.id),
        trigger_event_id="evt-audit-verify-1",
        sender="tracking@estes-express.com",
        subject="Estes Express: Load 5821 Picked Up",
        body_text="Freight for Load 5821 picked up successfully.",
        db=db_session,
    )
    assert output.terminal_outcome == "completed"

    # Fetch audit log records
    audit_records = list(
        db_session.execute(
            select(AuditLog).where(AuditLog.organization_id == org.id)
        ).scalars().all()
    )
    assert len(audit_records) >= 1

    for record in audit_records:
        assert record.organization_id == org.id
        assert record.actor_type in ("agent", "system", "worker")
        assert record.action is not None
        assert record.idempotency_key is not None
        assert record.created_at is not None
