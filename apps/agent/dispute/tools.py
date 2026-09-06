"""Typed tool definitions for the Dispute Agent.

Tool execution pattern:
- LLM selects tool by name and provides parameters
- Application code validates parameters, enforces permissions, and executes
- LLM never directly mutates the database or sends emails

SAFETY: SendDisputeEmailTool raises if approval_decision != 'auto_approved'.
The email is simulated (logged) — no real SMTP in MVP.
"""

import logging
import uuid

from sqlalchemy.orm import Session

from packages.domain.models import AuditFindingRecord
from packages.storage.repositories.carrier_contacts import CarrierContactRepository
from packages.storage.repositories.disputes import DisputeRepository
from packages.storage.repositories.recovery import RecoveryRepository
from packages.tools.registry import BaseTool, ToolContext, ToolRegistry

logger = logging.getLogger(__name__)


class GetAuditFindingsTool(BaseTool):
    name = "get_audit_findings"
    description = "Fetch AuditFindingRecord objects by ID. Returns finding data including discrepancy_amount which MUST be used as-is for disputed_amount."
    required_permission = "audit:read"

    def execute(self, context: ToolContext, db: Session, params: dict) -> dict:
        finding_ids = params.get("finding_ids", [])
        org_id = uuid.UUID(context.organization_id)
        findings = []
        total_discrepancy = 0.0
        for fid in finding_ids:
            record = (
                db.query(AuditFindingRecord)
                .filter(
                    AuditFindingRecord.id == uuid.UUID(fid),
                    AuditFindingRecord.organization_id == org_id,
                )
                .first()
            )
            if record:
                findings.append({
                    "id": str(record.id),
                    "rule_id": record.rule_id,
                    "rule_name": record.rule_name,
                    "severity": record.severity,
                    "discrepancy_amount": record.discrepancy_amount,  # AUTHORITATIVE — use as-is
                    "reason": record.reason,
                    "confidence": record.confidence,
                    "recommended_action": record.recommended_action,
                    "evidence": record.evidence,
                    "invoice_id": str(record.invoice_id),
                    "shipment_id": str(record.shipment_id) if record.shipment_id else None,
                })
                total_discrepancy += record.discrepancy_amount or 0.0
        return {
            "findings": findings,
            "count": len(findings),
            "total_discrepancy_amount": round(total_discrepancy, 2),  # Sum computed by code, not LLM
        }


class ResolveCarrierContactTool(BaseTool):
    name = "resolve_carrier_contact"
    description = "Look up the verified billing email for a carrier from the CarrierContact DB. Returns None if not found — caller MUST escalate to human. NEVER synthesize an email."
    required_permission = "carrier_contacts:read"

    def execute(self, context: ToolContext, db: Session, params: dict) -> dict:
        carrier_name = params.get("carrier_name", "")
        org_id = uuid.UUID(context.organization_id)
        repo = CarrierContactRepository(db)
        contact = repo.get_verified_contact(org_id, carrier_name)
        if contact is None:
            return {
                "found": False,
                "billing_email": None,
                "contact_name": None,
                "escalate_to_human": True,  # MANDATORY when no contact found
                "reason": f"No verified CarrierContact on file for '{carrier_name}'. Human must supply recipient.",
            }
        return {
            "found": True,
            "billing_email": contact.billing_email,
            "contact_name": contact.contact_name,
            "escalate_to_human": False,
        }


class CreateDisputeDraftTool(BaseTool):
    name = "create_dispute_draft"
    description = "Create a CarrierDispute record in 'draft' status. disputed_amount MUST be pre-validated against AuditFindingRecord.discrepancy_amount before calling."
    required_permission = "disputes:write"

    def execute(self, context: ToolContext, db: Session, params: dict) -> dict:
        org_id = uuid.UUID(context.organization_id)
        repo = DisputeRepository(db)
        dispute = repo.create_dispute(
            organization_id=org_id,
            dispute_number=params["dispute_number"],
            invoice_id=uuid.UUID(params["invoice_id"]),
            shipment_id=uuid.UUID(params["shipment_id"]) if params.get("shipment_id") else None,
            audit_finding_ids=params["finding_ids"],
            carrier_name=params["carrier_name"],
            disputed_amount=params["disputed_amount"],  # Pre-validated against findings
            expected_amount=params.get("expected_amount"),
            billed_amount=params.get("billed_amount"),
            auto_dispute_threshold_usd=params.get("auto_dispute_threshold_usd", 250.00),
            idempotency_key=params.get("idempotency_key"),
        )
        if params.get("recipient_email"):
            repo.set_recipient(
                dispute.id,
                params["recipient_email"],
                params.get("recipient_contact_name"),
            )
            db.refresh(dispute)
        if params.get("dispute_letter_subject") and params.get("dispute_letter_text"):
            repo.set_dispute_letter(
                dispute.id,
                params["dispute_letter_subject"],
                params["dispute_letter_text"],
            )
            db.refresh(dispute)
        return {
            "dispute_id": str(dispute.id),
            "dispute_number": dispute.dispute_number,
            "status": dispute.status,
        }


class SendDisputeEmailTool(BaseTool):
    name = "send_dispute_email"
    description = "Send the dispute email to the verified carrier contact. ONLY callable if approval_decision is auto_approved. Raises PermissionError otherwise."
    required_permission = "disputes:send"

    def execute(self, context: ToolContext, db: Session, params: dict) -> dict:
        approval_decision = params.get("approval_decision", "")
        if approval_decision != "auto_approved":
            raise PermissionError(
                f"SendDisputeEmailTool: Cannot send dispute. approval_decision is '{approval_decision}', "
                "not 'auto_approved'. Human approval required before sending."
            )
        dispute_id = uuid.UUID(params["dispute_id"])
        org_id = uuid.UUID(context.organization_id)
        repo = DisputeRepository(db)
        dispute = repo.get_dispute(org_id, dispute_id)
        if not dispute:
            raise ValueError(f"Dispute {dispute_id} not found")
        if not dispute.recipient_email:
            raise ValueError("Cannot send dispute: recipient_email is not set. Contact resolution required.")
        # MVP: Simulate email send (log only — no real SMTP)
        logger.info(
            "[DISPUTE EMAIL SIMULATED] To: %s | Subject: %s | Dispute: %s | Amount: $%.2f",
            dispute.recipient_email,
            dispute.dispute_letter_subject,
            dispute.dispute_number,
            float(dispute.disputed_amount),
        )
        repo.mark_dispute_sent(dispute_id)
        return {
            "sent": True,
            "dispute_id": str(dispute_id),
            "recipient_email": dispute.recipient_email,
            "dispute_number": dispute.dispute_number,
            "simulated": True,  # No real SMTP in MVP
        }


class CreateRecoveryEntryTool(BaseTool):
    name = "create_recovery_entry"
    description = "Create an initial DisputeRecovery ledger entry. approved_recovery is NULL until verified proof is recorded."
    required_permission = "recovery:write"

    def execute(self, context: ToolContext, db: Session, params: dict) -> dict:
        org_id = uuid.UUID(context.organization_id)
        repo = RecoveryRepository(db)
        recovery = repo.create_recovery_entry(
            organization_id=org_id,
            recovery_number=params["recovery_number"],
            dispute_id=uuid.UUID(params["dispute_id"]),
            invoice_id=uuid.UUID(params["invoice_id"]),
            disputed_amount=params["disputed_amount"],
            original_invoice_amount=params.get("original_invoice_amount"),
        )
        return {
            "recovery_id": str(recovery.id),
            "recovery_number": recovery.recovery_number,
            "approved_recovery": None,  # Always NULL initially
            "verified_at": None,
        }


class EscalateToHumanTool(BaseTool):
    name = "escalate_to_human"
    description = "Escalate the dispute to a human operator. Used when no verified carrier contact exists or approval threshold requires human review."
    required_permission = "tasks:write"

    def execute(self, context: ToolContext, db: Session, params: dict) -> dict:
        # In MVP: log the escalation. Full implementation creates TaskRecord + Approval.
        reason = params.get("reason", "Human review required")
        dispute_id = params.get("dispute_id", "N/A")
        logger.warning(
            "[HUMAN ESCALATION] Dispute %s requires human attention: %s",
            dispute_id,
            reason,
        )
        return {
            "escalated": True,
            "reason": reason,
            "dispute_id": dispute_id,
            "action_required": "human_review",
        }


def register_dispute_tools(registry: ToolRegistry, db: Session) -> None:
    """Register all dispute agent tools with the shared registry."""
    registry.register(GetAuditFindingsTool())
    registry.register(ResolveCarrierContactTool())
    registry.register(CreateDisputeDraftTool())
    registry.register(SendDisputeEmailTool())
    registry.register(CreateRecoveryEntryTool())
    registry.register(EscalateToHumanTool())
