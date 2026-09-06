"""Audit log repository."""

import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.domain.models import AuditLog
from packages.storage.repositories.base import BaseRepository


class AuditLogRepository(BaseRepository[AuditLog]):
    """Repository for the append-only immutable audit ledger."""

    def __init__(self, db: Session):
        super().__init__(AuditLog, db)

    def record(
        self,
        organization_id: uuid.UUID,
        event_id: str,
        action: str,
        actor_type: str,
        actor_id: str,
        target_entity_type: Optional[str] = None,
        target_entity_id: Optional[str] = None,
        payload_before: Optional[Dict[str, Any]] = None,
        payload_after: Optional[Dict[str, Any]] = None,
        metadata_json: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> AuditLog:
        """Record an immutable audit entry. Idempotent on idempotency_key."""
        if idempotency_key:
            existing = self.get_by_idempotency_key(organization_id, idempotency_key)
            if existing:
                return existing

        entry = AuditLog(
            id=uuid.uuid4(),
            organization_id=organization_id,
            event_id=event_id,
            action=action,
            actor_type=actor_type,
            actor_id=actor_id,
            target_entity_type=target_entity_type,
            target_entity_id=target_entity_id,
            payload_before=payload_before,
            payload_after=payload_after,
            metadata_json=metadata_json or {},
            idempotency_key=idempotency_key,
        )
        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def get_by_idempotency_key(self, organization_id: uuid.UUID, idempotency_key: str) -> Optional[AuditLog]:
        stmt = select(AuditLog).where(
            AuditLog.organization_id == organization_id,
            AuditLog.idempotency_key == idempotency_key,
        )
        return self.db.execute(stmt).scalars().first()

    def list_by_entity(
        self, organization_id: uuid.UUID, target_entity_type: str, target_entity_id: str
    ) -> List[AuditLog]:
        stmt = (
            select(AuditLog)
            .where(
                AuditLog.organization_id == organization_id,
                AuditLog.target_entity_type == target_entity_type,
                AuditLog.target_entity_id == target_entity_id,
            )
            .order_by(AuditLog.created_at.desc())
        )
        return list(self.db.execute(stmt).scalars().all())

    def create_entry(
        self,
        organization_id: uuid.UUID,
        entity_type: str,
        entity_id: Any,
        event_type: str,
        action_taken: str,
        actor_id: str,
        actor_type: str = "agent",
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> AuditLog:
        return self.record(
            organization_id=organization_id,
            event_id=f"evt-{uuid.uuid4().hex[:8]}",
            action=action_taken,
            actor_type=actor_type,
            actor_id=actor_id,
            target_entity_type=entity_type,
            target_entity_id=str(entity_id),
            metadata_json=metadata,
        )

