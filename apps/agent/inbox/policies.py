"""Phase 1.6 & 1.8 — Deterministic Policy Checks for Inbox Action Agent.

Per Section 4 Agent Implementation Standard: deterministic policy checks
(permissions, auto vs human approval thresholds).
Per Rule 3 & 4: Business rules in deterministic Python, not LLM.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

from dateutil.parser import parse as parse_date

AUTO_APPROVE_CONFIDENCE_THRESHOLD = 0.85
MAX_ALLOWED_ETA_DELAY_HOURS = 48.0
PERMITTED_INBOX_TOOLS = {
    "find_shipment",
    "get_shipment",
    "update_shipment",
    "attach_document",
    "create_task",
    "send_email",
    "reply_to_thread",
    "create_exception",
    "search_context",
}


class PolicyDecision:
    def __init__(
        self,
        approval_state: str,  # auto_approved, needs_human, rejected
        reason: str,
        requires_human_task: bool = False,
    ):
        self.approval_state = approval_state
        self.reason = reason
        self.requires_human_task = requires_human_task

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approval_state": self.approval_state,
            "reason": self.reason,
            "requires_human_task": self.requires_human_task,
        }


def check_tool_permission(tool_name: str, granted_permissions: Set[str]) -> bool:
    """Deterministic check if granted permissions permit tool execution."""
    if "*" in granted_permissions:
        return True

    permission_map = {
        "find_shipment": "shipment:read",
        "get_shipment": "shipment:read",
        "update_shipment": "shipment:write",
        "attach_document": "shipment:write",
        "create_task": "task:write",
        "send_email": "email:send",
        "reply_to_thread": "email:send",
        "create_exception": "exception:write",
        "search_context": "shipment:read",
    }

    req = permission_map.get(tool_name)
    if not req:
        return False
    return req in granted_permissions


def evaluate_action_policy(
    intent: str,
    confidence: float,
    is_ambiguous: bool,
    shipment: Optional[Dict[str, Any]],
    proposed_tool: str,
    proposed_arguments: Dict[str, Any],
    risk_level: str = "low",
) -> PolicyDecision:
    """Deterministic policy gate deciding whether an action may be automatically executed
    or must pause for human review.
    """
    # Rule 9: If evidence is ambiguous, never guess or choose arbitrarily; pause for human review.
    if is_ambiguous:
        return PolicyDecision(
            approval_state="needs_human",
            reason="Entity resolution is ambiguous; human operator must review and confirm shipment",
            requires_human_task=True,
        )

    # Unknown shipment: candidate identifier extracted from email but not found in database
    if intent == "unknown_shipment":
        return PolicyDecision(
            approval_state="needs_human",
            reason="Referenced freight identifier was not found in database; requires operator review",
            requires_human_task=True,
        )

    # Rule 9: Confidence threshold check
    if confidence < AUTO_APPROVE_CONFIDENCE_THRESHOLD:
        return PolicyDecision(
            approval_state="needs_human",
            reason=f"Confidence ({confidence:.2f}) is below automated threshold ({AUTO_APPROVE_CONFIDENCE_THRESHOLD:.2f})",
            requires_human_task=True,
        )

    # Validate tool is in inbox agent tool registry
    if proposed_tool not in PERMITTED_INBOX_TOOLS:
        return PolicyDecision(
            approval_state="rejected",
            reason=f"Tool '{proposed_tool}' is not permitted for the Inbox Action Agent",
            requires_human_task=True,
        )

    # Shipment-targeted operations require a verified shipment
    if proposed_tool in ("update_shipment", "attach_document") and not shipment:
        return PolicyDecision(
            approval_state="needs_human",
            reason=f"Tool '{proposed_tool}' requires an identified shipment, but none was resolved",
            requires_human_task=True,
        )

    # High or critical risk flags require human review
    if risk_level in ("high", "critical"):
        return PolicyDecision(
            approval_state="needs_human",
            reason=f"Proposed action flagged with risk level '{risk_level}'",
            requires_human_task=True,
        )

    # Workflow-specific deterministic policies

    # Workflow A: Pickup confirmation
    if intent == "pickup_confirmation":
        pickup_date_val = proposed_arguments.get("pickup_date")
        if not pickup_date_val:
            return PolicyDecision(
                approval_state="needs_human",
                reason="Pickup confirmation requires an explicit pickup date/timestamp",
                requires_human_task=True,
            )
        try:
            p_dt = parse_date(str(pickup_date_val)) if isinstance(pickup_date_val, str) else pickup_date_val
            # Sanity check: Pickup date cannot be more than 48 hours in the future
            now = datetime.now(timezone.utc)
            if p_dt.tzinfo is None:
                p_dt = p_dt.replace(tzinfo=timezone.utc)
            if (p_dt - now).total_seconds() > 48 * 3600:
                return PolicyDecision(
                    approval_state="needs_human",
                    reason="Pickup timestamp is more than 48 hours in the future",
                    requires_human_task=True,
                )
        except Exception:
            return PolicyDecision(
                approval_state="needs_human",
                reason="Invalid pickup timestamp format",
                requires_human_task=True,
            )

        return PolicyDecision(
            approval_state="auto_approved",
            reason="High-confidence pickup confirmation with valid timestamp",
            requires_human_task=False,
        )

    # Workflow B: ETA update
    if intent == "eta_update":
        new_eta_val = proposed_arguments.get("eta")
        if not new_eta_val:
            return PolicyDecision(
                approval_state="needs_human",
                reason="ETA update requires an explicit new ETA timestamp",
                requires_human_task=True,
            )

        # Deterministic delay calculation
        current_eta_val = shipment.get("eta") if shipment else None
        if current_eta_val:
            try:
                c_dt = parse_date(str(current_eta_val))
                n_dt = parse_date(str(new_eta_val))
                if c_dt.tzinfo is None:
                    c_dt = c_dt.replace(tzinfo=timezone.utc)
                if n_dt.tzinfo is None:
                    n_dt = n_dt.replace(tzinfo=timezone.utc)

                delay_hours = (n_dt - c_dt).total_seconds() / 3600.0
                if delay_hours > MAX_ALLOWED_ETA_DELAY_HOURS:
                    return PolicyDecision(
                        approval_state="needs_human",
                        reason=f"New ETA represents a severe delay of {delay_hours:.1f} hours (> {MAX_ALLOWED_ETA_DELAY_HOURS}h threshold)",
                        requires_human_task=True,
                    )
            except Exception:
                pass

        return PolicyDecision(
            approval_state="auto_approved",
            reason="High-confidence ETA update within normal SLA thresholds",
            requires_human_task=False,
        )

    # Workflow C: Missing-information request
    if intent == "missing_information":
        return PolicyDecision(
            approval_state="auto_approved",
            reason="Missing-information workflow automatically initiates standardized request and creates follow-up task",
            requires_human_task=False,
        )

    # Document attachment (BOL / POD / Rate Con)
    if intent in ("document_attached", "pod_received", "bol_received") or proposed_tool == "attach_document":
        return PolicyDecision(
            approval_state="auto_approved",
            reason="Document association for verified shipment auto-approved",
            requires_human_task=False,
        )

    # Irrelevant or unrelated messages do not execute side-effect tools
    if intent in ("unrelated", "general_inquiry"):
        return PolicyDecision(
            approval_state="auto_approved" if proposed_tool in ("reply_to_thread", "create_task") else "needs_human",
            reason=f"Intent is '{intent}'; executing standard response or routing",
            requires_human_task=(intent != "unrelated"),
        )

    # Default fallback: escalate to human
    return PolicyDecision(
        approval_state="needs_human",
        reason=f"Unknown intent '{intent}' requires human triage",
        requires_human_task=True,
    )
