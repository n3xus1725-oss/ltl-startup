"""Repository for CarrierDispute records."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from packages.domain.models import CarrierDispute


class DisputeRepository:
    """CRUD and state-management repository for CarrierDispute."""

    def __init__(self, db: Session):
        self.db = db

    def create_dispute(
        self,
        organization_id: uuid.UUID,
        dispute_number: str,
        invoice_id: uuid.UUID,
        shipment_id: Optional[uuid.UUID],
        audit_finding_ids: list,
        carrier_name: str,
        disputed_amount: float,
        expected_amount: Optional[float],
        billed_amount: Optional[float],
        auto_dispute_threshold_usd: float = 250.00,
        idempotency_key: Optional[str] = None,
        agent_run_id: Optional[uuid.UUID] = None,
    ) -> CarrierDispute:
        """Create a new dispute record. disputed_amount MUST be pre-validated to equal
        the sum of AuditFindingRecord.discrepancy_amount values before calling this."""
        dispute = CarrierDispute(
            organization_id=organization_id,
            dispute_number=dispute_number,
            invoice_id=invoice_id,
            shipment_id=shipment_id,
            audit_finding_ids=audit_finding_ids,
            carrier_name=carrier_name,
            disputed_amount=disputed_amount,
            expected_amount=expected_amount,
            billed_amount=billed_amount,
            auto_dispute_threshold_usd=auto_dispute_threshold_usd,
            idempotency_key=idempotency_key,
            agent_run_id=agent_run_id,
            status="draft",
            approval_status="pending",
        )
        self.db.add(dispute)
        self.db.commit()
        self.db.refresh(dispute)
        return dispute

    def get_dispute(
        self, organization_id: uuid.UUID, dispute_id: uuid.UUID
    ) -> Optional[CarrierDispute]:
        return (
            self.db.query(CarrierDispute)
            .filter(
                CarrierDispute.id == dispute_id,
                CarrierDispute.organization_id == organization_id,
            )
            .first()
        )

    def get_by_idempotency_key(
        self, organization_id: uuid.UUID, idempotency_key: str
    ) -> Optional[CarrierDispute]:
        return (
            self.db.query(CarrierDispute)
            .filter(
                CarrierDispute.organization_id == organization_id,
                CarrierDispute.idempotency_key == idempotency_key,
            )
            .first()
        )

    def get_disputes_for_invoice(
        self, organization_id: uuid.UUID, invoice_id: uuid.UUID
    ) -> list[CarrierDispute]:
        return (
            self.db.query(CarrierDispute)
            .filter(
                CarrierDispute.organization_id == organization_id,
                CarrierDispute.invoice_id == invoice_id,
            )
            .order_by(CarrierDispute.created_at.desc())
            .all()
        )

    def list_disputes(
        self,
        organization_id: uuid.UUID,
        status_filter: Optional[str] = None,
    ) -> list[CarrierDispute]:
        q = self.db.query(CarrierDispute).filter(
            CarrierDispute.organization_id == organization_id
        )
        if status_filter:
            q = q.filter(CarrierDispute.status == status_filter)
        return q.order_by(CarrierDispute.created_at.desc()).all()

    def update_dispute_status(
        self, dispute_id: uuid.UUID, status: str
    ) -> Optional[CarrierDispute]:
        dispute = self.db.query(CarrierDispute).filter(CarrierDispute.id == dispute_id).first()
        if not dispute:
            return None
        dispute.status = status
        self.db.commit()
        self.db.refresh(dispute)
        return dispute

    def update_approval_status(
        self, dispute_id: uuid.UUID, approval_status: str
    ) -> Optional[CarrierDispute]:
        dispute = self.db.query(CarrierDispute).filter(CarrierDispute.id == dispute_id).first()
        if not dispute:
            return None
        dispute.approval_status = approval_status
        self.db.commit()
        self.db.refresh(dispute)
        return dispute

    def set_recipient(
        self,
        dispute_id: uuid.UUID,
        recipient_email: str,
        recipient_contact_name: Optional[str] = None,
    ) -> Optional[CarrierDispute]:
        """Set the verified recipient email. Only called after CarrierContactRepository lookup."""
        dispute = self.db.query(CarrierDispute).filter(CarrierDispute.id == dispute_id).first()
        if not dispute:
            return None
        dispute.recipient_email = recipient_email
        dispute.recipient_contact_name = recipient_contact_name
        self.db.commit()
        self.db.refresh(dispute)
        return dispute

    def set_dispute_letter(
        self,
        dispute_id: uuid.UUID,
        subject: str,
        letter_text: str,
    ) -> Optional[CarrierDispute]:
        """Persist the LLM-drafted dispute letter (financial amounts injected by code)."""
        dispute = self.db.query(CarrierDispute).filter(CarrierDispute.id == dispute_id).first()
        if not dispute:
            return None
        dispute.dispute_letter_subject = subject
        dispute.dispute_letter_text = letter_text
        self.db.commit()
        self.db.refresh(dispute)
        return dispute

    def mark_dispute_sent(
        self, dispute_id: uuid.UUID
    ) -> Optional[CarrierDispute]:
        dispute = self.db.query(CarrierDispute).filter(CarrierDispute.id == dispute_id).first()
        if not dispute:
            return None
        dispute.status = "sent"
        dispute.sent_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(dispute)
        return dispute

    def record_carrier_response(
        self,
        dispute_id: uuid.UUID,
        response_text: str,
        response_classification: str,
    ) -> Optional[CarrierDispute]:
        dispute = self.db.query(CarrierDispute).filter(CarrierDispute.id == dispute_id).first()
        if not dispute:
            return None
        dispute.carrier_response_text = response_text
        dispute.carrier_response_classification = response_classification
        dispute.acknowledged_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(dispute)
        return dispute
