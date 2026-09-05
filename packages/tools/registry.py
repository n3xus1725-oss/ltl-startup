"""Phase 1.7 — Tool Registry and Base Tool definitions.

Provides BaseTool abstract class, ToolContext, and ToolRegistry with permission verification,
idempotency guarantees, and immutable audit logging.
"""

from abc import ABC, abstractmethod
import time
import uuid
from typing import Any, Dict, List, Optional, Set, Type
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from packages.domain.logging import logger
from packages.storage.repositories.agent_runs import AgentRunRepository
from packages.storage.repositories.audit import AuditLogRepository


class ToolPermissionDenied(Exception):
    """Raised when an actor lacks the required permission to execute a tool."""
    pass


class ToolNotFoundError(Exception):
    """Raised when a requested tool is not found in the registry."""
    pass


class ToolExecutionError(Exception):
    """Raised when tool execution fails unexpectedly."""
    pass


class ToolContext(BaseModel):
    """Execution context provided to tools during dispatch."""
    model_config = {"arbitrary_types_allowed": True}

    organization_id: uuid.UUID
    agent_run_id: Optional[uuid.UUID] = None
    actor_id: str = "agent"
    actor_type: str = "agent"
    granted_permissions: Set[str] = Field(default_factory=set)
    idempotency_key: Optional[str] = None


class ToolExecutionResult(BaseModel):
    """Result of a tool dispatch."""
    tool_name: str
    status: str  # success, error, cached
    data: Dict[str, Any]
    error_message: Optional[str] = None
    latency_ms: int = 0
    idempotency_key: Optional[str] = None
    audit_metadata: Dict[str, Any] = Field(default_factory=dict)


class BaseTool(ABC):
    """Abstract base class for all typed tools in the AI Freight Platform."""

    name: str
    purpose: str
    input_schema: Type[BaseModel]
    output_schema: Type[BaseModel]
    required_permission: str
    external_side_effects: bool = False

    def get_metadata(self) -> Dict[str, Any]:
        """Return standardized tool metadata."""
        return {
            "name": self.name,
            "purpose": self.purpose,
            "input_schema": self.input_schema.model_json_schema(),
            "output_schema": self.output_schema.model_json_schema(),
            "required_permission": self.required_permission,
            "external_side_effects": self.external_side_effects,
        }

    @abstractmethod
    async def execute(
        self, context: ToolContext, db: Session, input_data: BaseModel
    ) -> BaseModel:
        """Execute the typed tool logic."""
        pass


class ToolRegistry:
    """Registry managing available tools, access permissions, and audited execution."""

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool instance."""
        if tool.name in self._tools:
            logger.warning(f"Overwriting existing tool registration: {tool.name}")
        self._tools[tool.name] = tool
        logger.info(f"Registered tool: {tool.name} (permission: {tool.required_permission})")

    def get_tool(self, name: str) -> Optional[BaseTool]:
        """Retrieve tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> List[BaseTool]:
        """List all registered tools."""
        return list(self._tools.values())

    def list_tool_names(self) -> List[str]:
        """List all registered tool names."""
        return list(self._tools.keys())

    async def execute(
        self,
        name: str,
        context: ToolContext,
        db: Session,
        arguments: Dict[str, Any],
    ) -> ToolExecutionResult:
        """Verify permissions, enforce idempotency, execute tool, and record audit log."""
        tool = self.get_tool(name)
        if not tool:
            raise ToolNotFoundError(f"Tool '{name}' is not registered")

        # 1. Permission Check
        # If wildcard or specific permission granted
        has_permission = (
            "*" in context.granted_permissions
            or tool.required_permission in context.granted_permissions
        )
        if not has_permission:
            logger.warning(
                f"Permission denied: {context.actor_id} lacks '{tool.required_permission}' for tool '{name}'"
            )
            raise ToolPermissionDenied(
                f"Actor lacks required permission '{tool.required_permission}' for tool '{name}'"
            )

        # 2. Input validation
        validated_input = tool.input_schema.model_validate(arguments)

        # 3. Check idempotency from arguments or context
        idemp_key = getattr(validated_input, "idempotency_key", None) or context.idempotency_key
        agent_repo = AgentRunRepository(db)
        audit_repo = AuditLogRepository(db)

        if idemp_key and context.agent_run_id:
            # Check if this tool call already ran for this agent run
            existing_calls = agent_repo.list_all(context.organization_id)
            # Query ToolCall via agent_repo if idempotency match
            # If tool call with same idempotency key exists, return cached result
            from packages.domain.models import ToolCall
            from sqlalchemy import select

            existing_call = db.execute(
                select(ToolCall).where(
                    ToolCall.organization_id == context.organization_id,
                    ToolCall.agent_run_id == context.agent_run_id,
                    ToolCall.idempotency_key == idemp_key,
                )
            ).scalars().first()

            if existing_call and existing_call.status == "success":
                logger.info(f"Returning cached tool result for idempotency key: {idemp_key}")
                return ToolExecutionResult(
                    tool_name=name,
                    status="cached",
                    data=existing_call.result or {},
                    latency_ms=0,
                    idempotency_key=idemp_key,
                    audit_metadata={"cached": True},
                )

        start_time = time.time()
        input_dump = validated_input.model_dump(mode="json")
        try:
            # 4. Execute tool
            output_model = await tool.execute(context, db, validated_input)
            output_data = output_model.model_dump(mode="json")
            latency = int((time.time() - start_time) * 1000)

            # 5. Record tool call in agent run trace
            if context.agent_run_id:
                agent_repo.record_tool_call(
                    organization_id=context.organization_id,
                    agent_run_id=context.agent_run_id,
                    tool_name=name,
                    arguments=input_dump,
                    result=output_data,
                    status="success",
                    idempotency_key=idemp_key,
                    latency_ms=latency,
                )

            # 6. Record immutable audit ledger entry
            audit_repo.record(
                organization_id=context.organization_id,
                event_id=str(uuid.uuid4()),
                action=f"tool_execution:{name}",
                actor_type=context.actor_type,
                actor_id=context.actor_id,
                target_entity_type="tool",
                target_entity_id=name,
                payload_before=input_dump,
                payload_after=output_data,
                metadata_json={
                    "latency_ms": latency,
                    "external_side_effects": tool.external_side_effects,
                },
                idempotency_key=idemp_key,
            )

            return ToolExecutionResult(
                tool_name=name,
                status="success",
                data=output_data,
                latency_ms=latency,
                idempotency_key=idemp_key,
                audit_metadata={
                    "required_permission": tool.required_permission,
                    "external_side_effects": tool.external_side_effects,
                },
            )

        except Exception as e:
            latency = int((time.time() - start_time) * 1000)
            err_msg = str(e)
            logger.error(f"Tool execution failed for '{name}': {err_msg}")

            if context.agent_run_id:
                agent_repo.record_tool_call(
                    organization_id=context.organization_id,
                    agent_run_id=context.agent_run_id,
                    tool_name=name,
                    arguments=input_dump,
                    result={"error": err_msg},
                    status="failed",
                    idempotency_key=idemp_key,
                    latency_ms=latency,
                    error_message=err_msg,
                )

            return ToolExecutionResult(
                tool_name=name,
                status="failed",
                data={},
                error_message=err_msg,
                latency_ms=latency,
                idempotency_key=idemp_key,
            )
