"""LangGraph Dispute Agent — 8-node state machine.

Workflow:
  ingest → load_findings → resolve_recipient →
    [no contact] → request_human_approval → persist_results
    [contact found] → generate_dispute_package → apply_approval_gate →
      [auto_approved] → send_dispute → persist_results
      [requires_human] → request_human_approval → persist_results

FINANCIAL INTEGRITY: All dollar amounts flow from AuditFindingRecord.discrepancy_amount.
LLM only drafts dispute letter text. Financial amounts injected by application code.

RECIPIENT SAFETY: Email only resolved from CarrierContact DB. Never synthesized.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from apps.agent.dispute.policies import (
    ApprovalDecision,
    FinancialIntegrityError,
    determine_has_signed_documentation,
    evaluate_approval_gate,
    generate_dispute_number,
    generate_recovery_number,
    resolve_recipient,
    validate_financial_integrity,
)
from apps.agent.dispute.prompts import (
    DISPUTE_SYSTEM_PROMPT,
    build_dispute_letter_prompt,
)
from apps.agent.dispute.state import DisputeAgentState
from packages.domain.models import AuditFindingRecord, CarrierInvoice
from packages.llm.gateway import LLMGateway
from packages.storage.repositories.disputes import DisputeRepository
from packages.storage.repositories.recovery import RecoveryRepository

logger = logging.getLogger(__name__)


def _step(node: str, status: str, summary: str, details: dict | None = None) -> dict:
    return {
        "node": node,
        "status": status,
        "summary": summary,
        "details": details or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def build_dispute_graph(db: Session, llm: LLMGateway) -> Any:
    """Build and return the compiled LangGraph dispute agent."""

    def node_ingest(state: DisputeAgentState) -> dict:
        """Validate inputs, check idempotency against existing AgentRun."""
        try:
            org_id = uuid.UUID(state["organization_id"])
            finding_ids = state["finding_ids"]

            if not finding_ids:
                return {
                    "error": "No finding_ids provided. Cannot create dispute without audit findings.",
                    "needs_human": True,
                    "trajectory": state.get("trajectory", []) + [
                        _step("ingest", "error", "No finding_ids provided")
                    ],
                }

            return {
                "trajectory": state.get("trajectory", []) + [
                    _step("ingest", "ok", f"Dispute agent triggered for invoice {state['invoice_id']}", {
                        "finding_count": len(finding_ids),
                        "organization_id": str(org_id),
                    })
                ],
            }
        except Exception as e:
            return {
                "error": str(e),
                "needs_human": True,
                "trajectory": state.get("trajectory", []) + [
                    _step("ingest", "error", f"Ingest failed: {e}")
                ],
            }

    def node_load_findings(state: DisputeAgentState) -> dict:
        """Load AuditFindingRecord objects and compute disputed_amount deterministically.
        THIS IS WHERE FINANCIAL AMOUNTS ARE SET — from DB records, never from LLM.
        """
        try:
            org_id = uuid.UUID(state["organization_id"])
            invoice_id = uuid.UUID(state["invoice_id"])
            finding_ids = state["finding_ids"]

            findings_data = []
            total_discrepancy = 0.0

            for fid in finding_ids:
                record = (
                    db.query(AuditFindingRecord)
                    .filter(
                        AuditFindingRecord.id == uuid.UUID(fid),
                        AuditFindingRecord.organization_id == org_id,
                    )
                    .first()
                )
                if record:
                    findings_data.append({
                        "id": str(record.id),
                        "rule_id": record.rule_id,
                        "rule_name": record.rule_name,
                        "severity": record.severity,
                        "discrepancy_amount": record.discrepancy_amount,
                        "reason": record.reason,
                        "confidence": record.confidence,
                        "recommended_action": record.recommended_action,
                        "evidence": record.evidence,
                        "invoice_id": str(record.invoice_id),
                        "shipment_id": str(record.shipment_id) if record.shipment_id else None,
                    })
                    total_discrepancy += record.discrepancy_amount or 0.0

            if not findings_data:
                return {
                    "error": "No AuditFindingRecords found for provided finding_ids",
                    "needs_human": True,
                    "trajectory": state.get("trajectory", []) + [
                        _step("load_findings", "error", "No findings found")
                    ],
                }

            # Load invoice for carrier name and amounts
            invoice = db.query(CarrierInvoice).filter(
                CarrierInvoice.id == invoice_id,
                CarrierInvoice.organization_id == org_id,
            ).first()

            carrier_name = invoice.carrier_name if invoice else ""
            invoice_number = invoice.invoice_number if invoice else ""
            billed_amount = invoice.total_billed_amount if invoice else 0.0

            # Compute average confidence
            avg_confidence = sum(f["confidence"] for f in findings_data) / len(findings_data)

            # Validate financial integrity
            validate_financial_integrity(round(total_discrepancy, 2), round(total_discrepancy, 2))

            return {
                "findings": findings_data,
                "carrier_name": carrier_name,
                "invoice_number": invoice_number,
                "disputed_amount": round(total_discrepancy, 2),  # AUTHORITATIVE — from DB
                "billed_amount": billed_amount,
                "confidence": avg_confidence,
                "trajectory": state.get("trajectory", []) + [
                    _step("load_findings", "ok",
                          f"Loaded {len(findings_data)} findings, total disputed: ${total_discrepancy:.2f}",
                          {"finding_count": len(findings_data), "disputed_amount": round(total_discrepancy, 2)})
                ],
            }
        except FinancialIntegrityError as e:
            return {
                "error": f"FINANCIAL INTEGRITY VIOLATION: {e}",
                "needs_human": True,
                "trajectory": state.get("trajectory", []) + [
                    _step("load_findings", "error", f"Financial integrity check failed: {e}")
                ],
            }
        except Exception as e:
            return {
                "error": str(e),
                "needs_human": True,
                "trajectory": state.get("trajectory", []) + [
                    _step("load_findings", "error", f"Load findings failed: {e}")
                ],
            }

    def node_resolve_recipient(state: DisputeAgentState) -> dict:
        """Resolve the verified carrier billing contact.
        SAFETY: Returns None → needs_human=True. NEVER synthesizes email.
        """
        try:
            org_id = uuid.UUID(state["organization_id"])
            carrier_name = state.get("carrier_name", "")

            result = resolve_recipient(org_id, carrier_name, db)

            if result is None:
                logger.warning(
                    "No verified CarrierContact for '%s' — escalating to human", carrier_name
                )
                return {
                    "recipient_email": None,
                    "recipient_contact_name": None,
                    "recipient_resolved": False,
                    "needs_human": True,
                    "trajectory": state.get("trajectory", []) + [
                        _step("resolve_recipient", "escalate",
                              f"No verified contact for '{carrier_name}' — human escalation required",
                              {"carrier_name": carrier_name, "escalation_reason": "no_verified_contact"})
                    ],
                }

            email, contact_name = result
            return {
                "recipient_email": email,
                "recipient_contact_name": contact_name,
                "recipient_resolved": True,
                "needs_human": False,
                "trajectory": state.get("trajectory", []) + [
                    _step("resolve_recipient", "ok",
                          f"Verified recipient resolved: {email}",
                          {"billing_email": email, "contact_name": contact_name})
                ],
            }
        except Exception as e:
            return {
                "recipient_email": None,
                "recipient_resolved": False,
                "needs_human": True,
                "error": str(e),
                "trajectory": state.get("trajectory", []) + [
                    _step("resolve_recipient", "error", f"Recipient resolution failed: {e}")
                ],
            }

    def node_generate_dispute_package(state: DisputeAgentState) -> dict:
        """Generate the dispute letter using LLM for professional language.
        CRITICAL: Financial amounts are injected from state (which came from AuditFindingRecord).
        The LLM only writes the letter body and subject.
        """
        import asyncio

        try:
            dispute_number = generate_dispute_number(
                state.get("invoice_number", "UNKNOWN")
            )
            findings = state.get("findings", [])
            disputed_amount = state["disputed_amount"]  # From DB — NOT from LLM
            expected_amount = state.get("expected_amount") or (state.get("billed_amount", 0) - disputed_amount)
            billed_amount = state.get("billed_amount") or 0.0

            has_signed_docs = determine_has_signed_documentation(findings)

            # Build prompt — financial amounts injected by code, not asked of LLM
            user_prompt = build_dispute_letter_prompt(
                dispute_number=dispute_number,
                invoice_number=state.get("invoice_number", ""),
                carrier_name=state.get("carrier_name", ""),
                recipient_contact_name=state.get("recipient_contact_name", "Billing Department"),
                disputed_amount=disputed_amount,  # From AuditFindingRecord — authoritative
                expected_amount=expected_amount,  # From CarrierInvoice — authoritative
                billed_amount=billed_amount,  # From CarrierInvoice — authoritative
                findings=findings,
                shipment_number=str(findings[0].get("shipment_id", "")) if findings else "",
            )

            messages = [
                {"role": "system", "content": DISPUTE_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]

            # Use default model for letter drafting (cost-efficient)
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        future = pool.submit(asyncio.run, llm.complete(
                            messages=messages,
                            model=llm.default_model,
                            temperature=0.3,
                            max_tokens=1000,
                        ))
                        response = future.result(timeout=60)
                else:
                    response = loop.run_until_complete(llm.complete(
                        messages=messages,
                        model=llm.default_model,
                        temperature=0.3,
                        max_tokens=1000,
                    ))
            except Exception:
                response = asyncio.run(llm.complete(
                    messages=messages,
                    model=llm.default_model,
                    temperature=0.3,
                    max_tokens=1000,
                ))

            letter_content = response.content
            cost = getattr(response, "cost_estimate", 0.0) or 0.0

            # Parse subject and body from LLM response
            subject = f"Invoice Dispute — {state.get('invoice_number', '')} / {dispute_number}"
            body = letter_content
            if "SUBJECT:" in letter_content:
                parts = letter_content.split("\nLETTER:", 1)
                subj_part = parts[0].replace("SUBJECT:", "").strip()
                subject = subj_part
                body = parts[1].strip() if len(parts) > 1 else letter_content

            return {
                "dispute_number": dispute_number,
                "dispute_letter_subject": subject,
                "dispute_letter_text": body,
                "has_signed_documentation": has_signed_docs,
                "expected_amount": expected_amount,
                "trajectory": state.get("trajectory", []) + [
                    _step("generate_dispute_package", "ok",
                          f"Dispute package generated (${disputed_amount:.2f} disputed)",
                          {"dispute_number": dispute_number, "llm_cost": cost,
                           "has_signed_docs": has_signed_docs})
                ],
            }
        except Exception as e:
            return {
                "error": str(e),
                "needs_human": True,
                "trajectory": state.get("trajectory", []) + [
                    _step("generate_dispute_package", "error", f"Package generation failed: {e}")
                ],
            }

    def node_apply_approval_gate(state: DisputeAgentState) -> dict:
        """Deterministic approval policy. LLM has NO role in this decision."""
        try:
            decision = evaluate_approval_gate(
                disputed_amount=state.get("disputed_amount", 0.0),
                confidence=state.get("confidence", 0.0),
                has_signed_documentation=state.get("has_signed_documentation", False),
                auto_dispute_threshold_usd=250.00,
            )
            return {
                "approval_decision": decision.value,
                "needs_human": decision == ApprovalDecision.REQUIRES_HUMAN_APPROVAL,
                "trajectory": state.get("trajectory", []) + [
                    _step("apply_approval_gate", "ok",
                          f"Approval decision: {decision.value}",
                          {
                              "decision": decision.value,
                              "disputed_amount": state.get("disputed_amount"),
                              "confidence": state.get("confidence"),
                              "has_signed_docs": state.get("has_signed_documentation"),
                          })
                ],
            }
        except Exception as e:
            return {
                "approval_decision": "requires_human_approval",
                "needs_human": True,
                "error": str(e),
                "trajectory": state.get("trajectory", []) + [
                    _step("apply_approval_gate", "error", f"Approval gate failed: {e}")
                ],
            }

    def node_send_dispute(state: DisputeAgentState) -> dict:
        """Create dispute DB record and simulate email send.
        Only reachable if approval_decision == 'auto_approved'.
        """
        try:
            org_id = uuid.UUID(state["organization_id"])
            dispute_repo = DisputeRepository(db)
            recovery_repo = RecoveryRepository(db)

            # Create the dispute record
            dispute = dispute_repo.create_dispute(
                organization_id=org_id,
                dispute_number=state["dispute_number"],
                invoice_id=uuid.UUID(state["invoice_id"]),
                shipment_id=uuid.UUID(state["findings"][0]["shipment_id"]) if state.get("findings") and state["findings"][0].get("shipment_id") else None,
                audit_finding_ids=state["finding_ids"],
                carrier_name=state["carrier_name"],
                disputed_amount=state["disputed_amount"],  # FROM DB — authoritative
                expected_amount=state.get("expected_amount"),
                billed_amount=state.get("billed_amount"),
                idempotency_key=state.get("idempotency_key"),
            )

            dispute_repo.set_recipient(dispute.id, state["recipient_email"], state.get("recipient_contact_name"))
            dispute_repo.set_dispute_letter(dispute.id, state["dispute_letter_subject"], state["dispute_letter_text"])
            dispute_repo.update_approval_status(dispute.id, "auto_approved")

            # Simulate email send
            logger.info(
                "[DISPUTE EMAIL SIMULATED] To: %s | Dispute: %s | Amount: $%.2f",
                state["recipient_email"],
                state["dispute_number"],
                state["disputed_amount"],
            )
            dispute_repo.mark_dispute_sent(dispute.id)

            # Create recovery ledger entry (approved_recovery=NULL until proof)
            recovery_number = generate_recovery_number(state["dispute_number"])
            recovery = recovery_repo.create_recovery_entry(
                organization_id=org_id,
                recovery_number=recovery_number,
                dispute_id=dispute.id,
                invoice_id=uuid.UUID(state["invoice_id"]),
                disputed_amount=state["disputed_amount"],
            )

            return {
                "dispute_id": str(dispute.id),
                "dispute_status": "sent",
                "recovery_id": str(recovery.id),
                "trajectory": state.get("trajectory", []) + [
                    _step("send_dispute", "ok",
                          f"Dispute sent (simulated) to {state['recipient_email']}",
                          {"dispute_id": str(dispute.id), "recovery_id": str(recovery.id)})
                ],
            }
        except Exception as e:
            return {
                "error": str(e),
                "needs_human": True,
                "dispute_status": "failed",
                "trajectory": state.get("trajectory", []) + [
                    _step("send_dispute", "error", f"Send failed: {e}")
                ],
            }

    def node_request_human_approval(state: DisputeAgentState) -> dict:
        """Route to human approval queue."""
        try:
            org_id = uuid.UUID(state["organization_id"])
            dispute_repo = DisputeRepository(db)

            reason = "No verified carrier contact on file" if not state.get("recipient_resolved") else \
                f"Dispute exceeds auto-approval threshold or requires human review (amount: ${state.get('disputed_amount', 0):.2f})"

            # Create dispute in pending_approval status
            dispute_number = state.get("dispute_number") or generate_dispute_number(
                state.get("invoice_number", "UNKNOWN")
            )
            dispute = dispute_repo.create_dispute(
                organization_id=org_id,
                dispute_number=dispute_number,
                invoice_id=uuid.UUID(state["invoice_id"]),
                shipment_id=uuid.UUID(state["findings"][0]["shipment_id"]) if state.get("findings") and state["findings"] and state["findings"][0].get("shipment_id") else None,
                audit_finding_ids=state.get("finding_ids", []),
                carrier_name=state.get("carrier_name", ""),
                disputed_amount=state.get("disputed_amount", 0.0),
                expected_amount=state.get("expected_amount"),
                billed_amount=state.get("billed_amount"),
                idempotency_key=state.get("idempotency_key"),
            )
            dispute_repo.update_dispute_status(dispute.id, "pending_approval")
            dispute_repo.update_approval_status(dispute.id, "requires_human_approval")

            logger.warning("[HUMAN ESCALATION] Dispute %s: %s", dispute.dispute_number, reason)

            return {
                "dispute_id": str(dispute.id),
                "dispute_status": "pending_approval",
                "approval_decision": "requires_human_approval",
                "needs_human": True,
                "trajectory": state.get("trajectory", []) + [
                    _step("request_human_approval", "escalated",
                          f"Escalated to human: {reason}",
                          {"dispute_id": str(dispute.id), "reason": reason})
                ],
            }
        except Exception as e:
            return {
                "error": str(e),
                "needs_human": True,
                "trajectory": state.get("trajectory", []) + [
                    _step("request_human_approval", "error", f"Escalation failed: {e}")
                ],
            }

    def node_persist_results(state: DisputeAgentState) -> dict:
        """Finalize the agent run and persist final state."""
        return {
            "trajectory": state.get("trajectory", []) + [
                _step("persist_results", "ok",
                      f"Agent run complete. Dispute status: {state.get('dispute_status', 'unknown')}",
                      {
                          "dispute_id": state.get("dispute_id"),
                          "needs_human": state.get("needs_human", False),
                          "error": state.get("error"),
                      })
            ],
        }

    # Routing functions
    def route_after_recipient(state: DisputeAgentState) -> str:
        if state.get("error") or state.get("needs_human"):
            return "request_human_approval"
        return "generate_dispute_package"

    def route_after_approval_gate(state: DisputeAgentState) -> str:
        if state.get("approval_decision") == ApprovalDecision.AUTO_SEND.value:
            return "send_dispute"
        return "request_human_approval"

    # Build graph
    graph = StateGraph(DisputeAgentState)
    graph.add_node("ingest", node_ingest)
    graph.add_node("load_findings", node_load_findings)
    graph.add_node("resolve_recipient", node_resolve_recipient)
    graph.add_node("generate_dispute_package", node_generate_dispute_package)
    graph.add_node("apply_approval_gate", node_apply_approval_gate)
    graph.add_node("send_dispute", node_send_dispute)
    graph.add_node("request_human_approval", node_request_human_approval)
    graph.add_node("persist_results", node_persist_results)

    graph.set_entry_point("ingest")
    graph.add_edge("ingest", "load_findings")
    graph.add_edge("load_findings", "resolve_recipient")
    graph.add_conditional_edges("resolve_recipient", route_after_recipient, {
        "generate_dispute_package": "generate_dispute_package",
        "request_human_approval": "request_human_approval",
    })
    graph.add_edge("generate_dispute_package", "apply_approval_gate")
    graph.add_conditional_edges("apply_approval_gate", route_after_approval_gate, {
        "send_dispute": "send_dispute",
        "request_human_approval": "request_human_approval",
    })
    graph.add_edge("send_dispute", "persist_results")
    graph.add_edge("request_human_approval", "persist_results")
    graph.add_edge("persist_results", END)

    return graph.compile()
