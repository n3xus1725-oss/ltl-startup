"""Phase 2.5 — Relational & Keyword Search Engine."""

import re
import uuid
from typing import List, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from packages.domain.models import Document, Message, Shipment
from packages.retrieval.schemas import SearchResultItem


class KeywordSearchEngine:
    """Multi-tenant relational and keyword search over shipments, documents, and messages."""

    def __init__(self, db: Session):
        self.db = db

    def search_shipments(
        self,
        organization_id: uuid.UUID,
        query: str,
        limit: int = 10,
    ) -> List[SearchResultItem]:
        """Search shipment records by candidate identifiers, carrier, or status."""
        clean_q = query.strip()
        if not clean_q:
            return []

        pattern = f"%{clean_q}%"
        stmt = (
            select(Shipment)
            .where(
                Shipment.organization_id == organization_id,
                or_(
                    Shipment.shipment_number.ilike(pattern),
                    Shipment.carrier_reference.ilike(pattern),
                    Shipment.bol_number.ilike(pattern),
                    Shipment.external_invoice_id.ilike(pattern),
                    Shipment.carrier_name.ilike(pattern),
                    Shipment.status.ilike(pattern),
                ),
            )
            .limit(limit)
        )
        shipments = list(self.db.scalars(stmt).all())
        results: List[SearchResultItem] = []

        for shp in shipments:
            matched: List[str] = []
            score = 0.5
            q_lower = clean_q.lower()
            if shp.shipment_number and q_lower in shp.shipment_number.lower():
                matched.append("shipment_number")
                score = 1.0 if q_lower == shp.shipment_number.lower() else 0.9
            if shp.bol_number and q_lower in shp.bol_number.lower():
                matched.append("bol_number")
                score = max(score, 0.95 if q_lower == shp.bol_number.lower() else 0.85)
            if shp.carrier_reference and q_lower in shp.carrier_reference.lower():
                matched.append("carrier_reference")
                score = max(score, 0.95 if q_lower == shp.carrier_reference.lower() else 0.85)
            if shp.carrier_name and q_lower in shp.carrier_name.lower():
                matched.append("carrier_name")
                score = max(score, 0.8)

            snippet = f"Shipment #{shp.shipment_number} | Status: {shp.status} | Carrier: {shp.carrier_name or 'N/A'}"
            results.append(
                SearchResultItem(
                    entity_type="shipment",
                    entity_id=str(shp.id),
                    title=f"Shipment #{shp.shipment_number}",
                    matched_fields=matched or ["general_match"],
                    relevance_score=score,
                    snippet=snippet,
                    metadata={
                        "shipment_number": shp.shipment_number,
                        "status": shp.status,
                        "carrier_name": shp.carrier_name,
                        "pickup_date": shp.pickup_date.isoformat() if shp.pickup_date else None,
                        "delivery_date": shp.delivery_date.isoformat() if shp.delivery_date else None,
                        "bol_number": shp.bol_number,
                    },
                )
            )

        results.sort(key=lambda x: x.relevance_score, reverse=True)
        return results

    def search_documents(
        self,
        organization_id: uuid.UUID,
        query: str,
        document_types: Optional[List[str]] = None,
        shipment_id: Optional[uuid.UUID] = None,
        limit: int = 10,
    ) -> List[SearchResultItem]:
        """Search ingested documents by text, filename, or type."""
        clean_q = query.strip()
        if not clean_q:
            return []

        stmt = select(Document).where(Document.organization_id == organization_id)
        if shipment_id:
            stmt = stmt.where(Document.shipment_id == shipment_id)
        if document_types:
            stmt = stmt.where(Document.document_type.in_(document_types))

        all_docs = list(self.db.scalars(stmt).all())
        results: List[SearchResultItem] = []
        q_terms = [t.lower() for t in re.split(r"\s+", clean_q) if len(t) >= 2]

        for doc in all_docs:
            extracted_str = ""
            if isinstance(doc.extracted_data, dict):
                extracted_str = str(doc.extracted_data.get("raw_text") or doc.extracted_data.get("text") or doc.extracted_data)
            elif doc.extracted_data:
                extracted_str = str(doc.extracted_data)

            doc_text_lower = f"{doc.file_name} {doc.document_type} {extracted_str}".lower()
            term_hits = sum(1 for t in q_terms if t in doc_text_lower)

            if term_hits == 0 and clean_q.lower() not in doc_text_lower:
                continue

            score = 0.5
            if term_hits > 0:
                score = min(0.95, 0.5 + (term_hits / max(len(q_terms), 1)) * 0.45)
            if clean_q.lower() in doc.file_name.lower():
                score = max(score, 0.9)

            snippet = f"Document: {doc.file_name} ({doc.document_type})"
            if extracted_str:
                idx = extracted_str.lower().find(clean_q.lower())
                if idx >= 0:
                    start = max(0, idx - 40)
                    end = min(len(extracted_str), idx + len(clean_q) + 60)
                    snippet = "..." + extracted_str[start:end].replace("\n", " ") + "..."
                else:
                    snippet = extracted_str[:150].replace("\n", " ") + "..."

            results.append(
                SearchResultItem(
                    entity_type="document",
                    entity_id=str(doc.id),
                    title=f"{doc.document_type}: {doc.file_name}",
                    matched_fields=["extracted_data"] if term_hits else ["file_name"],
                    relevance_score=score,
                    snippet=snippet,
                    metadata={
                        "document_type": doc.document_type,
                        "file_name": doc.file_name,
                        "shipment_id": str(doc.shipment_id) if doc.shipment_id else None,
                        "sha256": doc.checksum_sha256,
                    },
                )
            )

        results.sort(key=lambda x: x.relevance_score, reverse=True)
        return results[:limit]

    def search_messages(
        self,
        organization_id: uuid.UUID,
        query: str,
        shipment_id: Optional[uuid.UUID] = None,
        limit: int = 10,
    ) -> List[SearchResultItem]:
        """Search incoming/outgoing email messages by subject or body."""
        clean_q = query.strip()
        if not clean_q:
            return []

        pattern = f"%{clean_q}%"
        stmt = select(Message).where(
            Message.organization_id == organization_id,
            or_(
                Message.subject.ilike(pattern),
                Message.body_text.ilike(pattern),
                Message.sender.ilike(pattern),
            ),
        )
        if shipment_id:
            stmt = stmt.where(Message.shipment_id == shipment_id)

        stmt = stmt.limit(limit)
        msgs = list(self.db.scalars(stmt).all())
        results: List[SearchResultItem] = []

        for msg in msgs:
            body = msg.body_text or ""
            score = 0.6
            if clean_q.lower() in (msg.subject or "").lower():
                score = 0.9
            snippet = f"Email from {msg.sender}: {msg.subject} — {body[:120].strip()}..."

            results.append(
                SearchResultItem(
                    entity_type="message",
                    entity_id=str(msg.id),
                    title=f"Message: {msg.subject}",
                    matched_fields=["subject"] if clean_q.lower() in (msg.subject or "").lower() else ["body_text"],
                    relevance_score=score,
                    snippet=snippet,
                    metadata={
                        "sender": msg.sender,
                        "thread_id": msg.thread_id,
                        "shipment_id": str(msg.shipment_id) if msg.shipment_id else None,
                    },
                )
            )

        results.sort(key=lambda x: x.relevance_score, reverse=True)
        return results
