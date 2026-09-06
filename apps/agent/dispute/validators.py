"""Input and output validation models for the Dispute Agent."""

from typing import Any, Optional

from pydantic import BaseModel, Field


class DisputeAgentInput(BaseModel):
    """Validated input to trigger the Dispute Agent."""
    organization_id: str = Field(..., description="Organization UUID")
    invoice_id: str = Field(..., description="CarrierInvoice UUID to dispute")
    finding_ids: list[str] = Field(
        ...,
        min_length=1,
        description="List of AuditFindingRecord UUIDs. Disputed amount will be summed from these — never from LLM.",
    )
    triggered_by: str = Field(default="system", description="Actor that triggered this dispute run")
    idempotency_key: Optional[str] = Field(
        default=None,
        description="Caller-supplied idempotency key to prevent duplicate disputes",
    )


class DisputeAgentOutput(BaseModel):
    """Structured output from the Dispute Agent run."""
    run_id: str
    dispute_id: Optional[str] = None
    dispute_number: Optional[str] = None
    status: str  # draft | pending_approval | sent | failed
    approval_status: str  # auto_approved | requires_human_approval | pending
    recipient_email: Optional[str] = None  # None if no verified contact found
    disputed_amount: Optional[float] = None
    dispute_letter_subject: Optional[str] = None
    dispute_letter_preview: Optional[str] = None  # First 500 chars of letter
    needs_human: bool = False
    error: Optional[str] = None
    trajectory: list[dict[str, Any]] = Field(default_factory=list)
    cost_estimate: float = 0.0
    model_used: Optional[str] = None
