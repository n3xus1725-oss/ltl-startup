"""Repository for DisputeRecovery — immutable verified recovery ledger."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from packages.domain.models import DisputeRecovery


class RecoveryIntegrityError(Exception):
    """Raised when a recovery integrity rule is violated."""


class RecoveryRepository:
    """Repository for the immutable recovery ledger.
    STRICT ENFORCEMENT: approved_recovery can ONLY be set after proof is recorded.
    Platform 15% success fee can ONLY be computed against verified entries."""

    def __init__(self, db: Session):
        self.db = db

    def create_recovery_entry(
        self,
        organization_id: uuid.UUID,
        recovery_number: str,
        dispute_id: uuid.UUID,
        invoice_id: uuid.UUID,
        disputed_amount: float,
        original_invoice_amount: Optional[float] = None,
    ) -> DisputeRecovery:
        """Create an initial recovery ledger entry. approved_recovery is NULL until verified."""
        recovery = DisputeRecovery(
            organization_id=organization_id,
            recovery_number=recovery_number,
            dispute_id=dispute_id,
            invoice_id=invoice_id,
            disputed_amount=disputed_amount,
            original_invoice_amount=original_invoice_amount,
            approved_recovery=None,  # MUST remain NULL until proof recorded
            verified_at=None,
            revenue_share_amount=None,
        )
        self.db.add(recovery)
        self.db.commit()
        self.db.refresh(recovery)
        return recovery

    def record_proof(
        self,
        organization_id: uuid.UUID,
        recovery_id: uuid.UUID,
        proof_type: str,
        carrier_response_type: str,
        proof_document_id: Optional[uuid.UUID] = None,
        proof_reference: Optional[str] = None,
    ) -> DisputeRecovery:
        """Record proof of carrier response. Proof must be recorded before recovery can be verified."""
        recovery = (
            self.db.query(DisputeRecovery)
            .filter(
                DisputeRecovery.id == recovery_id,
                DisputeRecovery.organization_id == organization_id,
            )
            .first()
        )
        if not recovery:
            raise RecoveryIntegrityError(f"Recovery {recovery_id} not found")
        recovery.proof_type = proof_type
        recovery.carrier_response_type = carrier_response_type
        recovery.proof_document_id = proof_document_id
        recovery.proof_reference = proof_reference
        self.db.commit()
        self.db.refresh(recovery)
        return recovery

    def verify_and_mark_recovered(
        self,
        organization_id: uuid.UUID,
        recovery_id: uuid.UUID,
        approved_amount: float,
        verified_by: str,
        customer_confirmation_user_id: Optional[str] = None,
        customer_confirmation_at: Optional[datetime] = None,
    ) -> DisputeRecovery:
        """GUARD: approved_recovery can ONLY be set if proof_type is already recorded.
        Computes revenue_share_amount = approved_amount * revenue_share_percentage (deterministically).
        The LLM never touches this function."""
        recovery = (
            self.db.query(DisputeRecovery)
            .filter(
                DisputeRecovery.id == recovery_id,
                DisputeRecovery.organization_id == organization_id,
            )
            .first()
        )
        if not recovery:
            raise RecoveryIntegrityError(f"Recovery {recovery_id} not found")
        if not recovery.proof_type:
            raise RecoveryIntegrityError(
                f"Cannot verify recovery {recovery_id}: no proof_type recorded. "
                "Record proof (credit memo, corrected invoice, or human approval) first."
            )
        if recovery.approved_recovery is not None:
            raise RecoveryIntegrityError(
                f"Recovery {recovery_id} already verified with amount {recovery.approved_recovery}. "
                "Recovery entries are immutable once verified."
            )
        # Deterministic financial computation — NOT performed by LLM
        revenue_share = float(recovery.revenue_share_percentage) * approved_amount
        recovery.approved_recovery = approved_amount
        recovery.revenue_share_amount = round(revenue_share, 2)
        recovery.verified_at = datetime.now(timezone.utc)
        recovery.verified_by = verified_by
        recovery.customer_confirmation_user_id = customer_confirmation_user_id
        recovery.customer_confirmation_at = customer_confirmation_at
        self.db.commit()
        self.db.refresh(recovery)
        return recovery

    def get_recovery_for_dispute(
        self, organization_id: uuid.UUID, dispute_id: uuid.UUID
    ) -> Optional[DisputeRecovery]:
        return (
            self.db.query(DisputeRecovery)
            .filter(
                DisputeRecovery.dispute_id == dispute_id,
                DisputeRecovery.organization_id == organization_id,
            )
            .first()
        )

    def list_verified_recoveries(self, organization_id: uuid.UUID) -> list[DisputeRecovery]:
        """List all entries where approved_recovery has been verified.
        Only these entries are eligible for platform success fee billing."""
        return (
            self.db.query(DisputeRecovery)
            .filter(
                DisputeRecovery.organization_id == organization_id,
                DisputeRecovery.verified_at.isnot(None),
            )
            .order_by(DisputeRecovery.verified_at.desc())
            .all()
        )

    def list_unverified_recoveries(self, organization_id: uuid.UUID) -> list[DisputeRecovery]:
        """List all entries where proof has been submitted but not yet verified."""
        return (
            self.db.query(DisputeRecovery)
            .filter(
                DisputeRecovery.organization_id == organization_id,
                DisputeRecovery.verified_at.is_(None),
            )
            .all()
        )
