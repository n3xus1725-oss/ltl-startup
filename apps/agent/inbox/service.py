"""Phase 1.6 & 1.8 — Business orchestration outside the model for Inbox Action Agent.

Per Section 4 Agent Implementation Standard: business orchestration outside model.
Enforces trigger idempotency, initializes AgentRun traces, invokes LangGraph,
and normalizes typed outputs.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.agent.inbox.graph import build_inbox_agent_graph
from apps.agent.inbox.prompts import PROMPT_VERSION
from apps.agent.inbox.tools import ALLOWED_INBOX_TOOL_NAMES, get_inbox_tool_registry
from apps.agent.inbox.validators import InboxAgentInput, InboxAgentOutput
from packages.domain.logging import logger
from packages.domain.models import AgentRun
from packages.llm.gateway import LLMGateway
from packages.storage.repositories.agent_runs import AgentRunRepository
from packages.tools.registry import ToolRegistry


class InboxAgentService:
    """Service orchestrator for the Inbox Action Agent workflow."""

    def __init__(
        self,
        db: Session,
        tool_registry: Optional[ToolRegistry] = None,
        llm_gateway: Optional[LLMGateway] = None,
    ):
        self.db = db
        self.tool_registry = tool_registry or get_inbox_tool_registry()
        self.llm_gateway = llm_gateway or LLMGateway()
        self.agent_repo = AgentRunRepository(db)

    async def run(self, input_data: InboxAgentInput) -> InboxAgentOutput:
        """Execute the Inbox Action Agent workflow with strict idempotency and audit tracing."""
        org_uuid = uuid.UUID(input_data.organization_id)
        trigger_id = input_data.trigger_event_id

        # 1. Idempotency Check: prevent duplicate agent runs for identical trigger_event_id
        if trigger_id:
            existing_run = self.db.execute(
                select(AgentRun).where(
                    AgentRun.organization_id == org_uuid,
                    AgentRun.trigger_event_id == trigger_id,
                )
            ).scalars().first()

            if existing_run and existing_run.status in ("completed", "needs_human"):
                logger.info(f"Duplicate trigger event {trigger_id} detected; returning existing AgentRun {existing_run.id}")
                return InboxAgentOutput(
                    run_id=str(existing_run.id),
                    organization_id=str(existing_run.organization_id),
                    trigger_event_id=existing_run.trigger_event_id,
                    entity_id=existing_run.entity_id,
                    terminal_outcome=existing_run.status,
                    decision=existing_run.decision,
                    confidence=existing_run.confidence or 1.0,
                    approval_state=existing_run.approval_state,
                    tools_available=existing_run.tools_available or ALLOWED_INBOX_TOOL_NAMES,
                    tool_calls=[],
                    final_result=existing_run.final_result or {},
                    errors=[],
                    cost_estimate=float(existing_run.cost_estimate or 0.0),
                    model_used=existing_run.model_used or self.llm_gateway.default_model,
                    model_version=existing_run.model_version or "v0",
                    prompt_version=existing_run.prompt_version or PROMPT_VERSION,
                    started_at=existing_run.started_at.isoformat() if existing_run.started_at else datetime.now(timezone.utc).isoformat(),
                    completed_at=existing_run.completed_at.isoformat() if existing_run.completed_at else None,
                )

        # 2. Create initial AgentRun database record
        run_record = self.agent_repo.create_run(
            organization_id=org_uuid,
            agent_name="inbox_action_agent",
            trigger_event_id=trigger_id,
            model_used=self.llm_gateway.default_model,
            model_version="v0",
            prompt_version=PROMPT_VERSION,
            tools_available=ALLOWED_INBOX_TOOL_NAMES,
        )

        # 3. Build and execute graph
        compiled_graph = build_inbox_agent_graph(
            db=self.db,
            tool_registry=self.tool_registry,
            llm_gateway=self.llm_gateway,
        )

        initial_state = {
            "run_id": str(run_record.id),
            "organization_id": str(org_uuid),
            "trigger_event_id": trigger_id,
            "message_id": input_data.message_id,
            "thread_id": input_data.thread_id,
            "sender": input_data.sender,
            "subject": input_data.subject,
            "body": input_data.body_text,
            "raw_payload": input_data.raw_metadata or {},
            "attachments": input_data.attachments or [],
            "associated_documents": [],
            "errors": [],
            "started_at": run_record.started_at.isoformat(),
        }

        config = {"configurable": {"thread_id": str(run_record.id)}}
        final_state = await compiled_graph.ainvoke(initial_state, config=config)

        # Update entity_id on run if resolved
        if final_state.get("entity_id") and not run_record.entity_id:
            run_record.entity_id = final_state["entity_id"]
            self.db.commit()

        # 4. Formulate validated output
        return InboxAgentOutput(
            run_id=str(run_record.id),
            organization_id=str(org_uuid),
            trigger_event_id=trigger_id,
            entity_id=final_state.get("entity_id"),
            terminal_outcome=final_state.get("terminal_outcome", "completed"),
            decision=final_state.get("decision"),
            confidence=float(final_state.get("confidence", 1.0)),
            approval_state=final_state.get("approval_state", "none"),
            tools_available=final_state.get("tools_available", ALLOWED_INBOX_TOOL_NAMES),
            tool_calls=final_state.get("tool_calls", []),
            final_result=final_state.get("final_result"),
            errors=final_state.get("errors", []),
            cost_estimate=float(final_state.get("cost_estimate", 0.0)),
            model_used=final_state.get("model_used", self.llm_gateway.default_model),
            model_version=final_state.get("model_version", "v0"),
            prompt_version=final_state.get("prompt_version", PROMPT_VERSION),
            started_at=final_state.get("started_at", run_record.started_at.isoformat()),
            completed_at=final_state.get("completed_at"),
        )


async def run_inbox_agent(
    organization_id: str,
    subject: str,
    body_text: str,
    sender: str,
    db: Session,
    trigger_event_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    message_id: Optional[str] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    tool_registry: Optional[ToolRegistry] = None,
    llm_gateway: Optional[LLMGateway] = None,
) -> InboxAgentOutput:
    """Convenience helper to invoke the Inbox Action Agent."""
    service = InboxAgentService(
        db=db,
        tool_registry=tool_registry,
        llm_gateway=llm_gateway,
    )
    input_data = InboxAgentInput(
        organization_id=organization_id,
        trigger_event_id=trigger_event_id,
        message_id=message_id,
        thread_id=thread_id,
        sender=sender,
        subject=subject,
        body_text=body_text,
        attachments=attachments or [],
    )
    return await service.run(input_data)
