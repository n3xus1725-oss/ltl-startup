"""Event dispatcher and retry-safe background queue processor."""

import asyncio
import uuid
from typing import Any, Callable, Coroutine, Dict, Tuple

from sqlalchemy.orm import Session

from packages.domain.events import (
    EventStatus,
    InboundEventPayload,
    NormalizedEvent,
    normalize_event,
)
from packages.domain.logging import logger
from packages.storage.repositories.audit import AuditLogRepository


class EventDispatcher:
    """Coordinates event ingestion, deduplication, audit persistence, and background worker dispatch."""

    def __init__(self, db: Session):
        self.db = db
        self.audit_repo = AuditLogRepository(db)

    def ingest(self, inbound: InboundEventPayload) -> Tuple[NormalizedEvent, bool]:
        """Ingest raw event, normalize, check idempotency, and record in audit log.

        Returns:
            Tuple[NormalizedEvent, bool]: (normalized_event, is_duplicate)
        """
        event = normalize_event(inbound)

        # Check idempotency in audit ledger
        existing_audit = self.audit_repo.get_by_idempotency_key(
            inbound.organization_id, event.idempotency_key
        )
        if existing_audit:
            logger.info(
                f"Duplicate event detected via idempotency key: {event.idempotency_key}",
                extra={
                    "event_id": str(existing_audit.event_id),
                    "idempotency_key": event.idempotency_key,
                },
            )
            # Reconstruct existing event status from metadata
            existing_event = NormalizedEvent(
                event_id=uuid.UUID(existing_audit.event_id),
                organization_id=inbound.organization_id,
                source=inbound.source,
                event_type=inbound.event_type,
                idempotency_key=event.idempotency_key,
                status=EventStatus(existing_audit.payload_after.get("status", EventStatus.COMPLETED)),
                raw_payload=existing_audit.metadata_json.get("raw_payload", {}),
                normalized_payload=existing_audit.payload_after.get("normalized_payload", {}),
            )
            return existing_event, True

        # New event: record received state in audit log
        event.status = EventStatus.RECEIVED
        self.audit_repo.record(
            organization_id=inbound.organization_id,
            event_id=str(event.event_id),
            action="inbound_event_received",
            actor_type="system",
            actor_id="event_ingestion_api",
            target_entity_type="event",
            target_entity_id=str(event.event_id),
            payload_before=None,
            payload_after={
                "status": event.status.value,
                "normalized_payload": event.normalized_payload,
            },
            metadata_json={
                "raw_payload": event.raw_payload,
                "source": event.source,
                "event_type": event.event_type,
            },
            idempotency_key=event.idempotency_key,
        )

        # Transition to QUEUED
        event.status = EventStatus.QUEUED
        logger.info(
            f"Event {event.event_id} queued for processing",
            extra={"event_id": str(event.event_id), "event_type": event.event_type},
        )
        return event, False

    async def execute_with_retry(
        self,
        event: NormalizedEvent,
        handler: Callable[[NormalizedEvent], Coroutine[Any, Any, Dict[str, Any]]],
    ) -> NormalizedEvent:
        """Execute event handler with retry-safe logic and status tracking."""
        event.status = EventStatus.PROCESSING

        while event.retry_count <= event.max_retries:
            try:
                result = await handler(event)
                event.status = EventStatus.COMPLETED
                logger.info(
                    f"Event {event.event_id} successfully completed",
                    extra={"event_id": str(event.event_id), "result": result},
                )
                # Update audit log
                self.audit_repo.record(
                    organization_id=event.organization_id,
                    event_id=str(event.event_id),
                    action="event_processing_completed",
                    actor_type="worker",
                    actor_id="event_dispatcher",
                    target_entity_type="event",
                    target_entity_id=str(event.event_id),
                    payload_after={"status": EventStatus.COMPLETED.value, "result": result},
                    metadata_json={"retry_count": event.retry_count},
                    idempotency_key=f"{event.idempotency_key}:completed",
                )
                return event
            except Exception as exc:
                event.retry_count += 1
                event.error_message = str(exc)
                logger.warning(
                    f"Event {event.event_id} attempt {event.retry_count} failed: {exc}",
                    extra={"event_id": str(event.event_id), "error": str(exc)},
                )
                if event.retry_count > event.max_retries:
                    break
                # Backoff before retry
                await asyncio.sleep(0.05 * (2 ** (event.retry_count - 1)))

        # Retries exhausted
        event.status = EventStatus.FAILED
        logger.error(
            f"Event {event.event_id} permanently failed after {event.retry_count} retries",
            extra={"event_id": str(event.event_id), "error": event.error_message},
        )
        self.audit_repo.record(
            organization_id=event.organization_id,
            event_id=str(event.event_id),
            action="event_processing_failed",
            actor_type="worker",
            actor_id="event_dispatcher",
            target_entity_type="event",
            target_entity_id=str(event.event_id),
            payload_after={
                "status": EventStatus.FAILED.value,
                "error": event.error_message,
            },
            metadata_json={"retry_count": event.retry_count},
            idempotency_key=f"{event.idempotency_key}:failed",
        )
        return event
