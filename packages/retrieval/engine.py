"""Phase 2.5 — Unified Shipment Retrieval Engine."""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.domain.models import Document, ShipmentConflict
from packages.retrieval.chunker import DocumentChunker
from packages.retrieval.keyword import KeywordSearchEngine
from packages.retrieval.schemas import SearchResultItem, ShipmentContextResult
from packages.retrieval.vector import VectorSearchEngine
from packages.storage.repositories.shipments import ShipmentRepository


class ShipmentRetrievalEngine:
    """Consolidated retrieval layer combining relational, keyword, and semantic search."""

    def __init__(self, db: Session, vector_engine: Optional[VectorSearchEngine] = None):
        self.db = db
        self.shipment_repo = ShipmentRepository(db)
        self.keyword_search = KeywordSearchEngine(db)
        self.vector_search = vector_engine or VectorSearchEngine()

    def search_all(
        self,
        organization_id: uuid.UUID,
        query: str,
        limit: int = 10,
    ) -> List[SearchResultItem]:
        """Perform unified multi-entity search across shipments, documents, and messages."""
        shipment_results = self.keyword_search.search_shipments(organization_id, query, limit=limit)
        doc_results = self.keyword_search.search_documents(organization_id, query, limit=limit)
        msg_results = self.keyword_search.search_messages(organization_id, query, limit=limit)

        combined = shipment_results + doc_results + msg_results
        combined.sort(key=lambda x: x.relevance_score, reverse=True)
        return combined[:limit]

    def get_canonical_context(
        self,
        organization_id: uuid.UUID,
        shipment_id: uuid.UUID,
    ) -> Optional[ShipmentContextResult]:
        """Retrieve complete canonical context, provenance trail, attached documents, and active conflicts."""
        shp = self.shipment_repo.get_by_id(organization_id, shipment_id)
        if not shp:
            return None

        canonical = self.shipment_repo.get_canonical(organization_id, shipment_id)
        ledger = self.shipment_repo.get_provenance_ledger(organization_id, shipment_id)

        # Attached documents
        doc_stmt = select(Document).where(
            Document.organization_id == organization_id,
            Document.shipment_id == shipment_id,
        )
        docs = list(self.db.scalars(doc_stmt).all())
        attached_docs = [
            {
                "id": str(d.id),
                "file_name": d.file_name,
                "document_type": d.document_type,
                "sha256": d.checksum_sha256,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in docs
        ]

        # Active conflicts
        conflict_stmt = select(ShipmentConflict).where(
            ShipmentConflict.organization_id == organization_id,
            ShipmentConflict.shipment_id == shipment_id,
            ShipmentConflict.status == "open",
        )
        conflicts = list(self.db.scalars(conflict_stmt).all())
        conflict_items = [
            {
                "id": str(c.id),
                "field_name": c.field_name,
                "conflict_type": c.conflict_type,
                "severity": c.severity,
                "source_a": c.source_a,
                "source_b": c.source_b,
                "explanation": c.explanation,
                "recommended_workflow": c.recommended_workflow,
            }
            for c in conflicts
        ]

        # Timeline events
        events = self.shipment_repo.get_events(organization_id, shipment_id)
        timeline = [
            {
                "id": str(e.id),
                "event_type": e.event_type,
                "source": e.source,
                "timestamp": e.event_timestamp.isoformat() if e.event_timestamp else None,
                "payload": e.normalized_payload or {},
            }
            for e in events
        ]

        # Provenance trail summary
        prov_dict = {}
        if ledger and ledger.active_assertions:
            for field, rec in ledger.active_assertions.items():
                prov_dict[field] = {
                    "current_value": rec.value,
                    "source_type": rec.source,
                    "source_id": rec.source_id,
                    "authority_score": rec.authority,
                    "confidence": rec.confidence,
                    "updated_at": rec.timestamp.isoformat() if isinstance(rec.timestamp, datetime) else str(rec.timestamp),
                    "history_count": len(ledger.get_history(field)),
                }

        return ShipmentContextResult(
            organization_id=str(organization_id),
            shipment_id=str(shipment_id),
            canonical_data=canonical if canonical else None,
            provenance_trail=prov_dict,
            attached_documents=attached_docs,
            active_conflicts=conflict_items,
            timeline_events=timeline,
        )

    def get_field_provenance_trail(
        self,
        organization_id: uuid.UUID,
        shipment_id: uuid.UUID,
        field_path: str,
    ) -> Optional[Dict[str, Any]]:
        """Retrieve granular assertion history for a specific canonical field."""
        ledger = self.shipment_repo.get_provenance_ledger(organization_id, shipment_id)
        if not ledger:
            return None

        record = ledger.get_active(field_path)
        if not record:
            return None

        history = ledger.get_history(field_path)

        return {
            "field_name": record.field,
            "current_value": record.value,
            "source_type": record.source,
            "source_id": record.source_id,
            "authority_score": record.authority,
            "confidence": record.confidence,
            "evidence": record.evidence,
            "updated_at": record.timestamp.isoformat() if isinstance(record.timestamp, datetime) else str(record.timestamp),
            "history": [
                {
                    "source_type": h.source,
                    "source_id": h.source_id,
                    "value": h.value,
                    "authority_score": h.authority,
                    "confidence": h.confidence,
                    "timestamp": h.timestamp.isoformat() if isinstance(h.timestamp, datetime) else str(h.timestamp),
                    "evidence": h.evidence,
                }
                for h in history
            ],
        }

    async def index_document_for_search(
        self,
        organization_id: uuid.UUID,
        document_id: uuid.UUID,
        text: str,
        document_type: str,
    ) -> int:
        """Chunk and index document text for semantic search."""
        chunks = DocumentChunker.chunk_text(
            text=text,
            source_id=str(document_id),
            source_type=document_type,
            metadata={"organization_id": str(organization_id)},
        )
        if chunks:
            return await self.vector_search.index_chunks(organization_id, chunks)
        return 0
