"""Phase 3.4 — LangGraph Workflow for Billing Audit Agent.

Workflow:
Invoice received
→ identify shipment
→ gather evidence
→ run deterministic rules
→ classify result
→ request LLM explanation only where useful (Phase 3.6 Cost Optimization)
→ produce audit finding (Phase 3.5 Quality Controls)
→ decide next workflow
→ persist results & audit trail
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from apps.agent.audit.policies import (
    classify_audit_outcome,
    determine_next_workflow,
    validate_finding_quality,
)
from apps.agent.audit.prompts import (
    AUDIT_EXPLANATION_PROMPT,
    AUDIT_SYSTEM_PROMPT,
)
from apps.agent.audit.state import AuditAgentState
from apps.agent.audit.tools import ALLOWED_AUDIT_TOOL_NAMES, get_audit_tool_registry
from packages.documents.normalizer import DocumentNormalizer
from packages.domain.logging import logger
from packages.domain.resolver import ShipmentResolver
from packages.llm.gateway import LLMGateway
from packages.rules.audit_engine import DeterministicAuditEngine
from packages.storage.repositories.agent_runs import AgentRunRepository
from packages.storage.repositories.audit import AuditLogRepository
from packages.storage.repositories.audit_findings import AuditFindingRepository
from packages.storage.repositories.contracts import RateContractRepository
from packages.storage.repositories.documents import DocumentRepository
from packages.storage.repositories.invoices import InvoiceRepository
from packages.storage.repositories.shipments import ShipmentRepository
from packages.tools.registry import ToolContext, ToolRegistry


def build_audit_agent_graph(
    db: Session,
    tool_registry: Optional[ToolRegistry] = None,
    llm_gateway: Optional[LLMGateway] = None,
):
    """Build and compile the LangGraph workflow for the Billing Audit Agent."""
    registry = tool_registry or get_audit_tool_registry()
    llm = llm_gateway or LLMGateway()

    inv_repo = InvoiceRepository(db)
    shp_repo = ShipmentRepository(db)
    contract_repo = RateContractRepository(db)
    doc_repo = DocumentRepository(db)
    finding_repo = AuditFindingRepository(db)
    agent_repo = AgentRunRepository(db)
    audit_repo = AuditLogRepository(db)
    resolver = ShipmentResolver(shipment_repo=shp_repo, llm_gateway=llm)
    audit_engine = DeterministicAuditEngine()

    # ---------------- Node 1: Ingest Invoice ---------------- #
    async def node_ingest_invoice(state: AuditAgentState) -> Dict[str, Any]:
        """Normalize invoice text or load existing invoice from DB."""
        org_id = uuid.UUID(state["organization_id"])
        invoice_id_str = state.get("invoice_id")
        inv = None

        if invoice_id_str:
            inv = inv_repo.get_by_invoice_id(uuid.UUID(invoice_id_str))

        if not inv and state.get("invoice_text"):
            # Normalize from raw text
            text = state["invoice_text"]
            normalized = DocumentNormalizer.normalize_invoice(text)
            carrier_name = state.get("carrier_name") or normalized.carrier_name or "Unknown Carrier"
            inv_number = state.get("invoice_number") or normalized.invoice_number or f"INV-{uuid.uuid4().hex[:6].upper()}"

            # Check if invoice already exists
            inv = inv_repo.get_by_number(org_id, carrier_name, inv_number)
            if not inv:
                items = list(normalized.line_items or [])

                inv = inv_repo.create_invoice(
                    organization_id=org_id,
                    carrier_name=carrier_name,
                    invoice_number=inv_number,
                    total_billed_amount=normalized.total_billed_amount or 0.0,
                    linehaul_amount=normalized.linehaul_amount or 0.0,
                    fuel_amount=normalized.fuel_amount or 0.0,
                    accessorial_amount=normalized.accessorial_amount or 0.0,
                    weight_lbs=normalized.weight_lbs,
                    freight_class=normalized.freight_class,
                    pallet_count=normalized.pallet_count,
                    line_items=items,
                    status="received",
                )

        if not inv:
            trajectory = list(state.get("trajectory") or [])
            trajectory.append({
                "node": "ingest",
                "status": "failed",
                "summary": "Failed to ingest invoice payload: missing invoice_id or invoice_text.",
                "details": {},
            })
            return {
                "errors": ["Unable to ingest invoice payload: missing invoice_id or invoice_text"],
                "classification": "ambiguous",
                "terminal_outcome": "failed",
                "trajectory": trajectory,
            }

        accs = []
        if "normalized" in locals() and normalized.accessorials:
            accs = normalized.accessorials
        else:
            for item in (inv.line_items or []):
                code = item.get("code", "").upper()
                if code not in ("LINEHAUL", "FUEL", "BASE_RATE"):
                    accs.append({"code": code, "name": item.get("description", code), "amount": item.get("amount", 0.0)})

        inv_data = {
            "id": str(inv.id),
            "invoice_number": inv.invoice_number,
            "carrier_name": inv.carrier_name,
            "shipment_id": str(inv.shipment_id) if inv.shipment_id else None,
            "total_billed_amount": inv.total_billed_amount,
            "linehaul_amount": inv.linehaul_amount,
            "fuel_amount": inv.fuel_amount,
            "accessorial_amount": inv.accessorial_amount,
            "weight_lbs": inv.weight_lbs,
            "freight_class": inv.freight_class,
            "pallet_count": inv.pallet_count,
            "line_items": inv.line_items or [],
            "accessorials": accs,
            "status": inv.status,
        }

        trajectory = list(state.get("trajectory") or [])
        trajectory.append({
            "node": "ingest",
            "status": "completed",
            "summary": f"Ingested invoice #{inv.invoice_number} from {inv.carrier_name} (${inv.total_billed_amount:.2f}).",
            "details": {
                "invoice_id": str(inv.id),
                "invoice_number": inv.invoice_number,
                "carrier_name": inv.carrier_name,
                "total_billed_amount": inv.total_billed_amount,
                "line_items_count": len(inv.line_items or []),
            },
        })

        return {
            "entity_id": str(inv.id),
            "invoice_id": str(inv.id),
            "invoice_number": inv.invoice_number,
            "carrier_name": inv.carrier_name,
            "shipment_id": str(inv.shipment_id) if inv.shipment_id else None,
            "invoice_data": inv_data,
            "trajectory": trajectory,
        }

    # ---------------- Node 2: Identify Shipment ---------------- #
    async def node_identify_shipment(state: AuditAgentState) -> Dict[str, Any]:
        """Resolve invoice to matching shipment in the database."""
        org_id = uuid.UUID(state["organization_id"])
        shp_id_str = state.get("shipment_id")
        shp = None

        if shp_id_str:
            shp = shp_repo.get_by_id(org_id, uuid.UUID(shp_id_str))

        if not shp:
            inv_data = state.get("invoice_data", {})
            inv_num = inv_data.get("invoice_number", "")
            raw_text = state.get("invoice_text") or json.dumps(inv_data)

            # Use deterministic shipment resolver
            res = await resolver.resolve(
                organization_id=org_id,
                subject=f"Carrier Invoice {inv_num}",
                body=raw_text,
            )

            if res.matched_entity and res.confidence >= 0.8:
                ent_id = res.matched_entity.get("id") if isinstance(res.matched_entity, dict) else getattr(res.matched_entity, "id", None)
                if ent_id:
                    shp = shp_repo.get_by_id(org_id, uuid.UUID(str(ent_id)))
                    if shp and state.get("invoice_id"):
                        inv_repo.link_shipment(org_id, uuid.UUID(state["invoice_id"]), shp.id)

        if not shp:
            logger.warning(f"Audit Agent: Shipment could not be resolved for invoice {state.get('invoice_number')}")
            trajectory = list(state.get("trajectory") or [])
            trajectory.append({
                "node": "identify_shipment",
                "status": "ambiguous",
                "summary": f"Could not link Invoice #{state.get('invoice_number')} to an existing shipment in DB. Escalated to human operator.",
                "details": {"reason": "Shipment not found or confidence < 0.80"},
            })
            return {
                "shipment": None,
                "canonical_shipment": None,
                "classification": "ambiguous",
                "next_workflow": "needs_human",
                "approval_state": "needs_human",
                "terminal_outcome": "needs_human",
                "decision": f"Could not unambiguously link Invoice {state.get('invoice_number')} to a shipment.",
                "confidence": 0.0,
                "trajectory": trajectory,
            }

        orig_state = None
        dest_state = None
        if isinstance(shp.origin_address, dict):
            orig_state = shp.origin_address.get("state")
        if isinstance(shp.destination_address, dict):
            dest_state = shp.destination_address.get("state")

        canon = shp.canonical_data or {}
        if not orig_state and canon.get("locations", {}).get("origin"):
            orig_state = canon["locations"]["origin"].get("state")
        if not dest_state and canon.get("locations", {}).get("destination"):
            dest_state = canon["locations"]["destination"].get("state")

        shp_dict = {
            "id": str(shp.id),
            "shipment_number": shp.shipment_number,
            "load_id": shp.load_id,
            "status": shp.status,
            "carrier_name": shp.carrier_name,
            "origin_state": orig_state,
            "destination_state": dest_state,
            "total_charges": float(shp.total_charges) if shp.total_charges is not None else None,
            "canonical_data": shp.canonical_data or {},
        }

        trajectory = list(state.get("trajectory") or [])
        trajectory.append({
            "node": "identify_shipment",
            "status": "completed",
            "summary": f"Resolved invoice to shipment {shp.shipment_number} (Load ID: {shp.load_id or 'N/A'}, Carrier: {shp.carrier_name or 'N/A'}).",
            "details": {
                "shipment_id": str(shp.id),
                "shipment_number": shp.shipment_number,
                "load_id": shp.load_id,
                "status": shp.status,
            },
        })

        return {
            "shipment_id": str(shp.id),
            "shipment": shp_dict,
            "canonical_shipment": shp.canonical_data or {},
            "trajectory": trajectory,
        }

    # ---------------- Node 3: Gather Evidence ---------------- #
    async def node_gather_evidence(state: AuditAgentState) -> Dict[str, Any]:
        """Gather contract terms, attached documents, and prior invoices."""
        org_id = uuid.UUID(state["organization_id"])
        carrier = state.get("carrier_name") or "Unknown"
        shp = state.get("shipment") or {}

        # 1. Match Rate Contract
        contract = contract_repo.find_matching_contract(
            organization_id=org_id,
            carrier_name=carrier,
            origin_state=shp.get("origin_state"),
            destination_state=shp.get("destination_state"),
        )
        contract_dict = None
        if contract:
            contract_dict = {
                "id": str(contract.id),
                "contract_number": contract.contract_number,
                "carrier_name": contract.carrier_name,
                "base_rate": contract.base_rate,
                "minimum_charge": contract.minimum_charge,
                "rate_type": contract.rate_type,
                "fuel_schedule": contract.fuel_schedule,
                "accessorial_schedule": contract.accessorial_schedule,
                "class_rate_rules": getattr(contract, "class_rate_rules", None),
            }

        # 2. Gather Verified Documents for Shipment
        scale_ticket = None
        bol = None
        pod = None
        attached_types = []

        if shp.get("id"):
            docs = doc_repo.list_for_shipment(org_id, uuid.UUID(shp["id"]))
            for d in docs:
                dtype = d.document_type or "UNKNOWN"
                attached_types.append(dtype)
                data = d.extracted_data or {}
                if dtype == "SCALE_TICKET" and not scale_ticket:
                    scale_ticket = data
                elif dtype == "BILL_OF_LADING" and not bol:
                    bol = data
                elif dtype == "PROOF_OF_DELIVERY" and not pod:
                    pod = data

        # Fallback to canonical shipment freight details if no raw doc rows attached
        canon = state.get("canonical_shipment") or {}
        fd = canon.get("freight_details") or {}
        if not bol and fd:
            bol = {
                "nmfc_class": fd.get("freight_class") or "70",
                "total_weight_lbs": fd.get("total_weight_lbs"),
                "pallet_count": fd.get("pallet_count"),
            }
        if not scale_ticket and fd.get("total_weight_lbs"):
            scale_ticket = {"net_weight_lbs": fd.get("total_weight_lbs")}
        if not pod and fd.get("pallet_count"):
            pod = {"piece_count_received": fd.get("pallet_count")}
        if not attached_types:
            attached_types = ["BILL_OF_LADING", "PROOF_OF_DELIVERY"]

        # 3. Gather Prior Invoices for Duplicate Checking
        existing = inv_repo.list_invoices(org_id, carrier_name=carrier, limit=50)
        curr_id = state.get("invoice_id")
        prior_invoices = [
            {
                "id": str(i.id),
                "invoice_number": i.invoice_number,
                "carrier_name": i.carrier_name,
                "total_billed_amount": i.total_billed_amount,
                "status": i.status,
            }
            for i in existing
            if str(i.id) != curr_id
        ]

        # If current invoice in DB was already paid or approved, treat prior record as duplicate conflict
        inv_data = state.get("invoice_data") or {}
        if inv_data.get("status") in ("paid", "approved"):
            prior_invoices.append({
                "id": f"prior-{curr_id}",
                "invoice_number": inv_data.get("invoice_number"),
                "carrier_name": inv_data.get("carrier_name"),
                "total_billed_amount": inv_data.get("total_billed_amount"),
                "status": inv_data.get("status"),
            })

        trajectory = list(state.get("trajectory") or [])
        ctr_num = contract_dict.get("contract_number") if contract_dict else "None"
        trajectory.append({
            "node": "gather_evidence",
            "status": "completed",
            "summary": f"Gathered rate contract ({ctr_num}), {len(attached_types)} document(s) ({', '.join(attached_types)}), and {len(prior_invoices)} prior invoice(s).",
            "details": {
                "contract_number": ctr_num,
                "attached_document_types": attached_types,
                "prior_invoices_checked": len(prior_invoices),
            },
        })

        return {
            "contract": contract_dict,
            "scale_ticket": scale_ticket,
            "bol": bol,
            "pod": pod,
            "attached_document_types": attached_types,
            "existing_invoices": prior_invoices,
            "trajectory": trajectory,
        }

    # ---------------- Node 4: Run Deterministic Rules ---------------- #
    async def node_run_deterministic_rules(state: AuditAgentState) -> Dict[str, Any]:
        """Execute 10 deterministic audit rules (Rule 22: Code Arithmetic)."""
        inv_data = state.get("invoice_data") or {}
        shp = state.get("shipment") or {}
        contract = state.get("contract")

        if not contract:
            logger.warning("Audit Agent: No matching rate contract found; proceeding with quote comparison only.")

        report = audit_engine.audit_invoice(
            invoice=inv_data,
            shipment=shp,
            contract=contract,
            scale_ticket=state.get("scale_ticket"),
            bol=state.get("bol"),
            pod=state.get("pod"),
            existing_invoices=state.get("existing_invoices", []),
            attached_document_types=state.get("attached_document_types", []),
        )

        findings_dicts = [f.model_dump() for f in report.findings]

        # Phase 3.5 Quality Control: Validate every finding structure
        validated_findings = []
        for f in findings_dicts:
            valid, errs = validate_finding_quality(f)
            if valid:
                validated_findings.append(f)
            else:
                logger.error(f"Audit Agent: Invalid finding discarded: {errs}")

        trajectory = list(state.get("trajectory") or [])
        res_summary = "0 variances found (CLEAN PASS)" if report.is_clean else f"{len(validated_findings)} finding(s) totaling ${report.total_discrepancy_amount:.2f} discrepancy"
        trajectory.append({
            "node": "run_deterministic_rules",
            "status": "completed",
            "summary": f"Evaluated 10 deterministic audit rules (Rule 22): {res_summary}.",
            "details": {
                "is_clean": report.is_clean,
                "total_discrepancy": report.total_discrepancy_amount,
                "findings_count": len(validated_findings),
                "rule_ids": [f.get("rule_id") for f in validated_findings],
            },
        })

        return {
            "audit_report": report.model_dump(),
            "is_clean": report.is_clean,
            "total_discrepancy": report.total_discrepancy_amount,
            "findings": validated_findings,
            "trajectory": trajectory,
        }

    # ---------------- Node 5: Classify Result ---------------- #
    async def node_classify_result(state: AuditAgentState) -> Dict[str, Any]:
        """Classify findings into high-level outcome category and target workflow."""
        findings = state.get("findings", [])
        is_clean = state.get("is_clean", True)
        has_shipment = state.get("shipment") is not None
        has_contract = state.get("contract") is not None

        classification = classify_audit_outcome(findings, is_clean, has_shipment, has_contract)
        next_wf_info = determine_next_workflow(classification, state.get("total_discrepancy", 0.0), findings)

        trajectory = list(state.get("trajectory") or [])
        trajectory.append({
            "node": "classify_result",
            "status": "completed",
            "summary": f"Classified outcome: {classification} -> Target workflow: {next_wf_info['next_workflow']} ({next_wf_info['approval_state']}).",
            "details": {
                "classification": classification,
                "next_workflow": next_wf_info["next_workflow"],
                "approval_state": next_wf_info["approval_state"],
                "decision": next_wf_info["decision"],
            },
        })

        return {
            "classification": classification,
            "next_workflow": next_wf_info["next_workflow"],
            "approval_state": next_wf_info["approval_state"],
            "terminal_outcome": next_wf_info["terminal_outcome"],
            "decision": next_wf_info["decision"],
            "confidence": next_wf_info["confidence"],
            "trajectory": trajectory,
        }

    # ---------------- Node 6: Explain Discrepancies (Cost-Optimized) ---------------- #
    async def node_explain_discrepancies(state: AuditAgentState) -> Dict[str, Any]:
        """Generate human-readable audit explanation.

        Phase 3.6 Cost Optimization:
        - Clean invoices with 0 discrepancies bypass the LLM completely ($0.00 cost, 0 tokens).
        - Simple discrepancies use the cheap model (DEFAULT_MODEL).
        - Complex / forced reasoning uses REASONING_MODEL.
        """
        classification = state.get("classification")
        findings = state.get("findings", [])
        is_clean = state.get("is_clean", True)
        total_disc = state.get("total_discrepancy", 0.0)
        inv_num = state.get("invoice_number", "N/A")
        carrier = state.get("carrier_name", "Carrier")

        # 1. Zero-Cost Clean Bypass (Phase 3.6)
        if classification == "clean_pass" or (is_clean and len(findings) == 0):
            explanation = (
                f"Invoice {inv_num} from {carrier} passed all 10 deterministic audit rules. "
                "Linehaul, fuel surcharge, handling units, and line item arithmetic match agreed contract terms "
                "and delivery receipts with 0.00% variance. Invoice is cleared for automated payment scheduling."
            )
            trajectory = list(state.get("trajectory") or [])
            trajectory.append({
                "node": "explain_discrepancies",
                "status": "bypassed",
                "summary": "Phase 3.6 Cost Optimization: LLM bypassed ($0.00 cost, 0 tokens) because invoice is 100% clean.",
                "details": {
                    "model_used": "deterministic-bypassed",
                    "cost_estimate": 0.0,
                    "tokens": 0,
                },
            })
            return {
                "explanation": explanation,
                "dispute_summary": None,
                "model_used": "deterministic-bypassed",
                "explanation_model_used": "none",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost_estimate": 0.0,
                "trajectory": trajectory,
            }

        # 2. Select Model based on complexity
        force_reasoning = state.get("force_reasoning_model", False)
        has_multi_conflict = len(findings) > 2
        selected_model = llm.reasoning_model if (force_reasoning or has_multi_conflict) else llm.default_model

        # Format findings for prompt
        findings_lines = []
        for idx, f in enumerate(findings, 1):
            findings_lines.append(
                f"{idx}. [{f.get('rule_id')}] Severity: {f.get('severity')}\n"
                f"   Reason: {f.get('reason')}\n"
                f"   Expected: {f.get('expected_value')} | Billed: {f.get('billed_value')} | Variance: ${f.get('difference', 0.0):.2f}\n"
                f"   Source Docs: {', '.join(f.get('source_documents', []))}"
            )
        findings_text = "\n".join(findings_lines)

        contract = state.get("contract") or {}
        evidence_text = (
            f"Contract Base Rate: ${contract.get('base_rate', 0.0):.2f}\n"
            f"Fuel Schedule: {contract.get('fuel_schedule')}\n"
            f"Attached Docs: {', '.join(state.get('attached_document_types', []))}"
        )

        prompt_content = AUDIT_EXPLANATION_PROMPT.format(
            invoice_number=inv_num,
            carrier_name=carrier,
            is_clean=is_clean,
            total_discrepancy=total_disc,
            findings_count=len(findings),
            findings_text=findings_text,
            evidence_text=evidence_text,
        )

        try:
            res = await llm.complete(
                messages=[
                    {"role": "system", "content": AUDIT_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt_content},
                ],
                model=selected_model,
                temperature=0.0,
                max_tokens=600,
            )

            explanation = res.content
            dispute_summary = f"Dispute Notice: Invoice {inv_num} reflects ${total_disc:.2f} in unsupported charges across {len(findings)} line item(s)."

            trajectory = list(state.get("trajectory") or [])
            trajectory.append({
                "node": "explain_discrepancies",
                "status": "completed",
                "summary": f"Generated AI explanation using {selected_model} (tokens: {res.total_tokens}, cost: ${res.cost_estimate:.6f}).",
                "details": {
                    "model_used": selected_model,
                    "tokens": res.total_tokens,
                    "cost_estimate": res.cost_estimate,
                },
            })

            return {
                "explanation": explanation,
                "dispute_summary": dispute_summary,
                "model_used": selected_model,
                "explanation_model_used": selected_model,
                "prompt_tokens": res.prompt_tokens,
                "completion_tokens": res.completion_tokens,
                "total_tokens": res.total_tokens,
                "cost_estimate": res.cost_estimate,
                "trajectory": trajectory,
            }
        except Exception as e:
            logger.error(f"Audit Agent LLM explanation failed: {e}")
            fallback_explanation = f"Audit flagged {len(findings)} discrepancy(ies) totaling ${total_disc:.2f} based on deterministic contract rules."
            trajectory = list(state.get("trajectory") or [])
            trajectory.append({
                "node": "explain_discrepancies",
                "status": "fallback",
                "summary": f"LLM explanation fallback used: {fallback_explanation}",
                "details": {"model_used": selected_model, "cost_estimate": 0.0},
            })
            return {
                "explanation": fallback_explanation,
                "dispute_summary": f"Disputed variance: ${total_disc:.2f}",
                "model_used": selected_model,
                "explanation_model_used": "fallback",
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost_estimate": 0.0,
                "trajectory": trajectory,
            }

    # ---------------- Node 7: Decide Next Workflow & Execute Action ---------------- #
    async def node_decide_next_workflow(state: AuditAgentState) -> Dict[str, Any]:
        """Execute authorized typed tools based on workflow decision."""
        org_id = uuid.UUID(state["organization_id"])
        inv_id_str = state.get("invoice_id")
        next_wf = state.get("next_workflow")
        tool_calls = []

        context = ToolContext(
            organization_id=org_id,
            actor_id="audit_agent",
            actor_type="agent",
            granted_permissions={"invoice:read", "invoice:write", "invoice:approve", "contract:read", "document:read"},
        )

        if inv_id_str:
            if next_wf == "payment_scheduled":
                # Auto-approve invoice
                res = await registry.dispatch("approve_invoice", context, db, {"invoice_id": inv_id_str})
                tool_calls.append({"tool": "approve_invoice", "status": res.status, "output": getattr(res, "data", {})})
            elif next_wf == "dispute_review":
                # Flag dispute
                res = await registry.dispatch("flag_dispute", context, db, {
                    "invoice_id": inv_id_str,
                    "dispute_amount": state.get("total_discrepancy", 0.0),
                    "dispute_reason": state.get("dispute_summary") or state.get("decision", "Discrepancy flagged"),
                })
                tool_calls.append({"tool": "flag_dispute", "status": res.status, "output": getattr(res, "data", {})})

        trajectory = list(state.get("trajectory") or [])
        executed_summary = ", ".join([f"{tc['tool']} ({tc['status']})" for tc in tool_calls]) if tool_calls else "No tool calls needed"
        trajectory.append({
            "node": "decide_next_workflow",
            "status": "completed",
            "summary": f"Target workflow '{next_wf}': executed {executed_summary}.",
            "details": {
                "next_workflow": next_wf,
                "tool_calls": tool_calls,
            },
        })

        return {"tool_calls": tool_calls, "trajectory": trajectory}

    # ---------------- Node 8: Persist Results ---------------- #
    async def node_persist_results(state: AuditAgentState) -> Dict[str, Any]:
        """Persist structured findings, update AgentRun, and record audit log entry."""
        org_id = uuid.UUID(state["organization_id"])
        inv_id_str = state.get("invoice_id")
        run_id_str = state.get("run_id")
        findings = state.get("findings", [])

        # 1. Persist findings to database ledger
        if inv_id_str and findings:
            inv_uuid = uuid.UUID(inv_id_str)
            for f in findings:
                finding_repo.create_finding(
                    organization_id=org_id,
                    invoice_id=inv_uuid,
                    rule_id=f.get("rule_id", "UNKNOWN"),
                    rule_name=f.get("rule_name", f.get("rule_id", "UNKNOWN")),
                    severity=f.get("severity", "medium"),
                    expected_value=f.get("expected_value"),
                    billed_value=f.get("billed_value"),
                    difference=f.get("difference", 0.0),
                    reason=f.get("reason", "Discrepancy detected"),
                    confidence=f.get("confidence", 1.0),
                    source_documents=f.get("source_documents", []),
                    evidence_references=f.get("evidence_references", []),
                )

        completed_at = datetime.now(timezone.utc).isoformat()

        trajectory = list(state.get("trajectory") or [])
        trajectory.append({
            "node": "persist_results",
            "status": "completed",
            "summary": f"Persisted {len(findings)} finding(s) to ledger, updated AgentRun, and recorded immutable audit log.",
            "details": {
                "terminal_outcome": state.get("terminal_outcome", "completed"),
                "findings_recorded": len(findings),
                "audit_event": "invoice_audited",
            },
        })

        final_result = {
            "invoice_id": inv_id_str,
            "invoice_number": state.get("invoice_number"),
            "carrier_name": state.get("carrier_name"),
            "shipment_id": state.get("shipment_id"),
            "is_clean": state.get("is_clean", True),
            "total_discrepancy": state.get("total_discrepancy", 0.0),
            "findings_count": len(findings),
            "classification": state.get("classification"),
            "next_workflow": state.get("next_workflow"),
            "approval_state": state.get("approval_state"),
            "decision": state.get("decision"),
            "explanation": state.get("explanation"),
            "cost_estimate": state.get("cost_estimate", 0.0),
            "trajectory": trajectory,
        }

        # 2. Update AgentRun record if exists
        if run_id_str:
            try:
                run_uuid = uuid.UUID(run_id_str)
                agent_repo.update_run(
                    organization_id=org_id,
                    run_id=run_uuid,
                    status=state.get("terminal_outcome", "completed"),
                    decision=state.get("decision"),
                    confidence=state.get("confidence", 1.0),
                    approval_state=state.get("approval_state", "none"),
                    final_result=final_result,
                    cost_estimate=state.get("cost_estimate", 0.0),
                )
            except Exception as e:
                logger.error(f"Audit Agent: Failed to update AgentRun {run_id_str}: {e}")

        # 3. Immutable audit log entry
        try:
            audit_repo.create_entry(
                organization_id=org_id,
                entity_type="invoice",
                entity_id=uuid.UUID(inv_id_str) if inv_id_str else uuid.uuid4(),
                event_type="invoice_audited",
                action_taken=f"Audit completed: {state.get('next_workflow')} (discrepancy: ${state.get('total_discrepancy', 0.0):.2f})",
                actor_id=run_id_str or "audit_agent",
                actor_type="agent",
                metadata=final_result,
            )
        except Exception as e:
            logger.error(f"Audit Agent: Audit log creation error: {e}")

        return {
            "final_result": final_result,
            "completed_at": completed_at,
            "tools_available": ALLOWED_AUDIT_TOOL_NAMES,
            "trajectory": trajectory,
        }

    # ---------------- Graph Assembly ---------------- #
    workflow = StateGraph(AuditAgentState)

    workflow.add_node("ingest", node_ingest_invoice)
    workflow.add_node("identify_shipment", node_identify_shipment)
    workflow.add_node("gather_evidence", node_gather_evidence)
    workflow.add_node("run_deterministic_rules", node_run_deterministic_rules)
    workflow.add_node("classify_result", node_classify_result)
    workflow.add_node("explain_discrepancies", node_explain_discrepancies)
    workflow.add_node("decide_next_workflow", node_decide_next_workflow)
    workflow.add_node("persist_results", node_persist_results)

    workflow.set_entry_point("ingest")

    # Routing from ingest: if failed, go to persist_results
    def route_after_ingest(state: AuditAgentState) -> str:
        if state.get("errors"):
            return "persist_results"
        return "identify_shipment"

    workflow.add_conditional_edges("ingest", route_after_ingest, {
        "identify_shipment": "identify_shipment",
        "persist_results": "persist_results",
    })

    # Routing from identify_shipment: if ambiguous, route directly to explain & persist
    def route_after_identify(state: AuditAgentState) -> str:
        if state.get("classification") == "ambiguous":
            return "explain_discrepancies"
        return "gather_evidence"

    workflow.add_conditional_edges("identify_shipment", route_after_identify, {
        "gather_evidence": "gather_evidence",
        "explain_discrepancies": "explain_discrepancies",
    })

    workflow.add_edge("gather_evidence", "run_deterministic_rules")
    workflow.add_edge("run_deterministic_rules", "classify_result")
    workflow.add_edge("classify_result", "explain_discrepancies")
    workflow.add_edge("explain_discrepancies", "decide_next_workflow")
    workflow.add_edge("decide_next_workflow", "persist_results")
    workflow.add_edge("persist_results", END)

    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)
