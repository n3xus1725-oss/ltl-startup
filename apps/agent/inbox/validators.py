"""Phase 1.6 & 1.8 — Input and output validators for Inbox Action Agent.

Per Section 4 Agent Implementation Standard: input/output validation.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class InboxAgentInput(BaseModel):
    """Normalized input payload that triggers an Inbox Action Agent run."""
    organization_id: str = Field(..., description="Organization UUID")
    trigger_event_id: Optional[str] = Field(default=None, description="Inbound event / idempotency ID")
    message_id: Optional[str] = Field(default=None, description="External message ID")
    thread_id: Optional[str] = Field(default=None, description="Email thread ID")
    sender: str = Field(..., description="Sender email address")
    subject: str = Field(default="", description="Email subject line")
    body_text: str = Field(default="", description="Email text body")
    raw_metadata: Optional[Dict[str, Any]] = Field(default=None, description="Optional headers/attachments metadata")
    attachments: Optional[List[Dict[str, Any]]] = Field(default_factory=list, description="Attached documents metadata and IDs")


class IntentClassificationOutput(BaseModel):
    """Schema for validated model intent classification."""
    intent: str = Field(..., description="pickup_confirmation, eta_update, missing_information, ambiguous, unrelated, general_inquiry")
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str
    extracted_data: Dict[str, Any] = Field(default_factory=dict)


class ActionProposalOutput(BaseModel):
    """Schema for validated model tool proposal."""
    tool_name: str
    arguments: Dict[str, Any]
    risk_level: str = "low"
    reasoning: str


class VerificationResult(BaseModel):
    """Schema for deterministic tool execution verification."""
    is_valid: bool
    checks_passed: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class InboxAgentOutput(BaseModel):
    """Final canonical output of an Inbox Action Agent execution run."""
    run_id: str
    organization_id: str
    trigger_event_id: Optional[str] = None
    entity_id: Optional[str] = None
    terminal_outcome: str  # completed, needs_human, failed
    decision: Optional[str] = None
    confidence: float = 0.0
    approval_state: str = "none"
    tools_available: List[str] = Field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    final_result: Optional[Dict[str, Any]] = None
    errors: List[str] = Field(default_factory=list)
    cost_estimate: float = 0.0
    model_used: str = "default"
    model_version: str = "v1"
    prompt_version: str = "inbox_agent_v0.1"
    started_at: str
    completed_at: Optional[str] = None
