"""Organization repository."""

import uuid
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.domain.models import Organization


class OrganizationRepository:
    """Repository for tenant organization operations."""

    def __init__(self, db: Session):
        self.db = db

    def create(self, name: str, slug: str) -> Organization:
        org = Organization(id=uuid.uuid4(), name=name, slug=slug)
        self.db.add(org)
        self.db.commit()
        self.db.refresh(org)
        return org

    def get_by_id(self, org_id: uuid.UUID) -> Optional[Organization]:
        stmt = select(Organization).where(Organization.id == org_id)
        return self.db.execute(stmt).scalars().first()

    def get_by_slug(self, slug: str) -> Optional[Organization]:
        stmt = select(Organization).where(Organization.slug == slug)
        return self.db.execute(stmt).scalars().first()

    def list_all(self) -> List[Organization]:
        stmt = select(Organization).where(Organization.is_active.is_(True))
        return list(self.db.execute(stmt).scalars().all())
