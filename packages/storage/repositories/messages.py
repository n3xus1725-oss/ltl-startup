"""Message repository."""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.orm import Session
from packages.domain.models import Message
from packages.storage.repositories.base import BaseRepository


class MessageRepository(BaseRepository[Message]):
    """Repository for managing inbound and outbound communication records."""

    def __init__(self, db: Session):
        super().__init__(Message, db)

    def create(
        self,
        organization_id: uuid.UUID,
        external_message_id: str,
        thread_id: str,
        direction: str,
        sender: str,
        recipients: List[str],
        subject: Optional[str] = None,
        body_text: Optional[str] = None,
        body_html: Optional[str] = None,
        received_at: Optional[datetime] = None,
        sent_at: Optional[datetime] = None,
        shipment_id: Optional[uuid.UUID] = None,
        inbox_connection_id: Optional[uuid.UUID] = None,
        raw_metadata: Optional[Dict[str, Any]] = None,
    ) -> Message:
        # Check idempotency by external_message_id
        existing = self.get_by_external_id(organization_id, external_message_id)
        if existing:
            return existing

        message = Message(
            id=uuid.uuid4(),
            organization_id=organization_id,
            shipment_id=shipment_id,
            inbox_connection_id=inbox_connection_id,
            external_message_id=external_message_id,
            thread_id=thread_id,
            direction=direction,
            sender=sender,
            recipients=recipients,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            received_at=received_at,
            sent_at=sent_at,
            is_processed=False,
            raw_metadata=raw_metadata or {},
        )
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return message

    def get_by_external_id(self, organization_id: uuid.UUID, external_message_id: str) -> Optional[Message]:
        stmt = select(Message).where(
            Message.organization_id == organization_id,
            Message.external_message_id == external_message_id,
        )
        return self.db.execute(stmt).scalars().first()

    def get_by_thread_id(self, organization_id: uuid.UUID, thread_id: str) -> List[Message]:
        stmt = (
            select(Message)
            .where(
                Message.organization_id == organization_id,
                Message.thread_id == thread_id,
            )
            .order_by(Message.created_at.asc())
        )
        return list(self.db.execute(stmt).scalars().all())

    def mark_as_processed(self, organization_id: uuid.UUID, message_id: uuid.UUID) -> Optional[Message]:
        message = self.get_by_id(organization_id, message_id)
        if message:
            message.is_processed = True
            self.db.commit()
            self.db.refresh(message)
        return message

    def link_shipment(
        self, organization_id: uuid.UUID, message_id: uuid.UUID, shipment_id: uuid.UUID
    ) -> Optional[Message]:
        message = self.get_by_id(organization_id, message_id)
        if message:
            message.shipment_id = shipment_id
            self.db.commit()
            self.db.refresh(message)
        return message
