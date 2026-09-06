"""Phase 1.6 & 1.8 — Typed state definition for Inbox Action Agent."""

from typing import Any, Dict, List, Optional, TypedDict


class InboxAgentState(TypedDict, total=False):
    # Identification & Execution
    run_id: str
    organization_id: str
    trigger_event_id: Optional[str]
    entity_id: Optional[str]

    # Email payload
    message_id: Optional[str]
    thread_id: Optional[str]
    sender: Optional[str]
    subject: Optional[str]
    body: Optional[str]
    raw_payload: Optional[Dict[str, Any]]
    attachments: List[Dict[str, Any]]
    associated_documents: List[Dict[str, Any]]

    # Resolution & Context
    resolver_result: Optional[Dict[str, Any]]
    shipment: Optional[Dict[str, Any]]
    context: Dict[str, Any]

    # Reasoning & Classification
    intent: Optional[str]  # pickup_confirmation, eta_update, missing_information, ambiguous, unrelated, etc.
    intent_confidence: float
    reasoning: Optional[str]
    extracted_data: Dict[str, Any]

    # Action Proposal & Policy
    proposed_action: Optional[Dict[str, Any]]  # {tool_name, arguments, risk_level, reason}
    approval_state: str  # none, auto_approved, needs_human, approved, rejected
    approval_reason: Optional[str]

    # Execution & Verification
    tools_available: List[str]
    tool_calls: List[Dict[str, Any]]
    tool_result: Optional[Dict[str, Any]]
    verification_result: Optional[Dict[str, Any]]

    # Outcome & Audit
    decision: Optional[str]
    confidence: float
    terminal_outcome: str  # completed, needs_human, failed
    final_result: Optional[Dict[str, Any]]
    errors: List[str]

    # Observability & Cost
    model_used: str
    model_version: str
    prompt_version: str
    cost_estimate: float
    started_at: str
    completed_at: Optional[str]
