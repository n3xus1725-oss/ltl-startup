"""Document repository."""

import uuid
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.orm import Session
from packages.domain.models import Document
from packages.storage.repositories.base import BaseRepository


class DocumentRepository(BaseRepository[Document]):
    """Repository for freight documents and attachments."""

    def __init__(self, db: Session):
        super().__init__(Document, db)

    def create(
        self,
        organization_id: uuid.UUID,
        file_name: str,
        storage_path: str,
        document_type: str = "UNKNOWN",
        file_size_bytes: Optional[int] = None,
        mime_type: Optional[str] = None,
        checksum_sha256: Optional[str] = None,
        shipment_id: Optional[uuid.UUID] = None,
        extracted_data: Optional[Dict[str, Any]] = None,
    ) -> Document:
        # Check idempotency via sha256 checksum if provided
        if checksum_sha256:
            existing = self.get_by_checksum(organization_id, checksum_sha256)
            if existing:
                return existing

        doc = Document(
            id=uuid.uuid4(),
            organization_id=organization_id,
            shipment_id=shipment_id,
            document_type=document_type,
            file_name=file_name,
            file_size_bytes=file_size_bytes,
            mime_type=mime_type,
            storage_path=storage_path,
            checksum_sha256=checksum_sha256,
            extracted_data=extracted_data or {},
        )
        self.db.add(doc)
        self.db.commit()
        self.db.refresh(doc)
        return doc

    def get_by_checksum(self, organization_id: uuid.UUID, checksum_sha256: str) -> Optional[Document]:
        stmt = select(Document).where(
            Document.organization_id == organization_id,
            Document.checksum_sha256 == checksum_sha256,
        )
        return self.db.execute(stmt).scalars().first()

    def list_by_shipment(self, organization_id: uuid.UUID, shipment_id: uuid.UUID) -> List[Document]:
        stmt = (
            select(Document)
            .where(
                Document.organization_id == organization_id,
                Document.shipment_id == shipment_id,
            )
            .order_by(Document.created_at.desc())
        )
        return list(self.db.execute(stmt).scalars().all())
