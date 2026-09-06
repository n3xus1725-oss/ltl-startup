"""Phase 2.5 — Pydantic Schemas for Retrieval Layer."""

import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """Query parameters for shipment and document context retrieval."""
    query: str = Field(..., description="Free-text search query or candidate identifier")
    organization_id: uuid.UUID = Field(..., description="Organization scope boundary")
    shipment_id: Optional[uuid.UUID] = Field(None, description="Optional target shipment constraint")
    document_types: Optional[List[str]] = Field(None, description="Optional filter by document types")
    top_k: int = Field(default=5, ge=1, le=50, description="Max results to return")
    min_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Minimum relevance score threshold")


class ContextChunk(BaseModel):
    """A granular chunk of evidence from a document or message."""
    chunk_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_type: str = Field(..., description="Document type, message, or canonical field")
    source_id: str = Field(..., description="Document ID or message ID")
    content: str = Field(..., description="Raw text snippet or table slice")
    relevance_score: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SearchResultItem(BaseModel):
    """A ranked search result containing matched entity and evidence snippets."""
    entity_type: str = Field(..., description="shipment, document, message, or provenance")
    entity_id: str = Field(...)
    title: str = Field(...)
    matched_fields: List[str] = Field(default_factory=list)
    relevance_score: float = Field(default=1.0)
    snippet: str = Field(...)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ShipmentContextResult(BaseModel):
    """Complete consolidated retrieval context for a shipment."""
    organization_id: str
    shipment_id: str
    canonical_data: Optional[Dict[str, Any]] = None
    provenance_trail: Dict[str, Any] = Field(default_factory=dict)
    relevant_chunks: List[ContextChunk] = Field(default_factory=list)
    attached_documents: List[Dict[str, Any]] = Field(default_factory=list)
    active_conflicts: List[Dict[str, Any]] = Field(default_factory=list)
    timeline_events: List[Dict[str, Any]] = Field(default_factory=list)
