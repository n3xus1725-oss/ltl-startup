"""Typed state for the Dispute Agent LangGraph workflow."""

from typing import Any, Optional

from typing_extensions import TypedDict


class DisputeAgentState(TypedDict):
    """Mutable state carried through the Dispute Agent graph.
    Financial amounts (disputed_amount, expected_amount, billed_amount) are
    ALWAYS set from deterministic code reading AuditFindingRecord — never from LLM output.
    """
    # Input
    organization_id: str
    invoice_id: str
    finding_ids: list[str]  # AuditFindingRecord UUIDs to dispute
    triggered_by: str
    run_id: str

    # Resolved findings data (populated by node_load_findings)
    findings: list[dict]  # serialized AuditFindingRecord data
    carrier_name: str
    invoice_number: str

    # Financial amounts — MUST be set from deterministic code, never from LLM
    disputed_amount: float  # sum of finding.discrepancy_amount values
    expected_amount: Optional[float]
    billed_amount: Optional[float]

    # Recipient — only set after CarrierContactRepository lookup (never guessed)
    recipient_email: Optional[str]
    recipient_contact_name: Optional[str]
    recipient_resolved: bool

    # Dispute package — LLM drafts subject/body; financial data injected by code
    dispute_id: Optional[str]
    dispute_number: Optional[str]
    dispute_letter_subject: Optional[str]
    dispute_letter_text: Optional[str]

    # Approval gateway
    approval_decision: str  # "auto_approved" | "requires_human_approval" | "pending"
    has_signed_documentation: bool
    confidence: float

    # Outcome
    dispute_status: str
    recovery_id: Optional[str]
    needs_human: bool
    error: Optional[str]

    # Trajectory — each node appends a step dict
    trajectory: list[dict[str, Any]]
