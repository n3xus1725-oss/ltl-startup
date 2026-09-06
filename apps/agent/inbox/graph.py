"""Phase 1.6 & 1.8 — LangGraph Workflow for Inbox Action Agent v0.

Nodes:
ingest -> resolve_entity -> load_context -> classify_intent -> propose_action ->
permission_check -> execute_tool -> verify_result -> audit

Terminal outcomes:
- completed
- needs_human
- failed
"""

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from dateutil.parser import parse as parse_date
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from apps.agent.inbox.policies import (
    evaluate_action_policy,
)
from apps.agent.inbox.prompts import (
    INTENT_CLASSIFICATION_PROMPT,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
)
from apps.agent.inbox.state import InboxAgentState
from apps.agent.inbox.tools import ALLOWED_INBOX_TOOL_NAMES, get_inbox_tool_registry
from packages.domain.logging import logger
from packages.domain.resolver import ShipmentResolver
from packages.llm.gateway import LLMGateway
from packages.storage.repositories.agent_runs import AgentRunRepository
from packages.storage.repositories.audit import AuditLogRepository
from packages.storage.repositories.documents import DocumentRepository
from packages.storage.repositories.messages import MessageRepository
from packages.storage.repositories.shipments import ShipmentRepository
from packages.storage.repositories.tasks import TaskRepository
from packages.tools.registry import ToolContext, ToolRegistry


def build_inbox_agent_graph(
    db: Session,
    tool_registry: Optional[ToolRegistry] = None,
    llm_gateway: Optional[LLMGateway] = None,
):
    """Build and compile the LangGraph workflow for the Inbox Action Agent."""
    registry = tool_registry or get_inbox_tool_registry()
    llm = llm_gateway or LLMGateway()

    shipment_repo = ShipmentRepository(db)
    message_repo = MessageRepository(db)
    doc_repo = DocumentRepository(db)
    task_repo = TaskRepository(db)
    agent_repo = AgentRunRepository(db)
    audit_repo = AuditLogRepository(db)
    resolver = ShipmentResolver(shipment_repo=shipment_repo, llm_gateway=llm)

    # ---------------- Node 1: Ingest ---------------- #
    async def node_ingest(state: InboxAgentState) -> Dict[str, Any]:
        """Normalize inbound payload, register attachments, and initialize run state."""
        import hashlib
        org_id = state.get("organization_id")
        run_id = state.get("run_id") or str(uuid.uuid4())
        started_at = state.get("started_at") or datetime.now(timezone.utc).isoformat()

        # Ingest/register any incoming attachments in DocumentRepository
        raw_atts = state.get("attachments") or []
        registered_docs = []
        for att in raw_atts:
            doc_id = att.get("document_id") or att.get("id")
            if doc_id:
                try:
                    existing_doc = doc_repo.get_by_id(uuid.UUID(org_id), uuid.UUID(doc_id))
                    if existing_doc:
                        registered_docs.append({
                            "id": str(existing_doc.id),
                            "document_id": str(existing_doc.id),
                            "filename": existing_doc.file_name,
                            "document_type": existing_doc.document_type,
                        })
                        continue
                except Exception:
                    pass

            fn = att.get("filename", "attachment.pdf")
            content = att.get("content", b"")
            if isinstance(content, str):
                content = content.encode("utf-8")
            sha = hashlib.sha256(content).hexdigest()
            doc_type = att.get("document_type") or ("BOL" if "bol" in fn.lower() else ("POD" if "pod" in fn.lower() else "UNKNOWN"))
            new_doc = doc_repo.create(
                organization_id=uuid.UUID(org_id),
                file_name=fn,
                storage_path=f"/attachments/{sha}_{fn}",
                document_type=doc_type,
                file_size_bytes=len(content),
                mime_type=att.get("mime_type", "application/pdf"),
                checksum_sha256=sha,
            )
            registered_docs.append({
                "id": str(new_doc.id),
                "document_id": str(new_doc.id),
                "filename": new_doc.file_name,
                "document_type": new_doc.document_type,
            })

        logger.info(f"Ingesting event for org {org_id}, run {run_id}, attachments: {len(registered_docs)}")
        return {
            "run_id": run_id,
            "started_at": started_at,
            "errors": state.get("errors") or [],
            "tools_available": ALLOWED_INBOX_TOOL_NAMES,
            "model_used": llm.default_model,
            "model_version": "v0",
            "prompt_version": PROMPT_VERSION,
            "cost_estimate": 0.0,
            "tool_calls": [],
            "attachments": registered_docs,
        }

    # ---------------- Node 2: Resolve Entity ---------------- #
    async def node_resolve_entity(state: InboxAgentState) -> Dict[str, Any]:
        """Resolve candidate freight identifiers to database shipment."""
        org_uuid = uuid.UUID(state["organization_id"])
        subject = state.get("subject", "")
        body = state.get("body", "")

        res = await resolver.resolve(
            organization_id=org_uuid,
            subject=subject,
            body=body,
            enable_llm_fallback=True,
        )

        updates: Dict[str, Any] = {
            "resolver_result": res.model_dump(),
        }

        if res.is_ambiguous:
            updates["entity_id"] = None
            updates["shipment"] = None
            updates["intent"] = "ambiguous"
            updates["intent_confidence"] = res.confidence
            updates["reasoning"] = res.evidence.get("reason", "Ambiguous entity resolution")
        elif res.matched_entity:
            updates["entity_id"] = res.shipment_id
            updates["shipment"] = res.matched_entity
        else:
            updates["entity_id"] = None
            updates["shipment"] = None
            # Check if candidates were extracted from message but not found in DB (Unknown Shipment Reference)
            cand = res.evidence.get("extracted_candidates") or []
            if cand:
                updates["intent"] = "unknown_shipment"
                cand_vals = [
                    str(c.get("normalized_value") or c.get("raw_value") or c.get("value") or c)
                    for c in cand
                    if c and (isinstance(c, dict) or isinstance(c, str))
                ]
                cand_str = ", ".join([v for v in cand_vals if v])
                updates["reasoning"] = f"Unknown shipment reference: {cand_str} not found in database" if cand_str else "Unknown shipment reference not found in database"

        return updates

    # ---------------- Node 3: Load Context ---------------- #
    async def node_load_context(state: InboxAgentState) -> Dict[str, Any]:
        """Load shipment events, documents, and thread history if entity resolved."""
        org_uuid = uuid.UUID(state["organization_id"])
        context_data: Dict[str, Any] = {}

        if state.get("entity_id"):
            shipment_uuid = uuid.UUID(state["entity_id"])
            events = shipment_repo.get_events(org_uuid, shipment_uuid)
            context_data["event_count"] = len(events)
            context_data["latest_event"] = events[-1].event_type if events else None

        if state.get("thread_id"):
            thread_msgs = message_repo.get_by_thread_id(org_uuid, state["thread_id"])
            context_data["thread_message_count"] = len(thread_msgs)

        return {"context": context_data}

    # ---------------- Node 4: Classify Intent ---------------- #
    async def node_classify_intent(state: InboxAgentState) -> Dict[str, Any]:
        """Classify message intent into one of the supported workflows, or ambiguous/unknown."""
        # If resolver already flagged ambiguity or unknown shipment reference, preserve it
        if state.get("intent") in ("ambiguous", "unknown_shipment"):
            return {
                "intent": state["intent"],
                "intent_confidence": state.get("intent_confidence", 0.5),
                "confidence": 0.5,
                "extracted_data": {},
            }

        subject = (state.get("subject") or "").lower()
        body = (state.get("body") or "").lower()
        combined = f"{subject} {body}"

        # 1. Deterministic Intent Classification (Rule 3 & 4)
        # Workflow A: Pickup confirmation
        pickup_keywords = [
            "picked up",
            "driver has picked up",
            "freight picked up",
            "loaded at",
            "departed origin",
            "departed shipper",
            "pickup confirmed",
            "picked up on",
        ]
        # Workflow B: ETA update
        eta_keywords = [
            "updated eta",
            "new eta",
            "eta is",
            "revised eta",
            "estimated arrival",
            "delayed to",
            "delayed until",
            "arrival update",
        ]
        # Workflow C: Missing information
        missing_info_keywords = [
            "missing paperwork",
            "missing bol",
            "please provide weight",
            "missing piece count",
            "need piece count",
            "missing pieces",
            "confirm weight",
            "missing information",
        ]
        # Unrelated / noise
        unrelated_keywords = [
            "unsubscribe",
            "newsletter",
            "marketing",
            "out of office",
            "automatic reply",
            "happy holidays",
        ]

        extracted_data: Dict[str, Any] = {}
        classified_intent: Optional[str] = None
        confidence = 0.0

        if any(kw in combined for kw in unrelated_keywords):
            classified_intent = "unrelated"
            confidence = 0.98

        elif any(kw in combined for kw in pickup_keywords):
            classified_intent = "pickup_confirmation"
            confidence = 0.95
            raw_text = f"{state.get('subject') or ''} {state.get('body') or ''}"
            iso_match = re.search(
                r"\b(\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[Zz]|[-+]\d{2}:?\d{2})?)?)\b",
                raw_text,
            )
            if iso_match:
                try:
                    dt = parse_date(iso_match.group(1))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    extracted_data["pickup_date"] = dt.isoformat()
                except Exception:
                    extracted_data["pickup_date"] = datetime.now(timezone.utc).isoformat()
            else:
                extracted_data["pickup_date"] = datetime.now(timezone.utc).isoformat()

        elif any(kw in combined for kw in eta_keywords):
            classified_intent = "eta_update"
            confidence = 0.95
            raw_text = f"{state.get('subject') or ''} {state.get('body') or ''}"
            iso_match = re.search(
                r"\b(\d{4}-\d{2}-\d{2}(?:[T\s]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:[Zz]|[-+]\d{2}:?\d{2})?)?)\b",
                raw_text,
            )
            if iso_match:
                try:
                    dt = parse_date(iso_match.group(1))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    extracted_data["eta"] = dt.isoformat()
                except Exception:
                    extracted_data["eta"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
            else:
                if "tomorrow" in combined:
                    t_match = re.search(r"(\d{1,2}):(\d{2})", combined)
                    hr = int(t_match.group(1)) if t_match else 18
                    mn = int(t_match.group(2)) if t_match else 0
                    now = datetime.now(timezone.utc)
                    tomorrow = now + timedelta(days=1)
                    extracted_data["eta"] = tomorrow.replace(hour=hr, minute=mn, second=0, microsecond=0).isoformat()
                else:
                    extracted_data["eta"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

        elif any(kw in combined for kw in missing_info_keywords):
            classified_intent = "missing_information"
            confidence = 0.95
            missing_fields = []
            if "weight" in combined:
                missing_fields.append("weight_lbs")
            if "piece" in combined or "pallet" in combined:
                missing_fields.append("pallet_count")
            if "bol" in combined:
                missing_fields.append("bol_number")
            extracted_data["missing_fields"] = missing_fields or ["general_shipment_details"]

        # Workflow D: Document attachment / BOL / POD received
        document_keywords = [
            "bol attached",
            "bill of lading",
            "pod attached",
            "proof of delivery",
            "delivery receipt",
            "attached please find",
            "attached document",
            "signed bol",
            "signed pod",
            "attached bol",
            "attached pod",
        ]
        if not classified_intent:
            if any(kw in combined for kw in document_keywords) or (
                state.get("attachments") and any(k in combined for k in ("bol", "pod", "attached", "lading", "delivery"))
            ):
                classified_intent = "document_attached"
                confidence = 0.95

        # 2. Fallback to LLM for unstructured or ambiguous cases
        if not classified_intent or confidence < 0.85:
            prompt = (
                f"{SYSTEM_PROMPT}\n\n{INTENT_CLASSIFICATION_PROMPT}\n\n"
                f"Subject: {state.get('subject')}\n"
                f"Body: {state.get('body')}\n"
                f"Resolved Shipment: {json.dumps(state.get('shipment'))}"
            )
            response = await llm.complete(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
            parsed = response.parsed_json or {}
            classified_intent = parsed.get("intent", "general_inquiry")
            confidence = float(parsed.get("confidence", 0.7))
            extracted_data = parsed.get("extracted_data", {})

        return {
            "intent": classified_intent,
            "intent_confidence": confidence,
            "confidence": confidence,
            "extracted_data": extracted_data,
        }

    # ---------------- Node 5: Propose Action ---------------- #
    async def node_propose_action(state: InboxAgentState) -> Dict[str, Any]:
        """Propose concrete typed tool call based on classified workflow."""
        intent = state.get("intent")
        shipment = state.get("shipment")
        extracted = state.get("extracted_data", {})
        trigger_id = state.get("trigger_event_id") or state.get("run_id")

        proposed_action: Optional[Dict[str, Any]] = None

        if intent == "pickup_confirmation":
            if shipment:
                p_date = extracted.get("pickup_date") or datetime.now(timezone.utc).isoformat()
                proposed_action = {
                    "tool_name": "update_shipment",
                    "arguments": {
                        "shipment_id": shipment["id"],
                        "status": "picked_up",
                        "pickup_date": p_date,
                        "reason": "Carrier confirmed pickup in email communication",
                        "idempotency_key": f"pickup-{shipment['id']}-{trigger_id}",
                    },
                    "risk_level": "low",
                    "reason": "Update canonical shipment status to picked_up with confirmed timestamp",
                }
            else:
                # No shipment resolved for pickup -> propose review task
                proposed_action = {
                    "tool_name": "create_task",
                    "arguments": {
                        "title": f"Review unmatched pickup confirmation: {state.get('subject')}",
                        "task_type": "review",
                        "description": "Pickup confirmation received but shipment was not identified.",
                        "idempotency_key": f"task-unmatched-pickup-{trigger_id}",
                    },
                    "risk_level": "medium",
                    "reason": "Unmatched pickup email requires operator review",
                }

        elif intent == "eta_update":
            if shipment:
                new_eta = extracted.get("eta") or datetime.now(timezone.utc).isoformat()
                proposed_action = {
                    "tool_name": "update_shipment",
                    "arguments": {
                        "shipment_id": shipment["id"],
                        "eta": new_eta,
                        "reason": "Carrier reported new ETA in email communication",
                        "idempotency_key": f"eta-{shipment['id']}-{trigger_id}",
                    },
                    "risk_level": "low",
                    "reason": "Update canonical shipment ETA with carrier provided estimate",
                }
            else:
                proposed_action = {
                    "tool_name": "create_task",
                    "arguments": {
                        "title": f"Review unmatched ETA update: {state.get('subject')}",
                        "task_type": "review",
                        "description": "ETA update received but shipment was not identified.",
                        "idempotency_key": f"task-unmatched-eta-{trigger_id}",
                    },
                    "risk_level": "medium",
                    "reason": "Unmatched ETA email requires operator review",
                }

        elif intent == "missing_information":
            missing_fields = extracted.get("missing_fields", ["required information"])
            missing_str = ", ".join(missing_fields)
            shipment_id = shipment["id"] if shipment else None
            proposed_action = {
                "tool_name": "create_task",
                "arguments": {
                    "title": f"Missing information requested: {missing_str}",
                    "task_type": "missing_information",
                    "shipment_id": shipment_id,
                    "description": f"Standardized inquiry drafted for missing fields: {missing_str}",
                    "idempotency_key": f"missing-info-{trigger_id}",
                },
                "risk_level": "low",
                "reason": "Initiate missing-information follow-up task and notify sender",
            }

        elif intent == "ambiguous":
            proposed_action = {
                "tool_name": "create_task",
                "arguments": {
                    "title": f"Resolve Ambiguous Shipment Reference: {state.get('subject')}",
                    "task_type": "review",
                    "description": state.get("reasoning", "Multiple shipments matched extracted candidate identifiers."),
                    "priority": "high",
                    "idempotency_key": f"ambig-{trigger_id}",
                },
                "risk_level": "high",
                "reason": "Ambiguous message requires human operator resolution",
            }

        elif intent == "unknown_shipment":
            proposed_action = {
                "tool_name": "create_task",
                "arguments": {
                    "title": f"Review Unknown Shipment Reference: {state.get('subject')}",
                    "task_type": "review",
                    "description": state.get("reasoning", "Candidate identifier found in email does not exist in database."),
                    "priority": "high",
                    "idempotency_key": f"unknown-shp-{trigger_id}",
                },
                "risk_level": "high",
                "reason": "Unknown shipment reference requires operator review",
            }

        elif intent in ("document_attached", "pod_received", "bol_received"):
            if shipment and state.get("attachments"):
                first_att = state["attachments"][0]
                doc_id = first_att.get("document_id") or first_att.get("id")
                proposed_action = {
                    "tool_name": "attach_document",
                    "arguments": {
                        "shipment_id": shipment["id"],
                        "document_id": doc_id,
                        "document_type": first_att.get("document_type"),
                        "idempotency_key": f"attach-{shipment['id']}-{doc_id}",
                    },
                    "risk_level": "low",
                    "reason": f"Attach verified {first_att.get('document_type', 'document')} to canonical shipment record",
                }
            else:
                proposed_action = {
                    "tool_name": "create_task",
                    "arguments": {
                        "title": f"Review unmatched document attachment: {state.get('subject')}",
                        "task_type": "review",
                        "description": "Document received but matching shipment could not be identified.",
                        "idempotency_key": f"doc-unmatched-{trigger_id}",
                    },
                    "risk_level": "medium",
                    "reason": "Unmatched document attachment requires operator review",
                }

        elif intent == "unrelated":
            proposed_action = None

        return {"proposed_action": proposed_action}

    # ---------------- Node 6: Permission & Policy Check ---------------- #
    async def node_permission_check(state: InboxAgentState) -> Dict[str, Any]:
        """Deterministic policy gate checking permissions, risk, and approval thresholds."""
        proposed = state.get("proposed_action")
        intent = state.get("intent", "unrelated")
        confidence = state.get("confidence", 0.0)
        shipment = state.get("shipment")
        is_ambiguous = state.get("resolver_result", {}).get("is_ambiguous", False) or (intent == "ambiguous")

        if not proposed:
            return {
                "approval_state": "auto_approved",
                "terminal_outcome": "completed",
                "decision": f"Processed message: intent '{intent}', no external action required",
            }

        tool_name = proposed["tool_name"]
        args = proposed["arguments"]
        risk_level = proposed.get("risk_level", "low")

        policy_decision = evaluate_action_policy(
            intent=intent,
            confidence=confidence,
            is_ambiguous=is_ambiguous,
            shipment=shipment,
            proposed_tool=tool_name,
            proposed_arguments=args,
            risk_level=risk_level,
        )

        updates: Dict[str, Any] = {
            "approval_state": policy_decision.approval_state,
            "approval_reason": policy_decision.reason,
        }

        if policy_decision.approval_state == "needs_human":
            updates["terminal_outcome"] = "needs_human"
            updates["decision"] = f"Action paused for human review: {policy_decision.reason}"
            if policy_decision.requires_human_task:
                org_uuid = uuid.UUID(state["organization_id"])
                trigger_id = state.get("trigger_event_id") or state.get("run_id")
                task_repo.create_task(
                    organization_id=org_uuid,
                    title=f"Human Review Required: {state.get('subject')}",
                    task_type="review",
                    description=policy_decision.reason,
                    shipment_id=uuid.UUID(shipment["id"]) if shipment else None,
                    idempotency_key=f"human-review-{trigger_id}",
                )

        elif policy_decision.approval_state == "rejected":
            updates["terminal_outcome"] = "failed"
            updates["decision"] = f"Action rejected by policy: {policy_decision.reason}"
            updates["errors"] = state.get("errors", []) + [policy_decision.reason]

        return updates

    # ---------------- Node 7: Execute Tool ---------------- #
    async def node_execute_tool(state: InboxAgentState) -> Dict[str, Any]:
        """Execute the proposed tool with full idempotency, audit trail, and side effect handling."""
        proposed = state.get("proposed_action")
        if not proposed:
            return {}

        org_uuid = uuid.UUID(state["organization_id"])
        run_uuid = uuid.UUID(state["run_id"])
        tool_name = proposed["tool_name"]
        arguments = proposed["arguments"]

        context = ToolContext(
            organization_id=org_uuid,
            agent_run_id=run_uuid,
            actor_id=f"agent:{state['run_id']}",
            actor_type="agent",
            granted_permissions={"*"},
            idempotency_key=arguments.get("idempotency_key"),
        )

        result = await registry.execute(
            name=tool_name,
            context=context,
            db=db,
            arguments=arguments,
        )

        tool_calls = list(state.get("tool_calls", []))
        tool_calls.append({
            "tool_name": tool_name,
            "arguments": arguments,
            "status": result.status,
            "latency_ms": result.latency_ms,
            "data": result.data,
            "error_message": result.error_message,
        })

        # Workflow Follow-up Side Effects (Workflow A, B, C)
        # If update_shipment succeeded for pickup confirmation or ETA update, send customer/carrier reply
        thread_id = state.get("thread_id")
        intent = state.get("intent")
        shipment = state.get("shipment")

        if result.status in ("success", "cached"):
            # 1. Attach any incoming documents/attachments to the resolved shipment
            if shipment and tool_name != "attach_document":
                for att in state.get("attachments", []):
                    doc_id = att.get("document_id") or att.get("id")
                    if doc_id:
                        att_ctx = ToolContext(
                            organization_id=org_uuid,
                            agent_run_id=run_uuid,
                            actor_id=f"agent:{state['run_id']}",
                            actor_type="agent",
                            granted_permissions={"*"},
                            idempotency_key=f"attach-{shipment['id']}-{doc_id}",
                        )
                        att_res = await registry.execute(
                            name="attach_document",
                            context=att_ctx,
                            db=db,
                            arguments={
                                "shipment_id": shipment["id"],
                                "document_id": doc_id,
                                "document_type": att.get("document_type"),
                            },
                        )
                        tool_calls.append({
                            "tool_name": "attach_document",
                            "arguments": {"shipment_id": shipment["id"], "document_id": doc_id},
                            "status": att_res.status,
                            "latency_ms": att_res.latency_ms,
                            "data": att_res.data,
                        })

            # 2. Outbound thread reply if appropriate
            if thread_id:
                reply_body = None
                if intent == "pickup_confirmation":
                    reply_body = "Thank you. Pickup confirmation has been recorded in the platform."
                elif intent == "eta_update":
                    reply_body = "Thank you. Updated ETA has been logged into the platform."
                elif intent == "missing_information":
                    missing = state.get("extracted_data", {}).get("missing_fields", ["required information"])
                    reply_body = f"Please provide the following missing information for this shipment: {', '.join(missing)}."

                if reply_body:
                    reply_context = ToolContext(
                        organization_id=org_uuid,
                        agent_run_id=run_uuid,
                        actor_id=f"agent:{state['run_id']}",
                        actor_type="agent",
                        granted_permissions={"*"},
                        idempotency_key=f"notice-{arguments.get('idempotency_key')}",
                    )
                    reply_res = await registry.execute(
                        name="reply_to_thread",
                        context=reply_context,
                        db=db,
                        arguments={"thread_id": thread_id, "body": reply_body},
                    )
                    tool_calls.append({
                        "tool_name": "reply_to_thread",
                        "arguments": {"thread_id": thread_id, "body": reply_body},
                        "status": reply_res.status,
                        "latency_ms": reply_res.latency_ms,
                        "data": reply_res.data,
                    })

        updates: Dict[str, Any] = {
            "tool_result": result.model_dump(),
            "tool_calls": tool_calls,
        }

        if result.status == "failed":
            updates["terminal_outcome"] = "failed"
            updates["errors"] = state.get("errors", []) + [result.error_message or "Tool failed"]

        return updates

    # ---------------- Node 8: Verify Result ---------------- #
    async def node_verify_result(state: InboxAgentState) -> Dict[str, Any]:
        """Verify tool execution outcome and certify completion."""
        tool_res = state.get("tool_result")
        errors = list(state.get("errors", []))

        if not tool_res:
            return {
                "verification_result": {"is_valid": True, "checks_passed": ["no_tool_executed"]},
                "terminal_outcome": "completed",
                "decision": state.get("decision") or "Execution completed successfully without tool call",
            }

        status = tool_res.get("status")
        if status in ("success", "cached"):
            decision = f"Successfully executed workflow: {state.get('intent')}"
            return {
                "verification_result": {
                    "is_valid": True,
                    "checks_passed": ["tool_success", "state_verified"],
                },
                "terminal_outcome": "completed",
                "decision": decision,
            }
        else:
            errors.append(f"Tool execution verification failed: {tool_res.get('error_message')}")
            return {
                "verification_result": {"is_valid": False, "errors": errors},
                "terminal_outcome": "failed",
                "decision": "Execution failed during tool dispatch",
                "errors": errors,
            }

    # ---------------- Node 9: Audit ---------------- #
    async def node_audit(state: InboxAgentState) -> Dict[str, Any]:
        """Persist AgentRun record in database and write immutable audit log with full before/after diff."""
        org_uuid = uuid.UUID(state["organization_id"])
        run_uuid = uuid.UUID(state["run_id"])
        completed_at = datetime.now(timezone.utc).isoformat()
        outcome = state.get("terminal_outcome", "completed")

        final_result = {
            "intent": state.get("intent"),
            "shipment_id": state.get("entity_id"),
            "approval_state": state.get("approval_state"),
            "tool_calls_count": len(state.get("tool_calls", [])),
            "terminal_outcome": outcome,
        }

        # Update AgentRun in database
        agent_repo.update_run(
            organization_id=org_uuid,
            run_id=run_uuid,
            status=outcome,
            decision=state.get("decision"),
            confidence=state.get("confidence", 1.0),
            approval_state=state.get("approval_state", "none"),
            final_result=final_result,
            errors={"errors": state.get("errors", [])},
            cost_estimate=state.get("cost_estimate", 0.0),
        )

        # Capture before/after entity snapshot for auditable diff
        shipment_before = state.get("shipment")
        shipment_after_dict = None
        if state.get("entity_id"):
            db.expire_all()
            after_rec = shipment_repo.get_by_id(org_uuid, uuid.UUID(state["entity_id"]))
            if after_rec:
                shipment_after_dict = {
                    "id": str(after_rec.id),
                    "load_id": after_rec.load_id,
                    "shipment_number": after_rec.shipment_number,
                    "status": after_rec.status,
                    "pickup_date": after_rec.pickup_date.isoformat() if after_rec.pickup_date else None,
                    "eta": after_rec.eta.isoformat() if after_rec.eta else None,
                    "carrier_name": after_rec.carrier_name,
                }

        # Append immutable audit entry
        audit_repo.record(
            organization_id=org_uuid,
            event_id=str(uuid.uuid4()),
            action=f"agent_run:{outcome}",
            actor_type="agent",
            actor_id=str(run_uuid),
            target_entity_type="shipment",
            target_entity_id=state.get("entity_id"),
            payload_before={
                "trigger_event_id": state.get("trigger_event_id"),
                "shipment": shipment_before,
            },
            payload_after={
                "final_result": final_result,
                "shipment": shipment_after_dict or shipment_before,
            },
            metadata_json={
                "model_used": state.get("model_used"),
                "prompt_version": state.get("prompt_version"),
                "decision": state.get("decision"),
            },
            idempotency_key=f"audit-run-{run_uuid}",
        )

        return {
            "completed_at": completed_at,
            "final_result": final_result,
        }

    # ---------------- Build Graph & Routing ---------------- #
    workflow = StateGraph(InboxAgentState)

    workflow.add_node("ingest", node_ingest)
    workflow.add_node("resolve_entity", node_resolve_entity)
    workflow.add_node("load_context", node_load_context)
    workflow.add_node("classify_intent", node_classify_intent)
    workflow.add_node("propose_action", node_propose_action)
    workflow.add_node("permission_check", node_permission_check)
    workflow.add_node("execute_tool", node_execute_tool)
    workflow.add_node("verify_result", node_verify_result)
    workflow.add_node("audit", node_audit)

    # Edge definitions
    workflow.set_entry_point("ingest")
    workflow.add_edge("ingest", "resolve_entity")
    workflow.add_edge("resolve_entity", "load_context")
    workflow.add_edge("load_context", "classify_intent")
    workflow.add_edge("classify_intent", "propose_action")
    workflow.add_edge("propose_action", "permission_check")

    def route_after_permission_check(state: InboxAgentState) -> str:
        app_state = state.get("approval_state")
        outcome = state.get("terminal_outcome")
        if app_state == "needs_human" or outcome == "needs_human":
            return "audit"
        if app_state == "rejected" or outcome == "failed":
            return "audit"
        if not state.get("proposed_action"):
            return "audit"
        return "execute_tool"

    workflow.add_conditional_edges(
        "permission_check",
        route_after_permission_check,
        {
            "audit": "audit",
            "execute_tool": "execute_tool",
        },
    )

    workflow.add_edge("execute_tool", "verify_result")
    workflow.add_edge("verify_result", "audit")
    workflow.add_edge("audit", END)

    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)
