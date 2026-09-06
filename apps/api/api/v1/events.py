import base64
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from apps.agent.inbox.service import InboxAgentService
from apps.agent.inbox.validators import InboxAgentInput
from packages.connectors.gmail import GmailConnector
from packages.domain.dispatcher import EventDispatcher
from packages.domain.events import InboundEventPayload
from packages.domain.models import InboxConnection, Organization
from packages.storage.db import get_db

router = APIRouter(prefix="/events", tags=["Events"])


@router.post("/inbound", status_code=status.HTTP_202_ACCEPTED)
async def ingest_inbound_event(
    payload: InboundEventPayload,
    db: Session = Depends(get_db),
):
    """Receive external event, normalize, check idempotency, persist audit, and queue for worker."""
    dispatcher = EventDispatcher(db)
    event, is_duplicate = dispatcher.ingest(payload)

    return {
        "event_id": str(event.event_id),
        "status": event.status.value,
        "idempotent_replay": is_duplicate,
        "received_at": event.received_at.isoformat(),
        "idempotency_key": event.idempotency_key,
    }


class GmailWebhookPayload(BaseModel):
    message: Optional[Dict[str, Any]] = None
    subscription: Optional[str] = None
    email_address: Optional[str] = None
    history_id: Optional[str] = None


@router.post("/gmail-webhook", status_code=status.HTTP_200_OK)
async def receive_gmail_pubsub_webhook(
    raw_payload: Dict[str, Any],
    db: Session = Depends(get_db),
):
    """Google Cloud Pub/Sub push notification endpoint for automated, hands-free Gmail ingestion."""
    # 1. Parse Pub/Sub envelope if present
    email_address = raw_payload.get("email_address")
    history_id = raw_payload.get("history_id")

    if "message" in raw_payload and isinstance(raw_payload["message"], dict):
        msg_data = raw_payload["message"].get("data")
        if msg_data:
            try:
                decoded = base64.b64decode(msg_data).decode("utf-8")
                parsed_json = json.loads(decoded)
                email_address = parsed_json.get("emailAddress", email_address)
                history_id = str(parsed_json.get("historyId", history_id))
            except Exception:
                pass

    # 2. Locate matching inbox connection or default organization
    org = None
    inbox_conn = None
    if email_address:
        inbox_conn = db.query(InboxConnection).filter(
            InboxConnection.email_address == email_address.lower().strip()
        ).first()
        if inbox_conn:
            org = db.query(Organization).filter(Organization.id == inbox_conn.organization_id).first()

    if not org:
        org = db.query(Organization).filter(Organization.slug == "nexus-freight-demo").first()
        if not org:
            org = Organization(name="Nexus Freight Logistics", slug="nexus-freight-demo", is_active=True)
            db.add(org)
            db.commit()
            db.refresh(org)

    # 3. Synchronize incoming messages
    connector = GmailConnector(
        organization_id=org.id,
        db=db,
        inbox_connection_id=inbox_conn.id if inbox_conn else None,
    )
    messages = await connector.fetch_messages(limit=5)

    dispatcher = EventDispatcher(db)
    agent_service = InboxAgentService(db=db)
    processed_events = []

    for msg in messages:
        msg_id = msg.get("id") or msg.get("message_id")
        if not msg_id:
            continue
        thread_id = msg.get("threadId") or msg.get("thread_id")
        inbound = InboundEventPayload(
            organization_id=org.id,
            source="gmail",
            event_type="email_received",
            idempotency_key=f"gmail-webhook-{msg_id}",
            payload={
                "message_id": msg_id,
                "thread_id": thread_id,
                "sender": msg.get("sender"),
                "recipients": msg.get("recipients", []),
                "subject": msg.get("subject"),
                "body_text": msg.get("body"),
                "attachments": msg.get("attachments", []),
            },
        )
        event, is_duplicate = dispatcher.ingest(inbound)
        if not is_duplicate:
            agent_input = InboxAgentInput(
                organization_id=str(org.id),
                trigger_event_id=str(event.event_id),
                sender=msg.get("sender") or "carrier@freight.com",
                subject=msg.get("subject") or "",
                body_text=msg.get("body") or "",
                thread_id=thread_id,
                attachments=msg.get("attachments", []),
            )
            res = await agent_service.run(agent_input)
            processed_events.append({"event_id": str(event.event_id), "run_id": res.run_id})

    return {
        "status": "acknowledged",
        "email_address": email_address,
        "history_id": history_id,
        "processed_count": len(processed_events),
    }
