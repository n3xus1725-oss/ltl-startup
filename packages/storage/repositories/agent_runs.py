"""Agent run and tool call repository."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.domain.models import AgentRun, Approval, ToolCall
from packages.storage.repositories.base import BaseRepository


class AgentRunRepository(BaseRepository[AgentRun]):
    """Repository for managing agent execution traces, tool calls, and approval lifecycles."""

    def __init__(self, db: Session):
        super().__init__(AgentRun, db)

    def create_run(
        self,
        organization_id: uuid.UUID,
        agent_name: str,
        trigger_event_id: Optional[str] = None,
        entity_id: Optional[str] = None,
        model_used: Optional[str] = None,
        model_version: Optional[str] = None,
        prompt_version: Optional[str] = None,
        tools_available: Optional[List[str]] = None,
    ) -> AgentRun:
        run = AgentRun(
            id=uuid.uuid4(),
            organization_id=organization_id,
            agent_name=agent_name,
            trigger_event_id=trigger_event_id,
            entity_id=entity_id,
            model_used=model_used,
            model_version=model_version,
            prompt_version=prompt_version,
            tools_available=tools_available or [],
            status="running",
            approval_state="none",
            started_at=datetime.now(timezone.utc),
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def update_run(
        self,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        status: Optional[str] = None,
        decision: Optional[str] = None,
        confidence: Optional[float] = None,
        approval_state: Optional[str] = None,
        final_result: Optional[Dict[str, Any]] = None,
        errors: Optional[Dict[str, Any]] = None,
        cost_estimate: Optional[float] = None,
    ) -> Optional[AgentRun]:
        run = self.get_by_id(organization_id, run_id)
        if not run:
            return None

        if status:
            run.status = status
            if status in ("completed", "failed", "needs_human"):
                run.completed_at = datetime.now(timezone.utc)
        if decision is not None:
            run.decision = decision
        if confidence is not None:
            run.confidence = confidence
        if approval_state is not None:
            run.approval_state = approval_state
        if final_result is not None:
            run.final_result = final_result
        if errors is not None:
            run.errors = errors
        if cost_estimate is not None:
            run.cost_estimate = cost_estimate

        self.db.commit()
        self.db.refresh(run)
        return run

    def record_tool_call(
        self,
        organization_id: uuid.UUID,
        agent_run_id: uuid.UUID,
        tool_name: str,
        arguments: Dict[str, Any],
        result: Optional[Dict[str, Any]] = None,
        status: str = "pending",
        idempotency_key: Optional[str] = None,
        latency_ms: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> ToolCall:
        """Record a tool execution. Enforces idempotency per run."""
        if idempotency_key:
            stmt = select(ToolCall).where(
                ToolCall.organization_id == organization_id,
                ToolCall.agent_run_id == agent_run_id,
                ToolCall.idempotency_key == idempotency_key,
            )
            existing = self.db.execute(stmt).scalars().first()
            if existing:
                return existing

        tool_call = ToolCall(
            id=uuid.uuid4(),
            organization_id=organization_id,
            agent_run_id=agent_run_id,
            tool_name=tool_name,
            arguments=arguments,
            result=result,
            status=status,
            idempotency_key=idempotency_key,
            latency_ms=latency_ms,
            error_message=error_message,
            executed_at=datetime.now(timezone.utc),
        )
        self.db.add(tool_call)
        self.db.commit()
        self.db.refresh(tool_call)
        return tool_call

    def create_approval(
        self,
        organization_id: uuid.UUID,
        agent_run_id: uuid.UUID,
        action_type: str,
        action_payload: Dict[str, Any],
        requested_by: str = "agent",
    ) -> Approval:
        approval = Approval(
            id=uuid.uuid4(),
            organization_id=organization_id,
            agent_run_id=agent_run_id,
            action_type=action_type,
            action_payload=action_payload,
            status="pending",
            requested_by=requested_by,
        )
        self.db.add(approval)
        self.db.commit()
        self.db.refresh(approval)
        return approval
