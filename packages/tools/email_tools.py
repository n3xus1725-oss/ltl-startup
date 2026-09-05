"""Agent-facing typed email tools with schemas, permissions, idempotency keys, and audit logs."""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from packages.connectors.gmail import GmailConnector
from packages.domain.logging import logger
from packages.storage.repositories.agent_runs import AgentRunRepository
from packages.storage.repositories.audit import AuditLogRepository
from packages.storage.repositories.documents import DocumentRepository
from packages.storage.repositories.messages import MessageRepository


class ToolPermissionDenied(Exception):
    """Raised when an agent attempts to call a tool without sufficient permissions."""
    pass


# ---------------- Input / Output Pydantic Schemas ---------------- #

class SearchEmailsInput(BaseModel):
    query: str = Field(..., description="Search keyword or identifier")
    limit: int = Field(default=10, ge=1, le=50)


class SearchEmailsOutput(BaseModel):
    messages: List[Dict[str, Any]]
    count: int


class GetEmailInput(BaseModel):
    message_id: str = Field(..., description="External email message ID")


class GetEmailOutput(BaseModel):
    found: bool
    message: Optional[Dict[str, Any]] = None


class GetAttachmentInput(BaseModel):
    document_id: str = Field(..., description="Document UUID")


class GetAttachmentOutput(BaseModel):
    found: bool
    document: Optional[Dict[str, Any]] = None


class SendEmailInput(BaseModel):
    to_recipients: List[str] = Field(..., min_length=1, description="List of recipient email addresses")
    subject: str = Field(..., min_length=1, description="Email subject line")
    body: str = Field(..., min_length=1, description="Email body content")
    cc_recipients: Optional[List[str]] = Field(default=None)
    idempotency_key: Optional[str] = Field(default=None, description="Unique key to prevent duplicate sends")


class SendEmailOutput(BaseModel):
    status: str
    message_id: str
    thread_id: str


class ReplyToThreadInput(BaseModel):
    thread_id: str = Field(..., description="Target thread identifier")
    body: str = Field(..., min_length=1, description="Reply message body")
    to_recipients: Optional[List[str]] = Field(default=None, description="Optional override recipients")
    idempotency_key: Optional[str] = Field(default=None, description="Unique key to prevent duplicate sends")


class ReplyToThreadOutput(BaseModel):
    status: str
    message_id: str
    thread_id: str


class MarkEmailProcessedInput(BaseModel):
    message_id: str = Field(..., description="External message ID")


class MarkEmailProcessedOutput(BaseModel):
    success: bool
    message_id: str


# ---------------- Tool Definitions & Execution ---------------- #

@dataclass
class ToolMetadata:
    name: str
    purpose: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    required_permission: str
    external_side_effects: bool
    audit_metadata: Dict[str, Any] = field(default_factory=dict)


class EmailToolSet:
    """Provides the 6 typed tools to agents with strict permission checks and audit persistence."""

    def __init__(
        self,
        organization_id: uuid.UUID,
        db: Session,
        connector: GmailConnector,
        granted_permissions: Optional[List[str]] = None,
    ):
        self.organization_id = organization_id
        self.db = db
        self.connector = connector
        # Default granted permissions: read, send, write
        self.granted_permissions = set(
            granted_permissions or ["email:read", "email:send", "email:write"]
        )
        self.msg_repo = MessageRepository(db)
        self.doc_repo = DocumentRepository(db)
        self.audit_repo = AuditLogRepository(db)
        self.agent_repo = AgentRunRepository(db)

    def _check_permission(self, permission: str):
        if permission not in self.granted_permissions:
            raise ToolPermissionDenied(f"Missing required permission: {permission}")

    def _record_tool_audit(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        result: Dict[str, Any],
        status: str,
        agent_run_id: Optional[uuid.UUID],
        idempotency_key: Optional[str],
        latency_ms: int,
        error_message: Optional[str] = None,
    ):
        """Persist tool call trace and audit log entry."""
        if agent_run_id:
            self.agent_repo.record_tool_call(
                organization_id=self.organization_id,
                agent_run_id=agent_run_id,
                tool_name=tool_name,
                arguments=arguments,
                result=result,
                status=status,
                idempotency_key=idempotency_key,
                latency_ms=latency_ms,
                error_message=error_message,
            )

        self.audit_repo.record(
            organization_id=self.organization_id,
            event_id=str(uuid.uuid4()),
            action=f"tool_call:{tool_name}",
            actor_type="agent" if agent_run_id else "system",
            actor_id=str(agent_run_id) if agent_run_id else "direct_api",
            target_entity_type="email_tool",
            target_entity_id=tool_name,
            payload_before=arguments,
            payload_after=result,
            metadata_json={"latency_ms": latency_ms, "status": status},
            idempotency_key=idempotency_key,
        )

    # 1. search_emails
    async def search_emails(
        self,
        input_data: SearchEmailsInput,
        agent_run_id: Optional[uuid.UUID] = None,
    ) -> SearchEmailsOutput:
        self._check_permission("email:read")
        start = time.time()
        messages = await self.connector.fetch_messages(input_data.query, input_data.limit)
        output = SearchEmailsOutput(messages=messages, count=len(messages))
        latency = int((time.time() - start) * 1000)

        self._record_tool_audit(
            tool_name="search_emails",
            arguments=input_data.model_dump(),
            result=output.model_dump(),
            status="success",
            agent_run_id=agent_run_id,
            idempotency_key=None,
            latency_ms=latency,
        )
        return output

    # 2. get_email
    async def get_email(
        self,
        input_data: GetEmailInput,
        agent_run_id: Optional[uuid.UUID] = None,
    ) -> GetEmailOutput:
        self._check_permission("email:read")
        start = time.time()
        msg = await self.connector.get_message(input_data.message_id)
        output = GetEmailOutput(found=msg is not None, message=msg)
        latency = int((time.time() - start) * 1000)

        self._record_tool_audit(
            tool_name="get_email",
            arguments=input_data.model_dump(),
            result=output.model_dump(),
            status="success",
            agent_run_id=agent_run_id,
            idempotency_key=None,
            latency_ms=latency,
        )
        return output

    # 3. get_attachment
    async def get_attachment(
        self,
        input_data: GetAttachmentInput,
        agent_run_id: Optional[uuid.UUID] = None,
    ) -> GetAttachmentOutput:
        self._check_permission("email:read")
        start = time.time()
        doc_uuid = uuid.UUID(input_data.document_id)
        doc = self.doc_repo.get_by_id(self.organization_id, doc_uuid)
        doc_dict = (
            {
                "id": str(doc.id),
                "file_name": doc.file_name,
                "document_type": doc.document_type,
                "file_size_bytes": doc.file_size_bytes,
                "mime_type": doc.mime_type,
                "checksum_sha256": doc.checksum_sha256,
                "storage_path": doc.storage_path,
            }
            if doc
            else None
        )
        output = GetAttachmentOutput(found=doc is not None, document=doc_dict)
        latency = int((time.time() - start) * 1000)

        self._record_tool_audit(
            tool_name="get_attachment",
            arguments=input_data.model_dump(),
            result=output.model_dump(),
            status="success",
            agent_run_id=agent_run_id,
            idempotency_key=None,
            latency_ms=latency,
        )
        return output

    # 4. send_email
    async def send_email(
        self,
        input_data: SendEmailInput,
        agent_run_id: Optional[uuid.UUID] = None,
    ) -> SendEmailOutput:
        self._check_permission("email:send")
        start = time.time()

        # Enforce idempotency: check if already sent under this idempotency_key
        if input_data.idempotency_key:
            existing_audit = self.audit_repo.get_by_idempotency_key(
                self.organization_id, input_data.idempotency_key
            )
            if existing_audit and existing_audit.payload_after:
                res = existing_audit.payload_after
                return SendEmailOutput(
                    status="sent (idempotent_cached)",
                    message_id=res["message_id"],
                    thread_id=res["thread_id"],
                )

        sent_res = await self.connector.send_email(
            to_recipients=input_data.to_recipients,
            subject=input_data.subject,
            body_text=input_data.body,
            cc_recipients=input_data.cc_recipients,
        )
        output = SendEmailOutput(
            status="sent",
            message_id=sent_res["message_id"],
            thread_id=sent_res["thread_id"],
        )
        latency = int((time.time() - start) * 1000)

        self._record_tool_audit(
            tool_name="send_email",
            arguments=input_data.model_dump(),
            result=output.model_dump(),
            status="success",
            agent_run_id=agent_run_id,
            idempotency_key=input_data.idempotency_key,
            latency_ms=latency,
        )
        return output

    # 5. reply_to_thread
    async def reply_to_thread(
        self,
        input_data: ReplyToThreadInput,
        agent_run_id: Optional[uuid.UUID] = None,
    ) -> ReplyToThreadOutput:
        self._check_permission("email:send")
        start = time.time()

        if input_data.idempotency_key:
            existing_audit = self.audit_repo.get_by_idempotency_key(
                self.organization_id, input_data.idempotency_key
            )
            if existing_audit and existing_audit.payload_after:
                res = existing_audit.payload_after
                return ReplyToThreadOutput(
                    status="sent (idempotent_cached)",
                    message_id=res["message_id"],
                    thread_id=res["thread_id"],
                )

        # Lookup thread to discover recipient and subject
        thread_messages = self.msg_repo.get_by_thread_id(self.organization_id, input_data.thread_id)
        if thread_messages:
            last_msg = thread_messages[-1]
            recipients = input_data.to_recipients or [last_msg.sender]
            subj = last_msg.subject
            if not subj.lower().startswith("re:"):
                subj = f"Re: {subj}"
            in_reply_to = last_msg.external_message_id
        else:
            recipients = input_data.to_recipients or ["operations@carrier.com"]
            subj = f"Re: Thread {input_data.thread_id}"
            in_reply_to = None

        sent_res = await self.connector.send_email(
            to_recipients=recipients,
            subject=subj,
            body_text=input_data.body,
            thread_id=input_data.thread_id,
            in_reply_to=in_reply_to,
        )

        output = ReplyToThreadOutput(
            status="sent",
            message_id=sent_res["message_id"],
            thread_id=sent_res["thread_id"],
        )
        latency = int((time.time() - start) * 1000)

        self._record_tool_audit(
            tool_name="reply_to_thread",
            arguments=input_data.model_dump(),
            result=output.model_dump(),
            status="success",
            agent_run_id=agent_run_id,
            idempotency_key=input_data.idempotency_key,
            latency_ms=latency,
        )
        return output

    # 6. mark_email_processed
    async def mark_email_processed(
        self,
        input_data: MarkEmailProcessedInput,
        agent_run_id: Optional[uuid.UUID] = None,
    ) -> MarkEmailProcessedOutput:
        self._check_permission("email:write")
        start = time.time()

        msg = self.msg_repo.get_by_external_id(self.organization_id, input_data.message_id)
        success = False
        if msg:
            self.msg_repo.mark_as_processed(self.organization_id, msg.id)
            success = True

        output = MarkEmailProcessedOutput(success=success, message_id=input_data.message_id)
        latency = int((time.time() - start) * 1000)

        self._record_tool_audit(
            tool_name="mark_email_processed",
            arguments=input_data.model_dump(),
            result=output.model_dump(),
            status="success" if success else "not_found",
            agent_run_id=agent_run_id,
            idempotency_key=None,
            latency_ms=latency,
        )
        return output
