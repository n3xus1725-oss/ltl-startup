"""Freight and operational exceptions repository."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.orm import Session
from packages.domain.models import ExceptionRecord
from packages.storage.repositories.base import BaseRepository


class ExceptionRepository(BaseRepository[ExceptionRecord]):
    """Repository for managing operational exceptions with organization scoping and idempotency."""

    def __init__(self, db: Session):
        super().__init__(ExceptionRecord, db)

    def create_exception(
        self,
        organization_id: uuid.UUID,
        exception_type: str,
        severity: str = "medium",
        shipment_id: Optional[uuid.UUID] = None,
        details: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> ExceptionRecord:
        """Create an exception record. Enforces idempotency via idempotency_key if provided."""
        if idempotency_key:
            existing = self.get_by_idempotency_key(organization_id, idempotency_key)
            if existing:
                return existing

        exc = ExceptionRecord(
            id=uuid.uuid4(),
            organization_id=organization_id,
            shipment_id=shipment_id,
            exception_type=exception_type,
            severity=severity,
            status="open",
            details=details or {},
            idempotency_key=idempotency_key,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.db.add(exc)
        self.db.commit()
        self.db.refresh(exc)
        return exc

    def get_by_idempotency_key(
        self, organization_id: uuid.UUID, idempotency_key: str
    ) -> Optional[ExceptionRecord]:
        stmt = select(ExceptionRecord).where(
            ExceptionRecord.organization_id == organization_id,
            ExceptionRecord.idempotency_key == idempotency_key,
        )
        return self.db.execute(stmt).scalars().first()

    def list_by_shipment(
        self, organization_id: uuid.UUID, shipment_id: uuid.UUID
    ) -> List[ExceptionRecord]:
        stmt = (
            select(ExceptionRecord)
            .where(
                ExceptionRecord.organization_id == organization_id,
                ExceptionRecord.shipment_id == shipment_id,
            )
            .order_by(ExceptionRecord.created_at.desc())
        )
        return list(self.db.execute(stmt).scalars().all())

    def resolve_exception(
        self,
        organization_id: uuid.UUID,
        exception_id: uuid.UUID,
        resolution_notes: str,
    ) -> Optional[ExceptionRecord]:
        exc = self.get_by_id(organization_id, exception_id)
        if not exc:
            return None
        exc.status = "resolved"
        exc.resolution_notes = resolution_notes
        exc.resolved_at = datetime.now(timezone.utc)
        exc.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(exc)
        return exc
