"""Domain event definitions, normalization, and status lifecycles."""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class EventStatus(str, Enum):
    RECEIVED = "received"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class InboundEventPayload(BaseModel):
    """Raw incoming event payload from external caller or webhook."""
    organization_id: uuid.UUID = Field(..., description="Multi-tenant organization boundary")
    source: str = Field(..., description="Event origin: gmail, carrier, webhook, api, etc.")
    event_type: str = Field(..., description="Type of event: email_received, eta_update, pickup_confirmed, etc.")
    idempotency_key: Optional[str] = Field(default=None, description="Optional caller idempotency key")
    timestamp: Optional[datetime] = Field(default=None, description="Event origin timestamp")
    payload: Dict[str, Any] = Field(default_factory=dict, description="Raw payload data")


class NormalizedEvent(BaseModel):
    """Normalized internal canonical event schema."""
    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    organization_id: uuid.UUID
    source: str
    event_type: str
    idempotency_key: str
    status: EventStatus = EventStatus.RECEIVED
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw_payload: Dict[str, Any]
    normalized_payload: Dict[str, Any]
    retry_count: int = 0
    max_retries: int = 3
    error_message: Optional[str] = None


def generate_deterministic_idempotency_key(org_id: uuid.UUID, source: str, event_type: str, payload: Dict[str, Any]) -> str:
    """Derive deterministic idempotency key from payload content when none is supplied."""
    payload_str = json.dumps(payload, sort_keys=True, default=str)
    raw = f"{org_id}:{source}:{event_type}:{payload_str}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_event(inbound: InboundEventPayload) -> NormalizedEvent:
    """Normalize raw external event into internal canonical schema."""
    idemp_key = inbound.idempotency_key or generate_deterministic_idempotency_key(
        inbound.organization_id, inbound.source, inbound.event_type, inbound.payload
    )

    normalized_data: Dict[str, Any] = {}

    # Standard normalization of common fields
    raw = inbound.payload
    if "shipment_id" in raw:
        normalized_data["shipment_id"] = str(raw["shipment_id"]).strip()
    if "shipment_number" in raw:
        normalized_data["shipment_number"] = str(raw["shipment_number"]).strip()
    if "carrier_reference" in raw:
        normalized_data["carrier_reference"] = str(raw["carrier_reference"]).strip()
    if "pro_number" in raw:
        normalized_data["carrier_reference"] = str(raw["pro_number"]).strip()
    if "bol_number" in raw:
        normalized_data["bol_number"] = str(raw["bol_number"]).strip()
    if "status" in raw:
        normalized_data["status"] = str(raw["status"]).lower().strip()
    if "eta" in raw:
        normalized_data["eta"] = raw["eta"]
    if "pickup_date" in raw:
        normalized_data["pickup_date"] = raw["pickup_date"]
    if "email" in raw:
        normalized_data["email"] = raw["email"]

    # Preserve remaining raw fields
    for k, v in raw.items():
        if k not in normalized_data:
            normalized_data[k] = v

    return NormalizedEvent(
        organization_id=inbound.organization_id,
        source=inbound.source,
        event_type=inbound.event_type,
        idempotency_key=idemp_key,
        status=EventStatus.RECEIVED,
        raw_payload=inbound.payload,
        normalized_payload=normalized_data,
    )
