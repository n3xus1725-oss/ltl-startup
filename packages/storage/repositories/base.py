"""Base repository with organization scoping and common operations."""

import uuid
from typing import Any, Dict, Generic, List, Optional, Type, TypeVar
from sqlalchemy import select, update, delete
from sqlalchemy.orm import Session
from packages.storage.db import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """Abstract generic repository ensuring strict organization-level data isolation."""

    def __init__(self, model: Type[ModelType], db: Session):
        self.model = model
        self.db = db

    def get_by_id(self, organization_id: uuid.UUID, entity_id: uuid.UUID) -> Optional[ModelType]:
        """Fetch a single record strictly scoped by organization_id."""
        stmt = select(self.model).where(
            self.model.organization_id == organization_id,
            self.model.id == entity_id,
        )
        return self.db.execute(stmt).scalars().first()

    def list_all(
        self,
        organization_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> List[ModelType]:
        """List records scoped by organization_id with pagination."""
        stmt = (
            select(self.model)
            .where(self.model.organization_id == organization_id)
            .limit(limit)
            .offset(offset)
        )
        return list(self.db.execute(stmt).scalars().all())

    def delete(self, organization_id: uuid.UUID, entity_id: uuid.UUID) -> bool:
        """Delete a single record strictly scoped by organization_id."""
        stmt = delete(self.model).where(
            self.model.organization_id == organization_id,
            self.model.id == entity_id,
        )
        result = self.db.execute(stmt)
        self.db.commit()
        return result.rowcount > 0
