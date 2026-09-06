"""Deterministic policy functions for the Dispute Agent.

CRITICAL: These functions enforce the non-negotiable safety rules:
1. Recipient resolution NEVER synthesizes email addresses — only reads from CarrierContact DB.
2. Financial amounts NEVER modified by or compared against LLM output.
3. Approval gate is purely deterministic — LLM has no role in the approve/reject decision.
"""

import uuid
from enum import Enum
from typing import Optional

from sqlalchemy.orm import Session

from packages.storage.repositories.carrier_contacts import CarrierContactRepository


class ApprovalDecision(str, Enum):
    AUTO_SEND = "auto_approved"
    REQUIRES_HUMAN_APPROVAL = "requires_human_approval"


class FinancialIntegrityError(Exception):
    """Raised when disputed_amount in the agent state does not match source finding data.
    This is a hard stop — the agent CANNOT proceed if financial amounts are inconsistent."""


class RecipientResolutionError(Exception):
    """Raised when no verified carrier contact is found.
    The agent MUST escalate to human — it may never guess or synthesize an email."""


def resolve_recipient(
    organization_id: uuid.UUID,
    carrier_name: str,
    db: Session,
) -> Optional[tuple[str, str]]:
    """Resolve the verified billing email for a carrier.

    Returns (billing_email, contact_name) if a verified CarrierContact exists.
    Returns None if no verified contact found — caller MUST escalate to human.

    SAFETY RULE: This function NEVER synthesizes, guesses, or generates an email address.
    If the DB lookup returns None, the only valid action is human escalation.
    """
    repo = CarrierContactRepository(db)
    contact = repo.get_verified_contact(organization_id, carrier_name)
    if contact is None:
        return None  # Caller must set needs_human=True and escalate
    return (contact.billing_email, contact.contact_name or "Billing Department")


def evaluate_approval_gate(
    disputed_amount: float,
    confidence: float,
    has_signed_documentation: bool,
    auto_dispute_threshold_usd: float = 250.00,
    is_first_time_carrier: bool = False,
) -> ApprovalDecision:
    """Evaluate the two-tier approval policy.

    AUTO_SEND requires ALL of:
    - confidence >= 0.85
    - has_signed_documentation is True
    - disputed_amount <= auto_dispute_threshold_usd
    - is_first_time_carrier is False

    Everything else → REQUIRES_HUMAN_APPROVAL.
    This decision is 100% deterministic — LLM has no role.
    """
    if (
        confidence >= 0.85
        and has_signed_documentation
        and disputed_amount <= auto_dispute_threshold_usd
        and not is_first_time_carrier
    ):
        return ApprovalDecision.AUTO_SEND
    return ApprovalDecision.REQUIRES_HUMAN_APPROVAL


def validate_financial_integrity(
    disputed_amount_in_state: float,
    sum_of_finding_discrepancies: float,
    tolerance: float = 0.01,
) -> None:
    """Assert that the disputed amount in agent state exactly equals the sum of
    AuditFindingRecord.discrepancy_amount values.

    Raises FinancialIntegrityError if there is any discrepancy.
    The LLM is forbidden from modifying financial amounts — this guard detects violations.
    """
    delta = abs(disputed_amount_in_state - sum_of_finding_discrepancies)
    if delta > tolerance:
        raise FinancialIntegrityError(
            f"Financial integrity violation: agent state disputed_amount "
            f"{disputed_amount_in_state:.2f} does not match sum of finding "
            f"discrepancy_amounts {sum_of_finding_discrepancies:.2f}. "
            f"Delta: {delta:.4f}. The LLM must not modify financial amounts."
        )


def determine_has_signed_documentation(findings: list[dict]) -> bool:
    """Determine if signed documentation (BOL, POD) is available in the evidence.
    Based purely on finding evidence metadata — no LLM involvement.
    """
    for finding in findings:
        evidence = finding.get("evidence") or {}
        docs = evidence.get("source_documents", [])
        for doc in docs:
            doc_type = str(doc).upper()
            if any(t in doc_type for t in ["BOL", "BILL_OF_LADING", "POD", "PROOF_OF_DELIVERY", "SIGNED"]):
                return True
        recommended = str(finding.get("recommended_action", "")).lower()
        if "signed" in recommended or "bol" in recommended:
            return True
    return False


def generate_dispute_number(invoice_number: str, sequence: int = 1) -> str:
    """Generate a unique, human-readable dispute number."""
    clean = invoice_number.replace("-", "").replace(" ", "").upper()[:10]
    return f"DSP-{clean}-{sequence:02d}"


def generate_recovery_number(dispute_number: str) -> str:
    """Generate a recovery number linked to the dispute."""
    return dispute_number.replace("DSP-", "REC-")
