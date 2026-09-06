"""Disputes API — Dispute creation, approval gateway, and recovery ledger.

Phase 4 non-negotiable safety rules enforced here:
1. Financial amounts come from AuditFindingRecord — never from request body
2. Recipient email only from CarrierContact DB records — never from request body
3. Approval gateway: AUTO_SEND only under threshold; otherwise human approval required
4. Recovery ledger: approved_recovery only settable with proof document
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.agent.dispute.service import run_dispute_agent
from packages.domain.models import CarrierDispute, DisputeRecovery
from packages.llm.gateway import LLMGateway
from packages.storage.db import get_db
from packages.storage.repositories.disputes import DisputeRepository
from packages.storage.repositories.recovery import RecoveryIntegrityError, RecoveryRepository

router = APIRouter(prefix="/disputes", tags=["Dispute Agent"])


# ── Request/Response Models ──────────────────────────────────────────────────

class CreateDisputeRequest(BaseModel):
    organization_id: str = Field(..., description="Organization UUID")
    invoice_id: str = Field(..., description="CarrierInvoice UUID to dispute")
    finding_ids: list[str] = Field(
        ...,
        min_length=1,
        description="AuditFindingRecord UUIDs. Financial amounts flow from these — never from this payload.",
    )
    triggered_by: str = Field(default="api", description="Actor triggering the dispute")
    idempotency_key: Optional[str] = Field(default=None)


class ApproveDisputeRequest(BaseModel):
    reviewed_by: str = Field(..., description="User ID of the approver")
    review_comment: Optional[str] = Field(default=None)


class RejectDisputeRequest(BaseModel):
    reviewed_by: str = Field(..., description="User ID of the reviewer")
    reason: str = Field(..., description="Rejection reason")


class RecordRecoveryRequest(BaseModel):
    organization_id: str
    carrier_response_type: str = Field(
        ...,
        description="accepted | partially_accepted | rejected | request_for_evidence | corrected_invoice_issued | credit_issued | unclear",
    )
    proof_type: Optional[str] = Field(
        default=None,
        description="credit_memo | corrected_invoice | ap_confirmation | human_broker_approval",
    )
    proof_reference: Optional[str] = Field(default=None, description="Credit memo number, corrected invoice ref, etc.")
    approved_amount: Optional[float] = Field(
        default=None,
        description="Verified recovery amount. Only set when proof_type is provided. NEVER set by LLM.",
    )
    verified_by: Optional[str] = Field(default=None, description="User ID verifying the recovery")


# ── Serialization helpers ────────────────────────────────────────────────────

def _serialize_dispute(d: CarrierDispute) -> dict:
    return {
        "id": str(d.id),
        "dispute_number": d.dispute_number,
        "organization_id": str(d.organization_id),
        "invoice_id": str(d.invoice_id),
        "shipment_id": str(d.shipment_id) if d.shipment_id else None,
        "audit_finding_ids": d.audit_finding_ids or [],
        "carrier_name": d.carrier_name,
        "disputed_amount": float(d.disputed_amount) if d.disputed_amount is not None else None,
        "expected_amount": float(d.expected_amount) if d.expected_amount is not None else None,
        "billed_amount": float(d.billed_amount) if d.billed_amount is not None else None,
        "recipient_email": d.recipient_email,
        "recipient_contact_name": d.recipient_contact_name,
        "dispute_letter_subject": d.dispute_letter_subject,
        "dispute_letter_text": d.dispute_letter_text,
        "status": d.status,
        "approval_status": d.approval_status,
        "auto_dispute_threshold_usd": float(d.auto_dispute_threshold_usd) if d.auto_dispute_threshold_usd else 250.00,
        "sent_at": d.sent_at.isoformat() if d.sent_at else None,
        "acknowledged_at": d.acknowledged_at.isoformat() if d.acknowledged_at else None,
        "resolved_at": d.resolved_at.isoformat() if d.resolved_at else None,
        "carrier_response_text": d.carrier_response_text,
        "carrier_response_classification": d.carrier_response_classification,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


def _serialize_recovery(r: DisputeRecovery) -> dict:
    return {
        "id": str(r.id),
        "recovery_number": r.recovery_number,
        "organization_id": str(r.organization_id),
        "dispute_id": str(r.dispute_id),
        "invoice_id": str(r.invoice_id),
        "original_invoice_amount": float(r.original_invoice_amount) if r.original_invoice_amount else None,
        "disputed_amount": float(r.disputed_amount) if r.disputed_amount is not None else None,
        "carrier_response_type": r.carrier_response_type,
        "approved_recovery": float(r.approved_recovery) if r.approved_recovery is not None else None,
        "proof_type": r.proof_type,
        "proof_reference": r.proof_reference,
        "revenue_share_percentage": float(r.revenue_share_percentage) if r.revenue_share_percentage else 0.15,
        "revenue_share_amount": float(r.revenue_share_amount) if r.revenue_share_amount is not None else None,
        "verified_at": r.verified_at.isoformat() if r.verified_at else None,
        "verified_by": r.verified_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


# ── Dispute Endpoints ────────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED, summary="Trigger the Dispute Agent for an invoice")
def create_dispute(
    body: CreateDisputeRequest,
    db: Session = Depends(get_db),
):
    """Run the LangGraph Dispute Agent.

    Financial amounts are sourced from AuditFindingRecord.discrepancy_amount — never from this payload.
    Recipient email is sourced from CarrierContact DB — never invented or guessed.
    """
    try:
        llm = LLMGateway()
        result = run_dispute_agent(
            organization_id=body.organization_id,
            invoice_id=body.invoice_id,
            finding_ids=body.finding_ids,
            db=db,
            llm=llm,
            triggered_by=body.triggered_by,
            idempotency_key=body.idempotency_key,
        )
        return {
            "run_id": result.run_id,
            "dispute_id": result.dispute_id,
            "dispute_number": result.dispute_number,
            "status": result.status,
            "approval_status": result.approval_status,
            "recipient_email": result.recipient_email,
            "disputed_amount": result.disputed_amount,
            "dispute_letter_subject": result.dispute_letter_subject,
            "dispute_letter_preview": result.dispute_letter_preview,
            "needs_human": result.needs_human,
            "error": result.error,
            "trajectory": result.trajectory,
            "cost_estimate": result.cost_estimate,
        }
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("", summary="List disputes for an organization")
def list_disputes(
    organization_id: str,
    status_filter: Optional[str] = None,
    invoice_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    repo = DisputeRepository(db)
    org_id = uuid.UUID(organization_id)
    if invoice_id:
        disputes = repo.get_disputes_for_invoice(org_id, uuid.UUID(invoice_id))
    else:
        disputes = repo.list_disputes(org_id, status_filter)
    return {
        "disputes": [_serialize_dispute(d) for d in disputes],
        "count": len(disputes),
    }


@router.get("/{dispute_id}", summary="Get dispute detail")
def get_dispute(
    dispute_id: str,
    organization_id: str,
    db: Session = Depends(get_db),
):
    repo = DisputeRepository(db)
    dispute = repo.get_dispute(uuid.UUID(organization_id), uuid.UUID(dispute_id))
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")
    return {"dispute": _serialize_dispute(dispute)}


@router.post("/{dispute_id}/approve", summary="Human approves a pending dispute — transitions to sent")
def approve_dispute(
    dispute_id: str,
    organization_id: str,
    body: ApproveDisputeRequest,
    db: Session = Depends(get_db),
):
    """Human approval gateway — approves a 'pending_approval' dispute and marks it as sent."""
    repo = DisputeRepository(db)
    org_id = uuid.UUID(organization_id)
    dispute = repo.get_dispute(org_id, uuid.UUID(dispute_id))
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")
    if dispute.status not in ("pending_approval", "draft"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot approve dispute in status '{dispute.status}'. Must be 'pending_approval' or 'draft'.",
        )
    if not dispute.recipient_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot approve: recipient_email is not set. Register a CarrierContact first.",
        )
    repo.update_approval_status(dispute.id, "human_approved")
    repo.mark_dispute_sent(dispute.id)
    # Create recovery entry if not exists
    recovery_repo = RecoveryRepository(db)
    existing_recovery = recovery_repo.get_recovery_for_dispute(org_id, dispute.id)
    if not existing_recovery:
        from apps.agent.dispute.policies import generate_recovery_number
        recovery_repo.create_recovery_entry(
            organization_id=org_id,
            recovery_number=generate_recovery_number(dispute.dispute_number),
            dispute_id=dispute.id,
            invoice_id=dispute.invoice_id,
            disputed_amount=float(dispute.disputed_amount),
        )
    db.refresh(dispute)
    return {
        "dispute": _serialize_dispute(dispute),
        "message": f"Dispute approved and sent by {body.reviewed_by}",
    }


@router.post("/{dispute_id}/reject", summary="Human rejects a pending dispute")
def reject_dispute(
    dispute_id: str,
    organization_id: str,
    body: RejectDisputeRequest,
    db: Session = Depends(get_db),
):
    repo = DisputeRepository(db)
    org_id = uuid.UUID(organization_id)
    dispute = repo.get_dispute(org_id, uuid.UUID(dispute_id))
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")
    repo.update_dispute_status(dispute.id, "cancelled")
    repo.update_approval_status(dispute.id, "human_rejected")
    db.refresh(dispute)
    return {
        "dispute": _serialize_dispute(dispute),
        "message": f"Dispute rejected by {body.reviewed_by}: {body.reason}",
    }


@router.post("/{dispute_id}/recovery", summary="Record carrier response and proof of recovery")
def record_recovery(
    dispute_id: str,
    body: RecordRecoveryRequest,
    db: Session = Depends(get_db),
):
    """Record carrier response and optionally verify recovery with proof.
    approved_recovery can ONLY be set when proof_type is provided.
    Revenue share (15%) is computed deterministically — never by LLM.
    """
    org_id = uuid.UUID(body.organization_id)
    dispute_repo = DisputeRepository(db)
    recovery_repo = RecoveryRepository(db)

    dispute = dispute_repo.get_dispute(org_id, uuid.UUID(dispute_id))
    if not dispute:
        raise HTTPException(status_code=404, detail="Dispute not found")

    # Record carrier response on the dispute
    if body.carrier_response_type:
        dispute_repo.record_carrier_response(
            dispute.id,
            response_text=f"Carrier response: {body.carrier_response_type}",
            response_classification=body.carrier_response_type,
        )

    # Get or create recovery entry
    recovery = recovery_repo.get_recovery_for_dispute(org_id, dispute.id)
    if not recovery:
        from apps.agent.dispute.policies import generate_recovery_number
        recovery = recovery_repo.create_recovery_entry(
            organization_id=org_id,
            recovery_number=generate_recovery_number(dispute.dispute_number),
            dispute_id=dispute.id,
            invoice_id=dispute.invoice_id,
            disputed_amount=float(dispute.disputed_amount),
        )

    # Record proof if provided
    if body.proof_type:
        recovery_repo.record_proof(
            organization_id=org_id,
            recovery_id=recovery.id,
            proof_type=body.proof_type,
            carrier_response_type=body.carrier_response_type,
            proof_reference=body.proof_reference,
        )

    # Verify and compute revenue share if amount provided
    if body.approved_amount is not None and body.proof_type and body.verified_by:
        try:
            recovery = recovery_repo.verify_and_mark_recovered(
                organization_id=org_id,
                recovery_id=recovery.id,
                approved_amount=body.approved_amount,
                verified_by=body.verified_by,
            )
        except RecoveryIntegrityError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    else:
        db.refresh(recovery)

    return {
        "recovery": _serialize_recovery(recovery),
        "message": "Recovery recorded",
    }


# ── Recovery Ledger Endpoints ────────────────────────────────────────────────

@router.get("/recovery-ledger/all", summary="List all recovery ledger entries (admin view)")
def list_recovery_ledger(
    organization_id: str,
    verified_only: bool = False,
    db: Session = Depends(get_db),
):
    """Platform success fee (15%) can ONLY be billed against verified entries in this ledger."""
    repo = RecoveryRepository(db)
    org_id = uuid.UUID(organization_id)
    if verified_only:
        entries = repo.list_verified_recoveries(org_id)
    else:
        entries = repo.list_unverified_recoveries(org_id) + repo.list_verified_recoveries(org_id)
    total_verified = sum(
        float(r.approved_recovery) for r in entries if r.approved_recovery is not None
    )
    total_revenue_share = sum(
        float(r.revenue_share_amount) for r in entries if r.revenue_share_amount is not None
    )
    return {
        "entries": [_serialize_recovery(r) for r in entries],
        "count": len(entries),
        "total_verified_recovery_usd": round(total_verified, 2),
        "total_platform_revenue_share_usd": round(total_revenue_share, 2),
    }
