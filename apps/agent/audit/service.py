"""Phase 3.4 — Business Orchestration Outside Model for Billing Audit Agent.

Adheres strictly to the Section 4 Agent Implementation Standard:
- Business orchestration outside model.
- Idempotency guarantees on triggers.
- Initializes AgentRun trace and persists financial audit records.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.agent.audit.graph import build_audit_agent_graph
from apps.agent.audit.prompts import AUDIT_PROMPT_VERSION
from apps.agent.audit.tools import ALLOWED_AUDIT_TOOL_NAMES, get_audit_tool_registry
from apps.agent.audit.validators import AuditAgentInput, AuditAgentOutput
from packages.domain.logging import logger
from packages.domain.models import AgentRun
from packages.llm.gateway import LLMGateway
from packages.storage.repositories.agent_runs import AgentRunRepository
from packages.tools.registry import ToolRegistry


class AuditAgentService:
    """Service orchestrator for the Billing Audit Agent workflow."""

    def __init__(
        self,
        db: Session,
        tool_registry: Optional[ToolRegistry] = None,
        llm_gateway: Optional[LLMGateway] = None,
    ):
        self.db = db
        self.tool_registry = tool_registry or get_audit_tool_registry()
        self.llm_gateway = llm_gateway or LLMGateway()
        self.agent_repo = AgentRunRepository(db)

    async def run(self, input_data: AuditAgentInput) -> AuditAgentOutput:
        """Execute the Billing Audit Agent workflow with strict idempotency and audit tracing."""
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
                fr = existing_run.final_result or {}
                return AuditAgentOutput(
                    run_id=str(existing_run.id),
                    organization_id=str(existing_run.organization_id),
                    invoice_id=fr.get("invoice_id") or (str(existing_run.entity_id) if existing_run.entity_id else None),
                    invoice_number=fr.get("invoice_number"),
                    carrier_name=fr.get("carrier_name"),
                    shipment_id=fr.get("shipment_id"),
                    is_clean=fr.get("is_clean", True),
                    total_discrepancy=fr.get("total_discrepancy", 0.0),
                    findings_count=fr.get("findings_count", 0),
                    findings=fr.get("findings", []),
                    classification=fr.get("classification", "clean_pass"),
                    next_workflow=fr.get("next_workflow", "payment_scheduled"),
                    approval_state=existing_run.approval_state or "none",
                    terminal_outcome=existing_run.status,
                    decision=existing_run.decision,
                    confidence=existing_run.confidence or 1.0,
                    explanation=fr.get("explanation"),
                    tools_available=existing_run.tools_available or ALLOWED_AUDIT_TOOL_NAMES,
                    tool_calls=[],
                    final_result=fr,
                    errors=[],
                    trajectory=fr.get("trajectory", []),
                    cost_estimate=float(existing_run.cost_estimate or 0.0),
                    model_used=existing_run.model_used or self.llm_gateway.default_model,
                    model_version=existing_run.model_version or "v0",
                    prompt_version=existing_run.prompt_version or AUDIT_PROMPT_VERSION,
                    started_at=existing_run.started_at.isoformat() if existing_run.started_at else datetime.now(timezone.utc).isoformat(),
                    completed_at=existing_run.completed_at.isoformat() if existing_run.completed_at else None,
                )

        # 2. Create initial AgentRun database record
        run_record = self.agent_repo.create_run(
            organization_id=org_uuid,
            agent_name="billing_audit_agent",
            trigger_event_id=trigger_id,
            model_used=self.llm_gateway.default_model,
            model_version="v0",
            prompt_version=AUDIT_PROMPT_VERSION,
            tools_available=ALLOWED_AUDIT_TOOL_NAMES,
        )

        # 3. Build and execute graph
        compiled_graph = build_audit_agent_graph(
            db=self.db,
            tool_registry=self.tool_registry,
            llm_gateway=self.llm_gateway,
        )

        initial_state = {
            "run_id": str(run_record.id),
            "organization_id": str(org_uuid),
            "trigger_event_id": trigger_id,
            "invoice_id": input_data.invoice_id,
            "invoice_text": input_data.invoice_text,
            "carrier_name": input_data.carrier_name,
            "invoice_number": input_data.invoice_number,
            "force_reasoning_model": input_data.force_reasoning_model,
            "tools_available": ALLOWED_AUDIT_TOOL_NAMES,
            "tool_calls": [],
            "errors": [],
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

        thread_id = f"audit-{run_record.id}"
        final_state = await compiled_graph.ainvoke(
            initial_state,
            config={"configurable": {"thread_id": thread_id}},
        )

        # 4. Construct typed output
        fr = final_state.get("final_result", {})
        return AuditAgentOutput(
            run_id=str(run_record.id),
            organization_id=str(org_uuid),
            invoice_id=final_state.get("invoice_id"),
            invoice_number=final_state.get("invoice_number"),
            carrier_name=final_state.get("carrier_name"),
            shipment_id=final_state.get("shipment_id"),
            is_clean=final_state.get("is_clean", True),
            total_discrepancy=final_state.get("total_discrepancy", 0.0),
            findings_count=len(final_state.get("findings", [])),
            findings=final_state.get("findings", []),
            classification=final_state.get("classification", "clean_pass"),
            next_workflow=final_state.get("next_workflow", "payment_scheduled"),
            approval_state=final_state.get("approval_state", "auto_approved"),
            terminal_outcome=final_state.get("terminal_outcome", "completed"),
            decision=final_state.get("decision"),
            confidence=final_state.get("confidence", 1.0),
            explanation=final_state.get("explanation"),
            dispute_summary=final_state.get("dispute_summary"),
            tools_available=ALLOWED_AUDIT_TOOL_NAMES,
            tool_calls=final_state.get("tool_calls", []),
            final_result=fr,
            errors=final_state.get("errors", []),
            trajectory=final_state.get("trajectory", []),
            model_used=final_state.get("model_used", self.llm_gateway.default_model),
            model_version="v0",
            prompt_version=AUDIT_PROMPT_VERSION,
            prompt_tokens=final_state.get("prompt_tokens", 0),
            completion_tokens=final_state.get("completion_tokens", 0),
            total_tokens=final_state.get("total_tokens", 0),
            cost_estimate=final_state.get("cost_estimate", 0.0),
            started_at=final_state.get("started_at", datetime.now(timezone.utc).isoformat()),
            completed_at=final_state.get("completed_at"),
        )


async def run_audit_agent(
    organization_id: str,
    db: Session,
    invoice_id: Optional[str] = None,
    invoice_text: Optional[str] = None,
    carrier_name: Optional[str] = None,
    invoice_number: Optional[str] = None,
    trigger_event_id: Optional[str] = None,
    force_reasoning_model: bool = False,
    tool_registry: Optional[ToolRegistry] = None,
    llm_gateway: Optional[LLMGateway] = None,
) -> AuditAgentOutput:
    """Convenience helper to instantiate AuditAgentService and execute an audit."""
    service = AuditAgentService(
        db=db,
        tool_registry=tool_registry,
        llm_gateway=llm_gateway,
    )
    input_data = AuditAgentInput(
        organization_id=organization_id,
        invoice_id=invoice_id,
        invoice_text=invoice_text,
        carrier_name=carrier_name,
        invoice_number=invoice_number,
        trigger_event_id=trigger_event_id,
        force_reasoning_model=force_reasoning_model,
    )
    return await service.run(input_data)
