"""Dispute Agent Service — orchestration layer outside the LangGraph model."""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from apps.agent.dispute.graph import build_dispute_graph
from apps.agent.dispute.prompts import DISPUTE_PROMPT_VERSION
from apps.agent.dispute.validators import DisputeAgentInput, DisputeAgentOutput
from packages.domain.models import AgentRun
from packages.llm.gateway import LLMGateway
from packages.storage.repositories.disputes import DisputeRepository

logger = logging.getLogger(__name__)

_AGENT_NAME = "dispute_agent"


class DisputeAgentService:
    """Orchestrates the dispute agent lifecycle outside the LangGraph model."""

    def __init__(self, db: Session, llm: LLMGateway):
        self.db = db
        self.llm = llm
        self.graph = build_dispute_graph(db, llm)

    def run(self, input_data: DisputeAgentInput) -> DisputeAgentOutput:
        """Execute the dispute agent workflow.
        Checks idempotency before creating a new AgentRun.
        """
        org_id = uuid.UUID(input_data.organization_id)
        idempotency_key = input_data.idempotency_key or f"dispute-{input_data.invoice_id}-{'-'.join(input_data.finding_ids[:2])}"

        # Idempotency: check for existing dispute with this key
        dispute_repo = DisputeRepository(self.db)
        existing_dispute = dispute_repo.get_by_idempotency_key(org_id, idempotency_key)
        if existing_dispute:
            logger.info("Idempotency hit: returning existing dispute %s", existing_dispute.dispute_number)
            return DisputeAgentOutput(
                run_id=str(existing_dispute.agent_run_id or uuid.uuid4()),
                dispute_id=str(existing_dispute.id),
                dispute_number=existing_dispute.dispute_number,
                status=existing_dispute.status,
                approval_status=existing_dispute.approval_status,
                recipient_email=existing_dispute.recipient_email,
                disputed_amount=float(existing_dispute.disputed_amount),
                dispute_letter_subject=existing_dispute.dispute_letter_subject,
                needs_human=(existing_dispute.status == "pending_approval"),
                trajectory=[],
            )

        # Create AgentRun record
        run_id = uuid.uuid4()
        agent_run = AgentRun(
            id=run_id,
            organization_id=org_id,
            agent_name=_AGENT_NAME,
            trigger_event_id=idempotency_key,
            entity_id=input_data.invoice_id,
            prompt_version=DISPUTE_PROMPT_VERSION,
            tools_available=["get_audit_findings", "resolve_carrier_contact",
                             "create_dispute_draft", "send_dispute_email",
                             "create_recovery_entry", "escalate_to_human"],
            status="running",
            approval_state="none",
            cost_estimate=0.0,
        )
        self.db.add(agent_run)
        self.db.commit()

        # Build initial state
        initial_state: dict = {
            "organization_id": input_data.organization_id,
            "invoice_id": input_data.invoice_id,
            "finding_ids": input_data.finding_ids,
            "triggered_by": input_data.triggered_by,
            "run_id": str(run_id),
            "findings": [],
            "carrier_name": "",
            "invoice_number": "",
            "disputed_amount": 0.0,
            "expected_amount": None,
            "billed_amount": None,
            "recipient_email": None,
            "recipient_contact_name": None,
            "recipient_resolved": False,
            "dispute_id": None,
            "dispute_number": None,
            "dispute_letter_subject": None,
            "dispute_letter_text": None,
            "approval_decision": "pending",
            "has_signed_documentation": False,
            "confidence": 0.0,
            "dispute_status": "draft",
            "recovery_id": None,
            "needs_human": False,
            "error": None,
            "trajectory": [],
        }

        # Run graph
        try:
            final_state = self.graph.invoke(initial_state)
        except Exception as e:
            logger.exception("Dispute agent graph failed: %s", e)
            agent_run.status = "failed"
            agent_run.errors = [{"error": str(e)}]
            agent_run.completed_at = datetime.now(timezone.utc)
            self.db.commit()
            return DisputeAgentOutput(
                run_id=str(run_id),
                status="failed",
                approval_status="pending",
                needs_human=True,
                error=str(e),
                trajectory=[],
            )

        # Update AgentRun with final results
        agent_run.status = "completed"
        agent_run.decision = final_state.get("dispute_status", "unknown")
        agent_run.confidence = final_state.get("confidence", 0.0)
        agent_run.approval_state = final_state.get("approval_decision", "none")
        agent_run.final_result = {
            "dispute_id": final_state.get("dispute_id"),
            "dispute_number": final_state.get("dispute_number"),
            "dispute_status": final_state.get("dispute_status"),
            "recipient_email": final_state.get("recipient_email"),
            "disputed_amount": final_state.get("disputed_amount"),
            "needs_human": final_state.get("needs_human", False),
        }
        agent_run.completed_at = datetime.now(timezone.utc)
        self.db.commit()

        trajectory = final_state.get("trajectory", [])
        letter_text = final_state.get("dispute_letter_text", "")
        return DisputeAgentOutput(
            run_id=str(run_id),
            dispute_id=final_state.get("dispute_id"),
            dispute_number=final_state.get("dispute_number"),
            status=final_state.get("dispute_status", "draft"),
            approval_status=final_state.get("approval_decision", "pending"),
            recipient_email=final_state.get("recipient_email"),
            disputed_amount=final_state.get("disputed_amount"),
            dispute_letter_subject=final_state.get("dispute_letter_subject"),
            dispute_letter_preview=letter_text[:500] if letter_text else None,
            needs_human=final_state.get("needs_human", False),
            error=final_state.get("error"),
            trajectory=trajectory,
        )


def run_dispute_agent(
    organization_id: str,
    invoice_id: str,
    finding_ids: list[str],
    db: Session,
    llm: LLMGateway,
    triggered_by: str = "system",
    idempotency_key: str | None = None,
) -> DisputeAgentOutput:
    """Convenience function to run the dispute agent."""
    input_data = DisputeAgentInput(
        organization_id=organization_id,
        invoice_id=invoice_id,
        finding_ids=finding_ids,
        triggered_by=triggered_by,
        idempotency_key=idempotency_key,
    )
    service = DisputeAgentService(db=db, llm=llm)
    return service.run(input_data)
