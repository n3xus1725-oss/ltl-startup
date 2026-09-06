"""Gmail / Email connector interface with OAuth, rate limiting, attachment storage, and normalization."""

import asyncio
import base64
import email.message
import email.utils
import hashlib
import re
import urllib.parse
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy.orm import Session

from packages.domain.config import get_settings
from packages.domain.logging import logger
from packages.domain.models import InboxConnection
from packages.storage.repositories.documents import DocumentRepository
from packages.storage.repositories.messages import MessageRepository

GMAIL_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]


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
    pattern = r"\b(?:PRO|BOL|LOAD|SHP|INV|PO|REF)[-:#\s]*([A-Z0-9]{4,20})\b"
    matches = re.findall(pattern, subject, re.IGNORECASE)
    return [m.strip() for m in matches]


def classify_document_type(filename: str) -> str:
    """Classify freight document type from filename."""
    lower = filename.lower()
    if "bol" in lower or "bill_of_lading" in lower or "lading" in lower:
        return "BOL"
    if "pod" in lower or "proof_of_delivery" in lower or "delivery_receipt" in lower:
        return "POD"
    if "rate" in lower or "confirmation" in lower or "ratecon" in lower:
        return "RATE_CONFIRMATION"
    if "invoice" in lower:
        return "INVOICE"
    return "UNKNOWN"


class BaseEmailConnector(ABC):
    """Abstract email connector defining standard operations."""

    @abstractmethod
    async def fetch_messages(self, query: str = "", limit: int = 20) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_attachments(self, message_id: str) -> List[Dict[str, Any]]:
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
        pass


_SHARED_MOCK_MESSAGES: Dict[str, Dict[str, Any]] = {}
_SHARED_MOCK_THREADS: Dict[str, List[str]] = {}


class GmailConnector(BaseEmailConnector):
    """Resilient Gmail connector with real Google API integration, OAuth 2.0 token management,
    attachment extraction to DocumentRepository, and local test mock mode.
    """

    def __init__(
        self,
        organization_id: uuid.UUID,
        db: Session,
        inbox_connection_id: Optional[uuid.UUID] = None,
        mock_mode: Optional[bool] = None,
        credentials: Optional[Dict[str, Any]] = None,
    ):
        self.organization_id = organization_id
        self.db = db
        self.inbox_connection_id = inbox_connection_id
        self.msg_repo = MessageRepository(db)
        self.doc_repo = DocumentRepository(db)
        self._max_rate_limit_retries = 3

        # In-memory store for mock mode (shared across instances for tests/local sync)
        self._mock_messages: Dict[str, Dict[str, Any]] = _SHARED_MOCK_MESSAGES
        self._mock_threads: Dict[str, List[str]] = _SHARED_MOCK_THREADS

        # Resolve credentials and operational mode
        settings = get_settings()
        self.credentials = credentials or self._resolve_credentials(settings)

        if mock_mode is not None:
            self.mock_mode = mock_mode
        elif settings.MOCK_EMAIL_CONNECTOR:
            self.mock_mode = True
        elif self.credentials and (
            self.credentials.get("access_token")
            or (
                self.credentials.get("refresh_token")
                and self.credentials.get("client_id")
                and self.credentials.get("client_secret")
            )
        ):
            self.mock_mode = False
        else:
            self.mock_mode = True

    def _resolve_credentials(self, settings) -> Dict[str, Any]:
        """Fetch credentials from database inbox connection or environment settings."""
        if self.inbox_connection_id:
            conn = self.db.query(InboxConnection).filter(
                InboxConnection.id == self.inbox_connection_id,
                InboxConnection.organization_id == self.organization_id,
            ).first()
            if conn and conn.credentials_encrypted:
                return dict(conn.credentials_encrypted)

        creds = {}
        if settings.GMAIL_CLIENT_ID:
            creds["client_id"] = settings.GMAIL_CLIENT_ID
        if settings.GMAIL_CLIENT_SECRET:
            creds["client_secret"] = settings.GMAIL_CLIENT_SECRET
        if settings.GMAIL_REFRESH_TOKEN:
            creds["refresh_token"] = settings.GMAIL_REFRESH_TOKEN
        return creds

    @staticmethod
    def create_oauth_url(
        client_id: str,
        redirect_uri: str,
        state: str,
        scopes: Optional[List[str]] = None,
    ) -> str:
        """Generate Google OAuth 2.0 authorization URL."""
        scope_str = " ".join(scopes or DEFAULT_SCOPES)
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scope_str,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"

    @staticmethod
    async def exchange_code_for_tokens(
        code: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
    ) -> Dict[str, Any]:
        """Exchange authorization code for Google access and refresh tokens."""
        data = {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(GOOGLE_TOKEN_URL, data=data)
            if resp.status_code != 200:
                raise ValueError(f"Failed to exchange Google OAuth code: {resp.text}")
            token_data = resp.json()

            # Retrieve user email profile
            access_token = token_data.get("access_token")
            profile_resp = await client.get(
                f"{GMAIL_API_BASE_URL}/profile",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            email_address = "unknown@gmail.com"
            if profile_resp.status_code == 200:
                email_address = profile_resp.json().get("emailAddress", email_address)

            token_data["email_address"] = email_address
            return token_data

    async def _get_access_token(self) -> str:
        """Retrieve valid access token, automatically refreshing via refresh_token if needed."""
        token = self.credentials.get("access_token")
        if token:
            return token

        refresh_token = self.credentials.get("refresh_token")
        client_id = self.credentials.get("client_id")
        client_secret = self.credentials.get("client_secret")

        if not refresh_token or not client_id or not client_secret:
            raise ValueError("Gmail API requires client_id, client_secret, and refresh_token")

        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(GOOGLE_TOKEN_URL, data=data)
            if resp.status_code != 200:
                raise ValueError(f"Failed to refresh Google OAuth token: {resp.text}")
            new_tokens = resp.json()
            new_access_token = new_tokens.get("access_token")
            self.credentials["access_token"] = new_access_token

            # Update DB connection if available
            if self.inbox_connection_id:
                conn = self.db.query(InboxConnection).filter(
                    InboxConnection.id == self.inbox_connection_id
                ).first()
                if conn:
                    creds = dict(conn.credentials_encrypted or {})
                    creds["access_token"] = new_access_token
                    conn.credentials_encrypted = creds
                    self.db.commit()

            return new_access_token

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

    # ---------------- Mock Mode Support ---------------- #
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

        stored_attachments = []
        for att in (attachments or []):
            content_bytes = att.get("content", b"")
            if isinstance(content_bytes, str):
                content_bytes = content_bytes.encode("utf-8")
            sha = hashlib.sha256(content_bytes).hexdigest()
            filename = att.get("filename", "attachment.pdf")
            doc_type = att.get("document_type") or classify_document_type(filename)

            doc = self.doc_repo.create(
                organization_id=self.organization_id,
                file_name=filename,
                storage_path=f"/attachments/{sha}_{filename}",
                document_type=doc_type,
                file_size_bytes=len(content_bytes),
                mime_type=att.get("mime_type", "application/pdf"),
                checksum_sha256=sha,
            )
            stored_attachments.append({
                "id": str(doc.id),
                "document_id": str(doc.id),
                "attachment_id": att.get("id", f"att-{uuid.uuid4().hex[:6]}"),
                "filename": filename,
                "document_type": doc_type,
                "mime_type": att.get("mime_type", "application/pdf"),
                "size": len(content_bytes),
                "checksum_sha256": sha,
            })

        email_data = {
            "id": message_id,
            "threadId": thread_id,
            "sender": clean_sender,
            "recipients": clean_recipients,
            "subject": subject,
            "body": body_text,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "attachments": stored_attachments,
        }
        self._mock_messages[message_id] = email_data
        self._mock_threads.setdefault(thread_id, []).append(message_id)

        self.msg_repo.create(
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
            raw_metadata={"mock": True, "attachment_count": len(stored_attachments)},
        )

        return email_data

    # ---------------- API Operations ---------------- #
    async def fetch_messages(self, query: str = "", limit: int = 20) -> List[Dict[str, Any]]:
        """Fetch incoming emails matching query via real Gmail API or local mock store."""
        if self.mock_mode:
            async def _fetch_mock():
                results = []
                # Clean query of Gmail operators like 'is:unread', 'label:INBOX', 'in:inbox' for mock matching
                clean_q = re.sub(r"\b(?:is:\w+|label:\w+|in:\w+)\b", "", query, flags=re.IGNORECASE).strip().lower()
                for msg in list(self._mock_messages.values()):
                    # Only return incoming emails from inbox
                    if msg.get("direction") == "outbound" or msg.get("status") == "sent":
                        continue
                    subj = (msg.get("subject") or "").lower()
                    body = (msg.get("body") or "").lower()
                    if not clean_q or clean_q in subj or clean_q in body:
                        results.append(msg)
                return results[:limit]
            return await self._execute_with_rate_limit_retry(_fetch_mock)

        # Real Google Gmail API execution
        token = await self._get_access_token()
        params = {"q": query or "label:INBOX", "maxResults": min(limit, 50)}
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{GMAIL_API_BASE_URL}/messages", params=params, headers=headers)
            if resp.status_code == 429:
                raise RateLimitExceeded("Gmail rate limit reached")
            if resp.status_code != 200:
                logger.error(f"Gmail fetch_messages error: {resp.status_code} {resp.text}")
                return []

            data = resp.json()
            message_items = data.get("messages", [])
            results = []
            for item in message_items:
                msg = await self.get_message(item["id"])
                if msg:
                    results.append(msg)
            return results

    async def get_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve full email message payload including headers, body, and attachments."""
        if self.mock_mode:
            async def _get_mock():
                return self._mock_messages.get(message_id)
            return await self._execute_with_rate_limit_retry(_get_mock)

        # Real Google Gmail API message retrieval
        token = await self._get_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{GMAIL_API_BASE_URL}/messages/{message_id}?format=full", headers=headers)
            if resp.status_code == 429:
                raise RateLimitExceeded("Gmail rate limit reached")
            if resp.status_code != 200:
                return None

            payload = resp.json()
            headers_list = payload.get("payload", {}).get("headers", [])
            header_map = {h.get("name", "").lower(): h.get("value", "") for h in headers_list}

            subject = header_map.get("subject", "")
            sender = normalize_email_address(header_map.get("from", ""))
            raw_to = header_map.get("to", "")
            recipients = [normalize_email_address(r) for r in raw_to.split(",") if r.strip()]
            thread_id = payload.get("threadId", message_id)

            # Parse message body & attachments from MIME parts
            body_text, attachments = self._parse_parts(payload.get("payload", {}))

            email_dict = {
                "id": message_id,
                "threadId": thread_id,
                "sender": sender,
                "recipients": recipients,
                "subject": subject,
                "body": body_text,
                "received_at": datetime.now(timezone.utc).isoformat(),
                "attachments": attachments,
            }

            # Store in database
            self.msg_repo.create(
                organization_id=self.organization_id,
                external_message_id=message_id,
                thread_id=thread_id,
                direction="inbound",
                sender=sender,
                recipients=recipients,
                subject=subject,
                body_text=body_text,
                received_at=datetime.now(timezone.utc),
                inbox_connection_id=self.inbox_connection_id,
                raw_metadata={"gmail_api": True, "attachment_count": len(attachments)},
            )
            return email_dict

    def _parse_parts(self, part: Dict[str, Any]) -> tuple[str, List[Dict[str, Any]]]:
        """Recursively extract plain text body and attachment metadata from Gmail MIME payload."""
        body_text = ""
        attachments = []

        mime_type = part.get("mimeType", "")
        filename = part.get("filename", "")
        body_data = part.get("body", {})

        if filename and body_data.get("attachmentId"):
            attachments.append({
                "attachment_id": body_data["attachmentId"],
                "filename": filename,
                "mime_type": mime_type,
                "size": body_data.get("size", 0),
                "document_type": classify_document_type(filename),
            })

        if mime_type == "text/plain" and body_data.get("data"):
            try:
                body_text += base64.urlsafe_b64decode(body_data["data"]).decode("utf-8", errors="replace")
            except Exception:
                pass

        for subpart in part.get("parts", []):
            sub_text, sub_atts = self._parse_parts(subpart)
            if sub_text:
                body_text += "\n" + sub_text
            attachments.extend(sub_atts)

        return body_text.strip(), attachments

    async def get_attachments(self, message_id: str) -> List[Dict[str, Any]]:
        """Download attachments for email message and store in DocumentRepository."""
        msg = await self.get_message(message_id)
        if not msg:
            return []

        if self.mock_mode:
            return msg.get("attachments", [])

        # In live mode, retrieve attachment bytes from Gmail API and persist to DocumentRepository
        token = await self._get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        stored = []

        async with httpx.AsyncClient(timeout=20.0) as client:
            for att in msg.get("attachments", []):
                att_id = att.get("attachment_id")
                if not att_id:
                    continue
                url = f"{GMAIL_API_BASE_URL}/messages/{message_id}/attachments/{att_id}"
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    raw_data = resp.json().get("data", "")
                    content_bytes = base64.urlsafe_b64decode(raw_data)
                    sha = hashlib.sha256(content_bytes).hexdigest()
                    filename = att.get("filename", "attachment.pdf")
                    doc_type = att.get("document_type") or classify_document_type(filename)

                    doc = self.doc_repo.create(
                        organization_id=self.organization_id,
                        file_name=filename,
                        storage_path=f"/attachments/{sha}_{filename}",
                        document_type=doc_type,
                        file_size_bytes=len(content_bytes),
                        mime_type=att.get("mime_type", "application/pdf"),
                        checksum_sha256=sha,
                    )
                    stored.append({
                        "id": str(doc.id),
                        "document_id": str(doc.id),
                        "attachment_id": att_id,
                        "filename": filename,
                        "document_type": doc_type,
                        "checksum_sha256": sha,
                        "size": len(content_bytes),
                    })
        return stored

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
        clean_to = [normalize_email_address(r) for r in to_recipients]
        clean_cc = [normalize_email_address(r) for r in (cc_recipients or [])]

        if self.mock_mode:
            async def _send_mock():
                outbound_msg_id = f"sent-{uuid.uuid4()}"
                outbound_thread_id = thread_id or f"thread-{uuid.uuid4()}"

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
                    raw_metadata={"in_reply_to": in_reply_to, "thread_id": outbound_thread_id},
                )
                result = {
                    "id": outbound_msg_id,
                    "message_id": outbound_msg_id,
                    "thread_id": outbound_thread_id,
                    "direction": "outbound",
                    "status": "sent",
                    "to": clean_to,
                    "subject": subject,
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                }
                self._mock_messages[outbound_msg_id] = result
                self._mock_threads.setdefault(outbound_thread_id, []).append(outbound_msg_id)
                return result

            return await self._execute_with_rate_limit_retry(_send_mock)

        # Real Google Gmail API message dispatch
        token = await self._get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        msg = email.message.EmailMessage()
        msg["To"] = ", ".join(clean_to)
        if clean_cc:
            msg["Cc"] = ", ".join(clean_cc)
        msg["Subject"] = subject
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to
        msg.set_content(body_text)

        raw_b64 = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        body_payload: Dict[str, Any] = {"raw": raw_b64}
        if thread_id:
            body_payload["threadId"] = thread_id

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{GMAIL_API_BASE_URL}/messages/send", json=body_payload, headers=headers)
            if resp.status_code not in (200, 201):
                raise ValueError(f"Failed to send email via Gmail API: {resp.text}")

            res_data = resp.json()
            outbound_msg_id = res_data.get("id", f"sent-{uuid.uuid4()}")
            outbound_thread_id = res_data.get("threadId", thread_id or outbound_msg_id)

            self.msg_repo.create(
                organization_id=self.organization_id,
                external_message_id=outbound_msg_id,
                thread_id=outbound_thread_id,
                direction="outbound",
                sender="me",
                recipients=clean_to + clean_cc,
                subject=subject,
                body_text=body_text,
                sent_at=datetime.now(timezone.utc),
                inbox_connection_id=self.inbox_connection_id,
                raw_metadata={"gmail_api": True, "in_reply_to": in_reply_to},
            )
            return {
                "message_id": outbound_msg_id,
                "thread_id": outbound_thread_id,
                "status": "sent",
                "to": clean_to,
                "subject": subject,
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }
