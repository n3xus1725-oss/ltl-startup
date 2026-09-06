"""Phase 3.3 — Billing Audit Finding Repository."""

import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.domain.models import AuditFindingRecord
from packages.storage.repositories.base import BaseRepository


class AuditFindingRepository(BaseRepository[AuditFindingRecord]):
    """Repository for persisting and querying billing audit discrepancy findings."""

    def __init__(self, db: Session):
        super().__init__(AuditFindingRecord, db)

    def create_finding(
        self,
        organization_id: uuid.UUID,
        invoice_id: uuid.UUID,
        rule_id: str,
        rule_name: str,
        severity: str,
        reason: str,
        discrepancy_amount: Optional[float] = None,
        expected_value: Optional[Any] = None,
        billed_value: Optional[Any] = None,
        shipment_id: Optional[uuid.UUID] = None,
        confidence: float = 1.0,
        evidence: Optional[Dict[str, Any]] = None,
        recommended_action: Optional[str] = None,
        status: str = "open",
        difference: Optional[float] = None,
        source_documents: Optional[List[str]] = None,
        evidence_references: Optional[List[Dict[str, Any]]] = None,
        **kwargs,
    ) -> AuditFindingRecord:
        """Create and persist an audit discrepancy finding."""
        final_diff = discrepancy_amount if discrepancy_amount is not None else (difference or 0.0)
        ev = evidence or {}
        if source_documents:
            ev["source_documents"] = source_documents
        if evidence_references:
            ev["evidence_references"] = evidence_references

        finding = AuditFindingRecord(
            organization_id=organization_id,
            invoice_id=invoice_id,
            shipment_id=shipment_id,
            rule_id=rule_id,
            rule_name=rule_name,
            severity=severity,
            reason=reason,
            discrepancy_amount=final_diff,
            expected_value=expected_value,
            billed_value=billed_value,
            confidence=confidence,
            evidence=ev,
            recommended_action=recommended_action,
            status=status,
        )
        self.db.add(finding)
        self.db.commit()
        self.db.refresh(finding)
        return finding

    def list_findings_for_invoice(
        self, organization_id: uuid.UUID, invoice_id: uuid.UUID
    ) -> List[AuditFindingRecord]:
        """List all findings for a specific invoice."""
        stmt = (
            select(AuditFindingRecord)
            .where(
                AuditFindingRecord.organization_id == organization_id,
                AuditFindingRecord.invoice_id == invoice_id,
            )
            .order_by(AuditFindingRecord.created_at.asc())
        )
        return list(self.db.execute(stmt).scalars().all())

    def list_findings_for_shipment(
        self, organization_id: uuid.UUID, shipment_id: uuid.UUID
    ) -> List[AuditFindingRecord]:
        """List all audit findings associated with a shipment."""
        stmt = (
            select(AuditFindingRecord)
            .where(
                AuditFindingRecord.organization_id == organization_id,
                AuditFindingRecord.shipment_id == shipment_id,
            )
            .order_by(AuditFindingRecord.created_at.asc())
        )
        return list(self.db.execute(stmt).scalars().all())
