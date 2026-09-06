"""Phase 3.4 — Typed State definition for LangGraph Billing Audit Agent."""

from typing import Any, Dict, List, Optional, TypedDict


class AuditAgentState(TypedDict, total=False):
    # Identification & Run Metadata (Section 4 Standard)
    run_id: str
    organization_id: str
    trigger_event_id: Optional[str]
    entity_id: Optional[str]

    # Input Invoice Payload
    invoice_id: Optional[str]
    invoice_text: Optional[str]
    invoice_number: Optional[str]
    carrier_name: Optional[str]
    force_reasoning_model: bool

    # Ingestion & Resolved Entities
    invoice_data: Optional[Dict[str, Any]]
    shipment_id: Optional[str]
    shipment: Optional[Dict[str, Any]]
    canonical_shipment: Optional[Dict[str, Any]]

    # Gathered Evidence from Repositories
    contract: Optional[Dict[str, Any]]
    scale_ticket: Optional[Dict[str, Any]]
    bol: Optional[Dict[str, Any]]
    pod: Optional[Dict[str, Any]]
    attached_document_types: List[str]
    existing_invoices: List[Dict[str, Any]]

    # Deterministic Audit Engine Output (Rule 22: Code Arithmetic)
    audit_report: Optional[Dict[str, Any]]
    is_clean: bool
    total_discrepancy: float
    findings: List[Dict[str, Any]]
    classification: str  # clean_pass, discrepancies_flagged, missing_evidence, ambiguous

    # AI Quality Gate & Explanation (Phase 3.5 & 3.6)
    explanation: Optional[str]
    dispute_summary: Optional[str]
    explanation_model_used: Optional[str]

    # Next Workflow Decision & Routing
    next_workflow: str  # payment_scheduled, dispute_review, hold_for_documents, rejected_duplicate, needs_human
    decision: Optional[str]
    confidence: float
    approval_state: str  # auto_approved, needs_human, dispute_flagged, held
    terminal_outcome: str  # completed, needs_human, failed

    # Tools & Execution
    tools_available: List[str]
    tool_calls: List[Dict[str, Any]]
    final_result: Optional[Dict[str, Any]]
    errors: List[str]

    # Observability, Trajectory Trace & Cost Engineering (Phases 3.4 - 3.6)
    trajectory: List[Dict[str, Any]]
    model_used: str
    model_version: str
    prompt_version: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_estimate: float
    started_at: str
    completed_at: Optional[str]
