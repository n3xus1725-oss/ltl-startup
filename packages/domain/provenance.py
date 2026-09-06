"""Phase 2.1 — Field-Level Provenance Engine.

Every attribute in the canonical shipment retains complete provenance:
- source (e.g. rate_confirmation, bol, pod, invoice, scale_ticket, carrier_edi, carrier_email, manual_entry)
- source_id (UUID or string ID of message/document/event)
- timestamp (assertion datetime in UTC)
- confidence (float between 0.0 and 1.0)
- authority (authority tier score integer 0 - 100)
- writer (agent run ID, user ID, integration name)
- evidence (supporting context: raw text, bounding box, page number, checksum)
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def utcnow():
    return datetime.now(timezone.utc)


class FieldProvenanceRecord(BaseModel):
    """Immutable record of an individual field assertion."""
    field: str
    value: Any
    source: str  # rate_confirmation, bol, pod, invoice, scale_ticket, carrier_edi, carrier_email, manual_entry
    source_id: str
    timestamp: datetime = Field(default_factory=utcnow)
    confidence: float = 1.0
    authority: int = 50  # 0 to 100
    writer: str = "system"
    evidence: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "value": self.value,
            "source": self.source,
            "source_id": self.source_id,
            "timestamp": self.timestamp.isoformat() if isinstance(self.timestamp, datetime) else str(self.timestamp),
            "confidence": float(self.confidence),
            "authority": int(self.authority),
            "writer": self.writer,
            "evidence": self.evidence or {},
        }


class ProvenanceLedger(BaseModel):
    """Maintains active winning assertions and complete audit history per field."""
    active_assertions: Dict[str, FieldProvenanceRecord] = Field(default_factory=dict)
    assertion_history: Dict[str, List[FieldProvenanceRecord]] = Field(default_factory=dict)

    def record_assertion(self, record: FieldProvenanceRecord, is_active: bool = True) -> None:
        """Append an assertion to history, and optionally update active winning assertion."""
        field = record.field
        if field not in self.assertion_history:
            self.assertion_history[field] = []
        self.assertion_history[field].append(record)

        if is_active:
            self.active_assertions[field] = record

    def get_active(self, field: str) -> Optional[FieldProvenanceRecord]:
        return self.active_assertions.get(field)

    def get_history(self, field: str) -> List[FieldProvenanceRecord]:
        return self.assertion_history.get(field, [])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "active_assertions": {
                k: v.to_dict() for k, v in self.active_assertions.items()
            },
            "assertion_history": {
                k: [r.to_dict() for r in records] for k, records in self.assertion_history.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "ProvenanceLedger":
        if not data:
            return cls()
        active = {}
        for k, v in data.get("active_assertions", {}).items():
            if isinstance(v, dict):
                active[k] = FieldProvenanceRecord(**v)
        history = {}
        for k, records in data.get("assertion_history", {}).items():
            if isinstance(records, list):
                history[k] = [FieldProvenanceRecord(**r) for r in records if isinstance(r, dict)]
        return cls(active_assertions=active, assertion_history=history)
