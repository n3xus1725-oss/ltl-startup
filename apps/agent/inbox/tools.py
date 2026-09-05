"""Phase 1.6 & 1.8 — Allowed tools for Inbox Action Agent.

Per Section 4 Agent Implementation Standard: allowed tools for this agent.
"""

from typing import List
from packages.tools.registry import BaseTool, ToolRegistry
from packages.tools.shipment_tools import (
    create_standard_tool_registry,
    FindShipmentTool,
    GetShipmentTool,
    UpdateShipmentTool,
    AttachDocumentTool,
    CreateTaskTool,
    SendEmailTool,
    ReplyToThreadTool,
    CreateExceptionTool,
)

ALLOWED_INBOX_TOOL_NAMES = [
    "find_shipment",
    "get_shipment",
    "update_shipment",
    "attach_document",
    "create_task",
    "send_email",
    "reply_to_thread",
    "create_exception",
]


def get_inbox_tool_registry() -> ToolRegistry:
    """Return ToolRegistry configured with the permitted tools for the Inbox Action Agent."""
    return create_standard_tool_registry()
