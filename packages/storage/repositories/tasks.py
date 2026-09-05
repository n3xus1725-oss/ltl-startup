"""Operational task repository."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.orm import Session
from packages.domain.models import TaskRecord
from packages.storage.repositories.base import BaseRepository


class TaskRepository(BaseRepository[TaskRecord]):
    """Repository for managing operational and review tasks with organization scoping and idempotency."""

    def __init__(self, db: Session):
        super().__init__(TaskRecord, db)

    def create_task(
        self,
        organization_id: uuid.UUID,
        title: str,
        task_type: str = "review",
        description: Optional[str] = None,
        shipment_id: Optional[uuid.UUID] = None,
        priority: str = "medium",
        assigned_to: Optional[uuid.UUID] = None,
        due_date: Optional[datetime] = None,
        details: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> TaskRecord:
        """Create a task. Enforces idempotency via idempotency_key if provided."""
        if idempotency_key:
            existing = self.get_by_idempotency_key(organization_id, idempotency_key)
            if existing:
                return existing

        task = TaskRecord(
            id=uuid.uuid4(),
            organization_id=organization_id,
            shipment_id=shipment_id,
            task_type=task_type,
            title=title,
            description=description,
            priority=priority,
            status="pending",
            assigned_to=assigned_to,
            due_date=due_date,
            details=details or {},
            idempotency_key=idempotency_key,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        return task

    def get_by_idempotency_key(
        self, organization_id: uuid.UUID, idempotency_key: str
    ) -> Optional[TaskRecord]:
        stmt = select(TaskRecord).where(
            TaskRecord.organization_id == organization_id,
            TaskRecord.idempotency_key == idempotency_key,
        )
        return self.db.execute(stmt).scalars().first()

    def list_by_shipment(
        self, organization_id: uuid.UUID, shipment_id: uuid.UUID
    ) -> List[TaskRecord]:
        stmt = (
            select(TaskRecord)
            .where(
                TaskRecord.organization_id == organization_id,
                TaskRecord.shipment_id == shipment_id,
            )
            .order_by(TaskRecord.created_at.desc())
        )
        return list(self.db.execute(stmt).scalars().all())

    def update_task_status(
        self,
        organization_id: uuid.UUID,
        task_id: uuid.UUID,
        status: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Optional[TaskRecord]:
        task = self.get_by_id(organization_id, task_id)
        if not task:
            return None
        task.status = status
        if details:
            current_details = dict(task.details or {})
            current_details.update(details)
            task.details = current_details
        task.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(task)
        return task
