"""Phase 3.4 — Input and Output Validators for Billing Audit Agent."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AuditAgentInput(BaseModel):
    """Input payload to trigger an Audit Agent execution."""

    organization_id: str = Field(..., description="Organization UUID")
    invoice_id: Optional[str] = Field(None, description="Stored invoice UUID (if already in DB)")
    invoice_text: Optional[str] = Field(None, description="Raw invoice text or OCR payload")
    carrier_name: Optional[str] = Field(None, description="Carrier name (optional override)")
    invoice_number: Optional[str] = Field(None, description="Invoice number (optional override)")
    trigger_event_id: Optional[str] = Field(None, description="Idempotency trigger key")
    force_reasoning_model: bool = Field(False, description="Force stronger reasoning model (e.g. gpt-4o)")


class AuditAgentOutput(BaseModel):
    """Standardized output returned by the Audit Agent."""

    run_id: str
    organization_id: str
    invoice_id: Optional[str] = None
    invoice_number: Optional[str] = None
    carrier_name: Optional[str] = None
    shipment_id: Optional[str] = None

    is_clean: bool = True
    total_discrepancy: float = 0.0
    findings_count: int = 0
    findings: List[Dict[str, Any]] = Field(default_factory=list)

    classification: str = "clean_pass"
    next_workflow: str = "payment_scheduled"
    approval_state: str = "auto_approved"
    terminal_outcome: str = "completed"
    decision: Optional[str] = None
    confidence: float = 1.0

    explanation: Optional[str] = None
    dispute_summary: Optional[str] = None

    tools_available: List[str] = Field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    final_result: Dict[str, Any] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)
    trajectory: List[Dict[str, Any]] = Field(default_factory=list)

    model_used: str = "none"
    model_version: str = "v0"
    prompt_version: str = "v1.0"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_estimate: float = 0.0
    started_at: str
    completed_at: Optional[str] = None
