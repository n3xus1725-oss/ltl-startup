"""Inbound event ingestion API endpoints."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from packages.domain.dispatcher import EventDispatcher
from packages.domain.events import InboundEventPayload
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
