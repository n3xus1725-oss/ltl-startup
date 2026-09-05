"""Phase 1 Testing UI backend endpoints.

Enables developer/operator testing of email intake, deterministic shipment resolution,
LangGraph workflow execution, tool calls, live database updates, and audit logging.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlalchemy.orm import Session

from apps.agent.inbox.service import InboxAgentService
from apps.agent.inbox.validators import InboxAgentInput
from packages.domain.models import AuditLog, Organization, Shipment, ToolCall
from packages.storage.db import check_db_connection, get_db

router = APIRouter(prefix="/test-ui", tags=["Testing UI"])

DEFAULT_TEST_ORG_NAME = "Nexus Freight Logistics"
DEFAULT_TEST_ORG_SLUG = "nexus-freight-demo"

# Preloaded freight email scenarios
PRELOADED_EMAILS = [
    {
        "id": "email-pickup-5821",
        "scenario": "Workflow A: Carrier Pickup Confirmation",
        "subject": "Estes Express: Load 5821 Picked Up",
        "sender": "tracking@estes-express.com",
        "body_text": "Hello Dispatch,\n\nLoad 5821 (PRO 9823411) has been picked up from our Atlanta GA facility today at 10:30 AM.\nShipment is now in transit to the Chicago IL terminal.\n\nThank you,\nEstes Express Dispatch Team",
        "expected_action": "Resolve Load 5821 -> Update status to 'in_transit' -> Record pickup date -> Draft customer notice",
    },
    {
        "id": "email-eta-delay-5821",
        "scenario": "Workflow B: Carrier ETA Delay Update",
        "subject": "Old Dominion: ETA Update for PRO 9823411 (Load 5821)",
        "sender": "updates@odfl.com",
        "body_text": "Attention Logistics Team,\n\nShipment PRO 9823411 (Load 5821) has encountered severe weather along the I-65 corridor.\nRevised delivery ETA is updated to 2026-09-08T16:00:00Z.\n\nOld Dominion Freight Line",
        "expected_action": "Resolve Load 5821 -> Update ETA -> Preserve prior ETA in metadata -> Notify stakeholders",
    },
    {
        "id": "email-missing-info-9842",
        "scenario": "Workflow C: Missing Information Request",
        "subject": "R+L Carriers: Missing BOL & Delivery Appt - Load 9842",
        "sender": "operations@rlcarriers.com",
        "body_text": "URGENT:\n\nWe need the signed BOL document and the confirmed delivery appointment window for Load 9842 before our driver can execute pickup at the Dallas TX warehouse.\n\nPlease reply with the requested documents ASAP.\n\nR+L Carriers Operations",
        "expected_action": "Resolve Load 9842 -> Identify missing documentation -> Draft request to sender -> Create follow-up task",
    },
    {
        "id": "email-ambiguous-unknown",
        "scenario": "Ambiguity Guardrail: Unmatched Inquiry (Human Escalation)",
        "subject": "Driver delayed at receiver facility",
        "sender": "carrier-unknown@dispatchers.com",
        "body_text": "Hi Team,\n\nOur driver is currently stuck at the dock door waiting for unloading instructions.\nPlease provide guidance.\n\nThanks,\nCarrier Dispatch",
        "expected_action": "No shipment reference found -> Mark ambiguous -> Escalate to human operator without unauthorized edits",
    },
]

# In-memory store for user-injected custom test emails
_INJECTED_EMAILS: List[Dict[str, Any]] = []


class InjectEmailRequest(BaseModel):
    subject: str = Field(..., description="Email subject line")
    sender: str = Field(..., description="Sender email address")
    body_text: str = Field(..., description="Email body content")
    scenario_label: Optional[str] = Field(default="Custom Test Email")


class ProcessEmailRequest(BaseModel):
    subject: str = Field(...)
    sender: str = Field(...)
    body_text: str = Field(...)
    organization_id: Optional[str] = Field(default=None)
    email_id: Optional[str] = Field(default=None)


def get_or_create_test_org(db: Session) -> Organization:
    """Ensure a persistent test organization exists."""
    org = db.query(Organization).filter(Organization.slug == DEFAULT_TEST_ORG_SLUG).first()
    if not org:
        org = Organization(
            name=DEFAULT_TEST_ORG_NAME,
            slug=DEFAULT_TEST_ORG_SLUG,
            is_active=True,
        )
        db.add(org)
        db.commit()
        db.refresh(org)
    return org


def seed_test_shipments_internal(db: Session, org_id: uuid.UUID) -> List[Shipment]:
    """Seed standard test shipments for demo and verification."""
    existing = db.query(Shipment).filter(Shipment.organization_id == org_id).all()
    if existing:
        return existing

    now = datetime.now(timezone.utc)
    s1 = Shipment(
        organization_id=org_id,
        shipment_number="SHP-5821",
        load_id="5821",
        carrier_name="Estes Express Lines",
        carrier_reference="9823411",
        bol_number="BOL-5821-ATL",
        origin_address={"city": "Atlanta", "state": "GA", "zip": "30301"},
        destination_address={"city": "Chicago", "state": "IL", "zip": "60601"},
        status="booked",
        weight_lbs=4250.0,
        pallet_count=4,
        eta=now,
    )
    s2 = Shipment(
        organization_id=org_id,
        shipment_number="SHP-9842",
        load_id="9842",
        carrier_name="R+L Carriers",
        carrier_reference="RL-984200",
        bol_number="BOL-847293",
        origin_address={"city": "Dallas", "state": "TX", "zip": "75201"},
        destination_address={"city": "Columbus", "state": "OH", "zip": "43201"},
        status="created",
        weight_lbs=8500.0,
        pallet_count=8,
    )
    s3 = Shipment(
        organization_id=org_id,
        shipment_number="SHP-7710",
        load_id="7710",
        carrier_name="Old Dominion Freight",
        carrier_reference="OD-771011",
        bol_number="BOL-7710-PHX",
        origin_address={"city": "Phoenix", "state": "AZ", "zip": "85001"},
        destination_address={"city": "Denver", "state": "CO", "zip": "80201"},
        status="in_transit",
        weight_lbs=3100.0,
        pallet_count=3,
    )

    db.add_all([s1, s2, s3])
    db.commit()
    db.refresh(s1)
    db.refresh(s2)
    db.refresh(s3)
    return [s1, s2, s3]


@router.get("/state")
def get_testing_state(db: Session = Depends(get_db)):
    """Retrieve test environment state, available emails, and system metrics."""
    db_connected = check_db_connection()
    org = get_or_create_test_org(db)
    # Auto-seed if empty
    shipments = seed_test_shipments_internal(db, org.id)

    all_emails = PRELOADED_EMAILS + _INJECTED_EMAILS

    return {
        "status": "ready",
        "database_connected": db_connected,
        "organization": {
            "id": str(org.id),
            "name": org.name,
            "slug": org.slug,
        },
        "shipment_count": len(shipments),
        "preloaded_emails": all_emails,
    }


@router.post("/seed")
def seed_shipments(db: Session = Depends(get_db)):
    """Seed or reset test shipments in the database."""
    org = get_or_create_test_org(db)
    # Clear existing demo shipments and recreate
    db.query(Shipment).filter(Shipment.organization_id == org.id).delete()
    db.commit()
    shipments = seed_test_shipments_internal(db, org.id)

    return {
        "status": "seeded",
        "count": len(shipments),
        "shipments": [
            {
                "id": str(s.id),
                "load_id": s.load_id,
                "shipment_number": s.shipment_number,
                "carrier_name": s.carrier_name,
                "carrier_reference": s.carrier_reference,
                "bol_number": s.bol_number,
                "status": s.status,
            }
            for s in shipments
        ],
    }


@router.post("/emails/inject")
def inject_custom_email(payload: InjectEmailRequest):
    """Add a custom email to the test inbox."""
    new_email = {
        "id": f"custom-{uuid.uuid4().hex[:8]}",
        "scenario": payload.scenario_label or "Custom Test Email",
        "subject": payload.subject,
        "sender": payload.sender,
        "body_text": payload.body_text,
        "expected_action": "Custom agent evaluation",
    }
    _INJECTED_EMAILS.insert(0, new_email)
    return {"status": "injected", "email": new_email}


@router.post("/process")
async def process_email(
    request: ProcessEmailRequest,
    db: Session = Depends(get_db),
):
    """Execute the Inbox Action Agent on an email and return the full execution trace."""
    org = get_or_create_test_org(db)
    org_id = uuid.UUID(request.organization_id) if request.organization_id else org.id

    # Capture shipments before run for diff calculation
    shipments_before = {
        str(s.id): {
            "id": str(s.id),
            "load_id": s.load_id,
            "shipment_number": s.shipment_number,
            "status": s.status,
            "pickup_date": s.pickup_date.isoformat() if s.pickup_date else None,
            "eta": s.eta.isoformat() if s.eta else None,
            "carrier_name": s.carrier_name,
        }
        for s in db.query(Shipment).filter(Shipment.organization_id == org_id).all()
    }

    # Execute LangGraph Inbox Action Agent
    service = InboxAgentService(db=db)
    trigger_event_id = f"test-ui-evt-{uuid.uuid4().hex[:12]}"
    agent_input = InboxAgentInput(
        organization_id=str(org_id),
        trigger_event_id=trigger_event_id,
        sender=request.sender,
        subject=request.subject,
        body_text=request.body_text,
    )

    agent_output = await service.run(agent_input)

    # Capture shipments after run for diff calculation
    db.expire_all()
    shipments_after = {
        str(s.id): {
            "id": str(s.id),
            "load_id": s.load_id,
            "shipment_number": s.shipment_number,
            "status": s.status,
            "pickup_date": s.pickup_date.isoformat() if s.pickup_date else None,
            "eta": s.eta.isoformat() if s.eta else None,
            "carrier_name": s.carrier_name,
        }
        for s in db.query(Shipment).filter(Shipment.organization_id == org_id).all()
    }

    matched_shipment_info = None
    diff_info = {}
    if agent_output.entity_id and agent_output.entity_id in shipments_after:
        eid = agent_output.entity_id
        before = shipments_before.get(eid, {})
        after = shipments_after.get(eid, {})
        for k in ["status", "pickup_date", "eta"]:
            if before.get(k) != after.get(k):
                diff_info[k] = {"before": before.get(k), "after": after.get(k)}

        matched_shipment_info = {
            "id": eid,
            "load_id": after.get("load_id"),
            "shipment_number": after.get("shipment_number"),
            "carrier_name": after.get("carrier_name"),
            "status_before": before.get("status"),
            "status_after": after.get("status"),
            "diff": diff_info,
        }

    # Fetch stored tool calls for this run
    run_uuid = uuid.UUID(agent_output.run_id)
    tool_call_records = db.query(ToolCall).filter(ToolCall.agent_run_id == run_uuid).all()
    tool_calls_data = [
        {
            "tool_name": tc.tool_name,
            "input_parameters": tc.arguments,
            "output_result": tc.result,
            "execution_status": tc.status,
            "executed_at": tc.executed_at.isoformat() if tc.executed_at else None,
        }
        for tc in tool_call_records
    ]

    # Fetch audit log entry
    audit_entry = (
        db.query(AuditLog)
        .order_by(desc(AuditLog.created_at))
        .first()
    )
    audit_data = None
    if audit_entry:
        audit_data = {
            "id": str(audit_entry.id),
            "event_type": audit_entry.event_id,
            "action_taken": audit_entry.action,
            "actor_type": audit_entry.actor_type,
            "actor_id": audit_entry.actor_id,
            "idempotency_key": audit_entry.idempotency_key,
            "timestamp": audit_entry.created_at.isoformat() if audit_entry.created_at else None,
        }

    return {
        "run_id": agent_output.run_id,
        "terminal_outcome": agent_output.terminal_outcome,
        "decision": agent_output.decision,
        "confidence": agent_output.confidence,
        "approval_state": agent_output.approval_state,
        "matched_shipment": matched_shipment_info,
        "tool_calls": tool_calls_data or agent_output.tool_calls,
        "final_result": agent_output.final_result,
        "audit_log": audit_data,
        "errors": agent_output.errors,
        "cost_estimate": agent_output.cost_estimate,
        "started_at": agent_output.started_at,
        "completed_at": agent_output.completed_at,
    }


@router.get("/shipments")
def list_test_shipments(db: Session = Depends(get_db)):
    """List current shipments in Supabase."""
    org = get_or_create_test_org(db)
    shipments = db.query(Shipment).filter(Shipment.organization_id == org.id).order_by(Shipment.shipment_number).all()
    return [
        {
            "id": str(s.id),
            "load_id": s.load_id,
            "shipment_number": s.shipment_number,
            "carrier_name": s.carrier_name,
            "carrier_reference": s.carrier_reference,
            "bol_number": s.bol_number,
            "status": s.status,
            "origin": s.origin_address,
            "destination": s.destination_address,
            "pickup_date": s.pickup_date.isoformat() if s.pickup_date else None,
            "eta": s.eta.isoformat() if s.eta else None,
            "weight_lbs": s.weight_lbs,
            "pallet_count": s.pallet_count,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        }
        for s in shipments
    ]


@router.get("/audit-logs")
def list_test_audit_logs(limit: int = 20, db: Session = Depends(get_db)):
    """List recent audit log entries in Supabase."""
    records = db.query(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit).all()
    return [
        {
            "id": str(r.id),
            "event_type": r.event_id,
            "action_taken": r.action,
            "actor_type": r.actor_type,
            "actor_id": r.actor_id,
            "idempotency_key": r.idempotency_key,
            "timestamp": r.created_at.isoformat() if r.created_at else None,
            "changes": r.metadata_json,
        }
        for r in records
    ]
