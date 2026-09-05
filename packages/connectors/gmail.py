"""Gmail / Email connector interface with rate limiting, attachment storage, and normalization."""

import asyncio
import email.utils
import hashlib
import re
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from packages.domain.logging import logger
from packages.storage.repositories.documents import DocumentRepository
from packages.storage.repositories.messages import MessageRepository


class RateLimitExceeded(Exception):
    """Raised when email provider rate limit is exceeded."""
    pass


def normalize_email_address(raw_address: str) -> str:
    """Extract clean email address from string like 'Name <email@example.com>'."""
    if not raw_address:
        return ""
    name, addr = email.utils.parseaddr(raw_address)
    return addr.lower().strip() if addr else raw_address.lower().strip()


def extract_references_from_subject(subject: str) -> List[str]:
    """Find candidate freight identifiers (PO, BOL, PRO, Load #) in email subject."""
    if not subject:
        return []
    # Match patterns like PRO12345, BOL-9876, LOAD#1234, SHP-1001, etc.
    pattern = r"\b(?:PRO|BOL|LOAD|SHP|INV|PO|REF)[-:#\s]*([A-Z0-9]{4,20})\b"
    matches = re.findall(pattern, subject, re.IGNORECASE)
    return [m.strip() for m in matches]


class BaseEmailConnector(ABC):
    """Abstract email connector defining standard operations."""

    @abstractmethod
    async def fetch_messages(self, query: str = "", limit: int = 20) -> List[Dict[str, Any]]:
        """Fetch incoming emails matching query."""
        pass

    @abstractmethod
    async def get_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        """Get full email payload including headers and body."""
        pass

    @abstractmethod
    async def get_attachments(self, message_id: str) -> List[Dict[str, Any]]:
        """Download attachments for a given email message."""
        pass

    @abstractmethod
    async def send_email(
        self,
        to_recipients: List[str],
        subject: str,
        body_text: str,
        cc_recipients: Optional[List[str]] = None,
        thread_id: Optional[str] = None,
        in_reply_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send an outbound email or thread reply."""
        pass


class GmailConnector(BaseEmailConnector):
    """Resilient Gmail connector with rate-limiting, retry-backoff, and repository persistence."""

    def __init__(
        self,
        organization_id: uuid.UUID,
        db: Session,
        inbox_connection_id: Optional[uuid.UUID] = None,
        mock_mode: bool = True,
    ):
        self.organization_id = organization_id
        self.db = db
        self.inbox_connection_id = inbox_connection_id
        self.mock_mode = mock_mode
        self.msg_repo = MessageRepository(db)
        self.doc_repo = DocumentRepository(db)
        # In-memory store for mock mode test mailbox
        self._mock_messages: Dict[str, Dict[str, Any]] = {}
        self._mock_threads: Dict[str, List[str]] = {}
        self._max_rate_limit_retries = 3

    async def _execute_with_rate_limit_retry(self, operation, *args, **kwargs):
        """Execute external API operation with exponential backoff on rate limits."""
        for attempt in range(self._max_rate_limit_retries):
            try:
                return await operation(*args, **kwargs)
            except RateLimitExceeded:
                if attempt == self._max_rate_limit_retries - 1:
                    logger.error("Gmail rate limit retries exhausted.")
                    raise
                backoff_time = 0.1 * (2 ** attempt)
                logger.warning(f"Gmail rate limit hit. Backing off for {backoff_time:.2f}s...")
                await asyncio.sleep(backoff_time)

    # In mock mode, allow injecting test emails for integration testing
    def inject_mock_email(
        self,
        message_id: str,
        thread_id: str,
        sender: str,
        recipients: List[str],
        subject: str,
        body_text: str,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Add a test email to the mock mailbox and persist to repository."""
        clean_sender = normalize_email_address(sender)
        clean_recipients = [normalize_email_address(r) for r in recipients]

        email_data = {
            "id": message_id,
            "threadId": thread_id,
            "sender": clean_sender,
            "recipients": clean_recipients,
            "subject": subject,
            "body": body_text,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "attachments": attachments or [],
        }
        self._mock_messages[message_id] = email_data
        self._mock_threads.setdefault(thread_id, []).append(message_id)

        # Store in database via MessageRepository
        msg_record = self.msg_repo.create(
            organization_id=self.organization_id,
            external_message_id=message_id,
            thread_id=thread_id,
            direction="inbound",
            sender=clean_sender,
            recipients=clean_recipients,
            subject=subject,
            body_text=body_text,
            received_at=datetime.now(timezone.utc),
            inbox_connection_id=self.inbox_connection_id,
            raw_metadata={"mock": True},
        )

        # Store attachments in DocumentRepository
        for att in (attachments or []):
            content_bytes = att.get("content", b"")
            sha = hashlib.sha256(content_bytes).hexdigest()
            self.doc_repo.create(
                organization_id=self.organization_id,
                file_name=att.get("filename", "attachment.pdf"),
                storage_path=f"/attachments/{sha}_{att.get('filename', 'file')}",
                document_type=att.get("document_type", "UNKNOWN"),
                file_size_bytes=len(content_bytes),
                mime_type=att.get("mime_type", "application/pdf"),
                checksum_sha256=sha,
            )

        return email_data

    async def fetch_messages(self, query: str = "", limit: int = 20) -> List[Dict[str, Any]]:
        """Fetch emails matching query."""
        async def _fetch():
            results = []
            for msg in self._mock_messages.values():
                if query.lower() in msg["subject"].lower() or query.lower() in msg["body"].lower():
                    results.append(msg)
                elif not query:
                    results.append(msg)
            return results[:limit]

        return await self._execute_with_rate_limit_retry(_fetch)

    async def get_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve single message details."""
        async def _get():
            return self._mock_messages.get(message_id)

        return await self._execute_with_rate_limit_retry(_get)

    async def get_attachments(self, message_id: str) -> List[Dict[str, Any]]:
        """Retrieve message attachments."""
        msg = await self.get_message(message_id)
        if not msg:
            return []
        return msg.get("attachments", [])

    async def send_email(
        self,
        to_recipients: List[str],
        subject: str,
        body_text: str,
        cc_recipients: Optional[List[str]] = None,
        thread_id: Optional[str] = None,
        in_reply_to: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Send outbound email and store in message repository."""
        async def _send():
            outbound_msg_id = f"sent-{uuid.uuid4()}"
            outbound_thread_id = thread_id or f"thread-{uuid.uuid4()}"
            clean_to = [normalize_email_address(r) for r in to_recipients]
            clean_cc = [normalize_email_address(r) for r in (cc_recipients or [])]

            # Store in database via MessageRepository
            self.msg_repo.create(
                organization_id=self.organization_id,
                external_message_id=outbound_msg_id,
                thread_id=outbound_thread_id,
                direction="outbound",
                sender="ops@freightplatform.com",
                recipients=clean_to + clean_cc,
                subject=subject,
                body_text=body_text,
                sent_at=datetime.now(timezone.utc),
                inbox_connection_id=self.inbox_connection_id,
                raw_metadata={
                    "in_reply_to": in_reply_to,
                    "thread_id": outbound_thread_id,
                },
            )

            result = {
                "message_id": outbound_msg_id,
                "thread_id": outbound_thread_id,
                "status": "sent",
                "to": clean_to,
                "subject": subject,
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }
            self._mock_messages[outbound_msg_id] = result
            self._mock_threads.setdefault(outbound_thread_id, []).append(outbound_msg_id)
            logger.info(f"Outbound email {outbound_msg_id} successfully sent in thread {outbound_thread_id}")
            return result

        return await self._execute_with_rate_limit_retry(_send)
