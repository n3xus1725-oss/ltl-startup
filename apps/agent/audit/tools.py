"""Phase 3.4 — Scoped Tool Registry for Billing Audit Agent."""

from typing import List

from packages.tools.audit_tools import register_audit_tools
from packages.tools.registry import ToolRegistry

ALLOWED_AUDIT_TOOL_NAMES: List[str] = [
    "get_invoice",
    "find_matching_contract",
    "get_shipment_documents",
    "record_audit_findings",
    "approve_invoice",
    "flag_dispute",
]


def get_audit_tool_registry() -> ToolRegistry:
    """Instantiate and return a registry populated with the audit agent's allowed tools."""
    registry = ToolRegistry()
    register_audit_tools(registry)
    return registry
