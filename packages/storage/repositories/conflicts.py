"""Repository for detected shipment conflicts."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.domain.models import ShipmentConflict
from packages.storage.repositories.base import BaseRepository


class ConflictRepository(BaseRepository[ShipmentConflict]):
    """Repository for managing detected data and authority conflicts."""

    def __init__(self, db: Session):
        super().__init__(ShipmentConflict, db)

    def create_conflict(
        self,
        organization_id: uuid.UUID,
        shipment_id: uuid.UUID,
        field_name: str,
        conflict_type: str,
        source_a: Dict[str, Any],
        source_b: Dict[str, Any],
        explanation: str,
        severity: str = "medium",
        recommended_workflow: str = "operator_review",
        idempotency_key: Optional[str] = None,
    ) -> ShipmentConflict:
        """Create or return existing conflict for idempotency."""
        if idempotency_key:
            existing = self.db.execute(
                select(ShipmentConflict).where(
                    ShipmentConflict.organization_id == organization_id,
                    ShipmentConflict.shipment_id == shipment_id,
                    ShipmentConflict.field_name == field_name,
                    ShipmentConflict.idempotency_key == idempotency_key,
                )
            ).scalars().first()
            if existing:
                return existing

        conflict = ShipmentConflict(
            id=uuid.uuid4(),
            organization_id=organization_id,
            shipment_id=shipment_id,
            field_name=field_name,
            conflict_type=conflict_type,
            source_a=source_a,
            source_b=source_b,
            severity=severity,
            explanation=explanation,
            recommended_workflow=recommended_workflow,
            status="open",
            idempotency_key=idempotency_key,
        )
        self.db.add(conflict)
        self.db.commit()
        self.db.refresh(conflict)
        return conflict

    create = create_conflict

    def list_by_shipment(
        self, organization_id: uuid.UUID, shipment_id: uuid.UUID, status: Optional[str] = None
    ) -> List[ShipmentConflict]:
        stmt = select(ShipmentConflict).where(
            ShipmentConflict.organization_id == organization_id,
            ShipmentConflict.shipment_id == shipment_id,
        )
        if status:
            stmt = stmt.where(ShipmentConflict.status == status)
        stmt = stmt.order_by(ShipmentConflict.created_at.desc())
        return list(self.db.execute(stmt).scalars().all())

    def list_by_organization(
        self, organization_id: uuid.UUID, status: Optional[str] = "open", limit: int = 50
    ) -> List[ShipmentConflict]:
        stmt = select(ShipmentConflict).where(
            ShipmentConflict.organization_id == organization_id
        )
        if status:
            stmt = stmt.where(ShipmentConflict.status == status)
        stmt = stmt.order_by(ShipmentConflict.created_at.desc()).limit(limit)
        return list(self.db.execute(stmt).scalars().all())

    def resolve_conflict(
        self,
        organization_id: uuid.UUID,
        conflict_id: uuid.UUID,
        resolved_by: str,
        resolved_value: Any,
        status: str = "resolved",
    ) -> Optional[ShipmentConflict]:
        conflict = self.get_by_id(organization_id, conflict_id)
        if not conflict:
            return None

        conflict.status = status
        conflict.resolved_by = resolved_by
        conflict.resolved_value = resolved_value
        conflict.resolved_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(conflict)
        return conflict
