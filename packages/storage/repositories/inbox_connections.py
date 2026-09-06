"""Inbox connection repository."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.domain.models import InboxConnection
from packages.storage.repositories.base import BaseRepository


class InboxConnectionRepository(BaseRepository[InboxConnection]):
    """Repository for managing mailbox provider connection credentials and status."""

    def __init__(self, db: Session):
        super().__init__(InboxConnection, db)

    def get_by_email(
        self, organization_id: uuid.UUID, email_address: str
    ) -> Optional[InboxConnection]:
        stmt = select(InboxConnection).where(
            InboxConnection.organization_id == organization_id,
            InboxConnection.email_address == email_address.lower().strip(),
        )
        return self.db.execute(stmt).scalars().first()

    def list_by_org(
        self, organization_id: uuid.UUID, provider: Optional[str] = None
    ) -> List[InboxConnection]:
        stmt = select(InboxConnection).where(
            InboxConnection.organization_id == organization_id
        )
        if provider:
            stmt = stmt.where(InboxConnection.provider == provider)
        return list(self.db.execute(stmt).scalars().all())

    def upsert_connection(
        self,
        organization_id: uuid.UUID,
        email_address: str,
        provider: str = "gmail",
        status: str = "active",
        credentials: Optional[Dict[str, Any]] = None,
        sync_cursor: Optional[str] = None,
    ) -> InboxConnection:
        existing = self.get_by_email(organization_id, email_address)
        if existing:
            existing.status = status
            existing.provider = provider
            if credentials:
                existing.credentials_encrypted = credentials
            if sync_cursor:
                existing.sync_cursor = sync_cursor
            existing.updated_at = datetime.now(timezone.utc)
            self.db.commit()
            self.db.refresh(existing)
            return existing

        conn = InboxConnection(
            id=uuid.uuid4(),
            organization_id=organization_id,
            email_address=email_address.lower().strip(),
            provider=provider,
            status=status,
            credentials_encrypted=credentials or {},
            sync_cursor=sync_cursor,
        )
        self.db.add(conn)
        self.db.commit()
        self.db.refresh(conn)
        return conn

    def update_sync_status(
        self,
        organization_id: uuid.UUID,
        connection_id: uuid.UUID,
        sync_cursor: Optional[str] = None,
        last_synced_at: Optional[datetime] = None,
    ) -> Optional[InboxConnection]:
        conn = self.get_by_id(organization_id, connection_id)
        if not conn:
            return None
        if sync_cursor is not None:
            conn.sync_cursor = sync_cursor
        conn.last_synced_at = last_synced_at or datetime.now(timezone.utc)
        conn.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(conn)
        return conn
