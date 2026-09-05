"""Tests for Phase 1.4 Gmail/Email Connector and Agent Typed Tools."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.connectors.gmail import GmailConnector, RateLimitExceeded
from packages.storage.db import Base
from packages.storage.repositories.agent_runs import AgentRunRepository
from packages.storage.repositories.audit import AuditLogRepository
from packages.storage.repositories.documents import DocumentRepository
from packages.storage.repositories.messages import MessageRepository
from packages.storage.repositories.organizations import OrganizationRepository
from packages.tools.email_tools import (
    EmailToolSet,
    GetEmailInput,
    MarkEmailProcessedInput,
    ReplyToThreadInput,
    SearchEmailsInput,
    SendEmailInput,
    ToolPermissionDenied,
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
    return org_repo.create("Apex Freightways", "apex-freightways")


@pytest.mark.asyncio
async def test_email_normalization_and_inbound_intake(test_db, test_org):
    """Test mailbox connection, receiving new email with attachment, and persistence."""
    connector = GmailConnector(test_org.id, test_db, mock_mode=True)

    # Inbound email with messy sender format and attachment
    raw_sender = "John Doe <johndoe@freightline.com>"
    pdf_bytes = b"%PDF-1.4 sample bill of lading content"

    injected = connector.inject_mock_email(
        message_id="msg-inbox-001",
        thread_id="thread-apex-100",
        sender=raw_sender,
        recipients=["Operations <dispatch@apexfreight.com>"],
        subject="Shipment SHP-909 Pickup Confirmed",
        body_text="Picked up load SHP-909 at 08:30 AM.",
        attachments=[
            {
                "filename": "BOL_SHP_909.pdf",
                "document_type": "BOL",
                "content": pdf_bytes,
                "mime_type": "application/pdf",
            }
        ],
    )

    assert injected["id"] == "msg-inbox-001"
    assert injected["sender"] == "johndoe@freightline.com"

    # Verify message in MessageRepository
    msg_repo = MessageRepository(test_db)
    msg_record = msg_repo.get_by_external_id(test_org.id, "msg-inbox-001")
    assert msg_record is not None
    assert msg_record.sender == "johndoe@freightline.com"
    assert msg_record.recipients == ["dispatch@apexfreight.com"]
    assert msg_record.is_processed is False

    # Verify attachment in DocumentRepository
    doc_repo = DocumentRepository(test_db)
    docs = doc_repo.list_all(test_org.id)
    assert len(docs) == 1
    assert docs[0].file_name == "BOL_SHP_909.pdf"
    assert docs[0].document_type == "BOL"
    assert docs[0].checksum_sha256 is not None


@pytest.mark.asyncio
async def test_rate_limit_backoff_handling(test_db, test_org):
    """Test connector recovers from rate-limiting via backoff."""
    connector = GmailConnector(test_org.id, test_db, mock_mode=True)

    calls = 0

    async def flaky_api_call():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RateLimitExceeded("429 Too Many Requests")
        return {"status": "success", "attempts": calls}

    result = await connector._execute_with_rate_limit_retry(flaky_api_call)
    assert result["status"] == "success"
    assert calls == 3


@pytest.mark.asyncio
async def test_agent_email_toolset(test_db, test_org):
    """Verify all 6 agent-facing typed tools with auditing and idempotency."""
    connector = GmailConnector(test_org.id, test_db, mock_mode=True)
    toolset = EmailToolSet(test_org.id, test_db, connector)

    # 1. Inject test email
    connector.inject_mock_email(
        message_id="msg-agent-test-1",
        thread_id="thread-load-777",
        sender="driver@carrier.com",
        recipients=["ops@apexfreight.com"],
        subject="Delay on Load 777",
        body_text="Traffic on I-95, new ETA 18:00.",
    )

    # Create an agent run trace
    run_repo = AgentRunRepository(test_db)
    agent_run = run_repo.create_run(
        organization_id=test_org.id,
        agent_name="inbox_agent",
        trigger_event_id="evt-load-777",
    )

    # 2. Tool: search_emails
    search_res = await toolset.search_emails(
        SearchEmailsInput(query="Load 777"), agent_run_id=agent_run.id
    )
    assert search_res.count == 1
    assert search_res.messages[0]["id"] == "msg-agent-test-1"

    # 3. Tool: get_email
    get_res = await toolset.get_email(
        GetEmailInput(message_id="msg-agent-test-1"), agent_run_id=agent_run.id
    )
    assert get_res.found is True
    assert "ETA 18:00" in get_res.message["body"]

    # 4. Tool: reply_to_thread (safely replying through typed tool)
    reply_res = await toolset.reply_to_thread(
        ReplyToThreadInput(
            thread_id="thread-load-777",
            body="Thank you for the update. Customer has been notified of the 18:00 ETA.",
            idempotency_key="reply-load-777-v1",
        ),
        agent_run_id=agent_run.id,
    )
    assert reply_res.status == "sent"
    assert reply_res.thread_id == "thread-load-777"

    # Duplicate reply with same idempotency key must not send duplicate
    dup_reply = await toolset.reply_to_thread(
        ReplyToThreadInput(
            thread_id="thread-load-777",
            body="Duplicate attempt text",
            idempotency_key="reply-load-777-v1",
        ),
        agent_run_id=agent_run.id,
    )
    assert dup_reply.message_id == reply_res.message_id
    assert "idempotent" in dup_reply.status

    # 5. Tool: mark_email_processed
    mark_res = await toolset.mark_email_processed(
        MarkEmailProcessedInput(message_id="msg-agent-test-1"), agent_run_id=agent_run.id
    )
    assert mark_res.success is True

    # Check database state
    msg_repo = MessageRepository(test_db)
    db_msg = msg_repo.get_by_external_id(test_org.id, "msg-agent-test-1")
    assert db_msg.is_processed is True

    # 6. Tool: send_email
    send_res = await toolset.send_email(
        SendEmailInput(
            to_recipients=["customer@shipper.com"],
            subject="ETA Update for Load 777",
            body="Your shipment ETA is now 18:00.",
            idempotency_key="send-cust-777-v1",
        ),
        agent_run_id=agent_run.id,
    )
    assert send_res.status == "sent"

    # Verify audit log captures tool calls
    audit_repo = AuditLogRepository(test_db)
    tool_audits = audit_repo.list_all(test_org.id)
    assert len(tool_audits) >= 5


@pytest.mark.asyncio
async def test_tool_permission_enforcement(test_db, test_org):
    """Verify tool execution fails when agent lacks required permission."""
    connector = GmailConnector(test_org.id, test_db, mock_mode=True)
    # Toolset with only read permission
    read_only_toolset = EmailToolSet(
        test_org.id, test_db, connector, granted_permissions=["email:read"]
    )

    with pytest.raises(ToolPermissionDenied) as exc:
        await read_only_toolset.send_email(
            SendEmailInput(
                to_recipients=["unauthorized@external.com"],
                subject="Test",
                body="Should fail permission check",
            )
        )
    assert "Missing required permission: email:send" in str(exc.value)
