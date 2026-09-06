"""Phase 3.5 — Deterministic Policy Checks and Quality Controls for Audit Agent.

Strictly enforces:
- Quality controls on all audit findings (expected, billed, diff, reason, source_docs, rule_id, confidence, evidence_refs).
- Deterministic routing to next workflow: payment_scheduled, dispute_review, hold_for_documents, or needs_human.
- Prohibition against confirming findings without sufficient evidence.
"""

from typing import Any, Dict, List, Tuple


def validate_finding_quality(finding: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Phase 3.5 Quality Control: verify all 8 required fields are present and valid."""
    errors = []
    required_fields = [
        "rule_id",
        "severity",
        "expected_value",
        "billed_value",
        "difference",
        "reason",
        "confidence",
        "source_documents",
    ]

    for f in required_fields:
        if f not in finding:
            errors.append(f"Missing required field: {f}")

    # Confidence must be explicitly calibrated (deterministic findings are 1.0)
    conf = finding.get("confidence")
    if conf is None or not (0.0 <= conf <= 1.0):
        errors.append("Confidence must be a float between 0.0 and 1.0")

    # Source documents must not be empty
    docs = finding.get("source_documents")
    if not docs or not isinstance(docs, list):
        errors.append("Source documents must be a non-empty list of document types")

    # Reason must not be empty or generic
    reason = finding.get("reason", "")
    if not reason or len(reason.strip()) < 5:
        errors.append("Finding reason must provide explicit factual details")

    return (len(errors) == 0, errors)


def classify_audit_outcome(
    findings: List[Dict[str, Any]],
    is_clean: bool,
    has_shipment: bool,
    has_contract: bool,
) -> str:
    """Classify the high-level outcome of deterministic rule evaluation."""
    if not has_shipment or not has_contract:
        return "ambiguous"

    if is_clean and len(findings) == 0:
        return "clean_pass"

    # Check for duplicate
    if any(f.get("rule_id") == "RULE_01_DUPLICATE_INVOICE" for f in findings):
        return "duplicate_detected"

    # Check if only missing evidence / unverified documents
    has_substantive_overcharge = any(
        f.get("difference", 0.0) > 0.01
        and f.get("rule_id") not in ("RULE_04_UNSUPPORTED_ACCESSORIAL", "RULE_09_QUOTE_VERSUS_INVOICE_MISMATCH")
        for f in findings
    )
    has_unsupported_acc = any(f.get("rule_id") == "RULE_04_UNSUPPORTED_ACCESSORIAL" for f in findings)

    if has_unsupported_acc and not has_substantive_overcharge:
        return "missing_evidence"

    return "discrepancies_flagged"


def determine_next_workflow(
    classification: str,
    total_discrepancy: float,
    findings: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Map classification to deterministic next workflow route and approval state."""
    if classification == "clean_pass":
        return {
            "next_workflow": "payment_scheduled",
            "approval_state": "auto_approved",
            "terminal_outcome": "completed",
            "decision": "All 10 deterministic audit checks passed. Clean invoice auto-approved for payment scheduling.",
            "confidence": 1.0,
        }

    if classification == "duplicate_detected":
        return {
            "next_workflow": "rejected_duplicate",
            "approval_state": "rejected",
            "terminal_outcome": "completed",
            "decision": "Duplicate invoice submission detected. Payment halted to prevent double payment.",
            "confidence": 1.0,
        }

    if classification == "missing_evidence":
        return {
            "next_workflow": "hold_for_documents",
            "approval_state": "held",
            "terminal_outcome": "needs_human",
            "decision": "Hold for supporting receipts or certified timestamps before processing accessorial charges.",
            "confidence": 1.0,
        }

    if classification == "discrepancies_flagged":
        return {
            "next_workflow": "dispute_review",
            "approval_state": "dispute_flagged",
            "terminal_outcome": "completed",
            "decision": f"Discrepancies flagged totaling ${total_discrepancy:.2f}. Routed to carrier dispute review.",
            "confidence": 1.0,
        }

    # Default fallback: ambiguous
    return {
        "next_workflow": "needs_human",
        "approval_state": "needs_human",
        "terminal_outcome": "needs_human",
        "decision": "Unable to resolve matching shipment or rate contract. Escalating to human operator.",
        "confidence": 0.5,
    }
