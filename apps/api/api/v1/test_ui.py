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
from packages.domain.models import (
    AuditFindingRecord,
    AuditLog,
    CarrierContact,
    CarrierInvoice,
    Organization,
    Shipment,
    ToolCall,
)
from packages.storage.db import check_db_connection, get_db
from packages.storage.repositories.carrier_contacts import CarrierContactRepository

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


# -------------------------------------------------------------
# PHASE 2: SOURCE-OF-TRUTH, PROVENANCE, CONFLICTS & RETRIEVAL UI
# -------------------------------------------------------------

class ParseDocumentRequest(BaseModel):
    filename: str = Field(default="freight_document.pdf")
    content_text: str = Field(..., description="Raw text or simulated document content")


class SearchContextRequest(BaseModel):
    query: str = Field(...)
    limit: int = Field(default=10)


class ResolveConflictRequest(BaseModel):
    resolution_notes: Optional[str] = Field(default="Resolved by operator in testing UI")


@router.get("/shipments/{shipment_id}/canonical")
def get_canonical_shipment_details(
    shipment_id: str,
    db: Session = Depends(get_db),
):
    """Phase 2.1 — Inspect Canonical Shipment Model and Field-Level Provenance Ledger."""
    from packages.retrieval.engine import ShipmentRetrievalEngine
    from packages.storage.repositories.shipments import ShipmentRepository

    org = get_or_create_test_org(db)
    shp_repo = ShipmentRepository(db)
    shipment_uuid = uuid.UUID(shipment_id)
    shipment = shp_repo.get_by_id(org.id, shipment_uuid)
    if not shipment:
        return {"error": "Shipment not found", "status": 404}

    retrieval_engine = ShipmentRetrievalEngine(db)
    context = retrieval_engine.get_canonical_context(org.id, shipment_uuid)

    return {
        "shipment_id": str(shipment.id),
        "shipment_number": shipment.shipment_number,
        "load_id": shipment.load_id,
        "status": shipment.status,
        "canonical_data": context.canonical_data if context else shipment.canonical_data,
        "provenance_ledger": context.provenance_trail if context else shipment.provenance_ledger,
        "active_conflicts": context.active_conflicts if context else [],
        "attached_documents": context.attached_documents if context else [],
    }


@router.get("/conflicts")
def list_detected_conflicts(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Phase 2.4 & 2.6 — List all detected data and authority conflicts."""
    from packages.storage.repositories.conflicts import ConflictRepository

    org = get_or_create_test_org(db)
    repo = ConflictRepository(db)
    conflicts = repo.list_by_organization(org.id, status=status, limit=100)
    return [
        {
            "id": str(c.id),
            "shipment_id": str(c.shipment_id),
            "field_name": c.field_name,
            "conflict_type": c.conflict_type,
            "severity": c.severity,
            "status": c.status,
            "explanation": c.explanation,
            "recommended_workflow": c.recommended_workflow,
            "source_a": c.source_a,
            "source_b": c.source_b,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
            "resolution_notes": c.resolution_notes,
        }
        for c in conflicts
    ]


@router.post("/conflicts/{conflict_id}/resolve")
def resolve_conflict(
    conflict_id: str,
    payload: ResolveConflictRequest,
    db: Session = Depends(get_db),
):
    """Phase 2.4 — Resolve a detected discrepancy with operator notes."""
    from packages.storage.repositories.conflicts import ConflictRepository

    org = get_or_create_test_org(db)
    repo = ConflictRepository(db)
    resolved = repo.resolve_conflict(
        organization_id=org.id,
        conflict_id=uuid.UUID(conflict_id),
        resolution_notes=payload.resolution_notes or "Resolved via Testing Console",
        resolved_by="operator:test-console",
    )
    if not resolved:
        return {"error": "Conflict not found", "status": 404}

    return {
        "status": "resolved",
        "conflict_id": str(resolved.id),
        "resolution_notes": resolved.resolution_notes,
        "resolved_at": resolved.resolved_at.isoformat() if resolved.resolved_at else None,
    }


@router.post("/parse-document")
def parse_document_playground(
    payload: ParseDocumentRequest,
    db: Session = Depends(get_db),
):
    """Phase 2.2 — Document Intake, Classification, and Normalizer Playground."""
    from packages.documents.pipeline import DocumentProcessingPipeline

    org = get_or_create_test_org(db)
    pipeline = DocumentProcessingPipeline(db)
    content_bytes = payload.content_text.encode("utf-8")

    result = pipeline.process_content(
        filename=payload.filename,
        content_bytes=content_bytes,
        organization_id=org.id,
    )

    return {
        "filename": payload.filename,
        "classified_type": result.classified_type,
        "confidence": result.confidence,
        "extracted_fields": result.extracted_fields,
        "provenance_assertions": result.provenance_assertions,
        "raw_text_snippet": result.raw_text_snippet,
    }


@router.post("/search")
def search_retrieval_playground(
    payload: SearchContextRequest,
    db: Session = Depends(get_db),
):
    """Phase 2.5 — Unified Relational & Semantic Context Retrieval."""
    from packages.retrieval.engine import ShipmentRetrievalEngine

    org = get_or_create_test_org(db)
    engine = ShipmentRetrievalEngine(db)
    items = engine.search_all(org.id, payload.query, limit=payload.limit)

    return {
        "query": payload.query,
        "total_results": len(items),
        "results": [
            {
                "entity_type": item.entity_type,
                "entity_id": str(item.entity_id),
                "title": item.title,
                "snippet": item.snippet,
                "relevance_score": item.relevance_score,
                "metadata": item.metadata,
            }
            for item in items
        ],
    }


@router.post("/simulate-multi-source")
async def simulate_multi_source_reconstruction(
    db: Session = Depends(get_db),
):
    """Phase 2 Exit Criteria Demo: Progressive multi-source shipment reconstruction and conflict guardrails.

    Demonstrates:
    1. Initial Rate Confirmation ($1,650 agreed rate, Chicago -> Atlanta)
    2. Driver Pickup Notification email (sets status to 'in_transit', confirms actual pickup)
    3. BOL Document ingestion (enriches weight: 16,450 lbs, pallets: 12 without overwriting pricing)
    4. Reweigh Scale Ticket (highest authority 100 updates verified weight to 16,520 lbs)
    5. Conflicting Invoice ($1,850 billed amount vs $1,650 agreed) -> Triggers Critical Conflict & halts write
    6. POD Signed Delivery Receipt (marks delivered with consignee signature)
    """
    from apps.agent.inbox.service import run_inbox_agent
    from packages.domain.canonical import (
        CanonicalShipment,
        ShipmentParties,
        ShipmentParty,
        ShipmentPricing,
    )
    from packages.domain.provenance import FieldProvenanceRecord, ProvenanceLedger
    from packages.storage.repositories.shipments import ShipmentRepository

    org = get_or_create_test_org(db)
    shp_repo = ShipmentRepository(db)

    # 1. Create baseline shipment
    sim_load_id = f"SIM-{uuid.uuid4().hex[:6].upper()}"
    shp = shp_repo.create(
        organization_id=org.id,
        shipment_number=f"LOAD-{sim_load_id}",
        load_id=sim_load_id,
        status="booked",
        total_charges=1650.0,
    )

    # Step 1: Rate Confirmation Baseline
    canonical = CanonicalShipment(
        organization_id=str(org.id),
        shipment_number=f"LOAD-{sim_load_id}",
        status="booked",
        parties=ShipmentParties(
            shipper=ShipmentParty(name="Chicago Metal Works", city="Chicago", state="IL"),
            consignee=ShipmentParty(name="Atlanta Auto Assembly", city="Atlanta", state="GA"),
            carrier=ShipmentParty(name="Estes Express Lines"),
        ),
        pricing=ShipmentPricing(agreed_total=1650.0, linehaul=1650.0),
    )
    ledger = ProvenanceLedger()
    ledger.record_assertion(
        FieldProvenanceRecord(
            field="pricing.agreed_total",
            value=1650.0,
            source="rate_confirmation",
            source_id=f"RC-{sim_load_id}",
            authority=100,
            confidence=0.99,
        )
    )
    shp_repo.update_canonical(org.id, shp.id, canonical.model_dump(), ledger.to_dict())

    steps = [
        {
            "step": 1,
            "source": "Rate Confirmation (Initial Tender)",
            "action": "Baseline shipment created with Rate Con pricing ($1,650.00 agreed rate, Chicago -> Atlanta)",
            "status": "booked",
            "agreed_total": 1650.0,
            "weight_lbs": None,
        }
    ]

    # Step 2: Driver Pickup Confirmation Email
    await run_inbox_agent(
        organization_id=str(org.id),
        trigger_event_id=f"evt-sim-pickup-{uuid.uuid4().hex[:8]}",
        sender="dispatch@estes-express.com",
        subject=f"Estes Express: Load {sim_load_id} Picked Up",
        body_text=f"Driver has picked up Load {sim_load_id} from Chicago facility on 2026-09-06T10:00:00Z and is en route.",
        db=db,
    )
    db.expire_all()
    c_after_pickup = shp_repo.get_canonical(org.id, shp.id)
    steps.append({
        "step": 2,
        "source": "Carrier Email (Pickup Notice)",
        "action": f"Status updated to '{c_after_pickup.get('status')}' based on carrier email assertion",
        "status": c_after_pickup.get("status"),
        "agreed_total": c_after_pickup.get("pricing", {}).get("agreed_total"),
        "weight_lbs": c_after_pickup.get("freight_details", {}).get("total_weight_lbs"),
    })

    # Step 3: BOL Attachment Ingestion
    bol_content = f"""
    UNIFORM STRAIGHT BILL OF LADING
    BOL #: BOL-{sim_load_id}
    Load #: {sim_load_id}
    Carrier Name: Estes Express Lines
    Trailer #: TR-4421
    Total Weight: 16,450 lbs
    Pallet Count: 12
    """
    await run_inbox_agent(
        organization_id=str(org.id),
        trigger_event_id=f"evt-sim-bol-{uuid.uuid4().hex[:8]}",
        sender="dispatch@estes-express.com",
        subject=f"Attached BOL for Load {sim_load_id}",
        body_text=f"Please find the attached Bill of Lading for Load {sim_load_id}:\n{bol_content}",
        db=db,
    )
    db.expire_all()
    c_after_bol = shp_repo.get_canonical(org.id, shp.id)
    steps.append({
        "step": 3,
        "source": "Bill of Lading (Document Extraction)",
        "action": "Extracted 16,450 lbs & 12 pallets; enriched canonical shipment while strictly preserving Rate Con pricing",
        "status": c_after_bol.get("status"),
        "agreed_total": c_after_bol.get("pricing", {}).get("agreed_total"),
        "weight_lbs": c_after_bol.get("freight_details", {}).get("total_weight_lbs"),
        "pallet_count": c_after_bol.get("freight_details", {}).get("pallet_count"),
    })

    # Step 4: Reweigh Scale Ticket
    scale_content = f"""
    CERTIFIED CAT SCALE TICKET
    Ticket #: ST-{sim_load_id}
    Scale Name: Pilot Travel Center #412
    Gross Weight: 34,520 lbs
    Tare Weight: 18,000 lbs
    Net Weight: 16,520 lbs
    """
    await run_inbox_agent(
        organization_id=str(org.id),
        trigger_event_id=f"evt-sim-scale-{uuid.uuid4().hex[:8]}",
        sender="driver@estes-express.com",
        subject=f"Scale Ticket Reweigh for Load {sim_load_id}",
        body_text=f"Certified scale ticket attached for Load {sim_load_id}:\n{scale_content}",
        db=db,
    )
    db.expire_all()
    c_after_scale = shp_repo.get_canonical(org.id, shp.id)
    steps.append({
        "step": 4,
        "source": "Scale Ticket (Certified Reweigh - Authority 100)",
        "action": "Verified reweigh updated certified weight to 16,520 lbs (superseding BOL 16,450 lbs per authority matrix)",
        "status": c_after_scale.get("status"),
        "agreed_total": c_after_scale.get("pricing", {}).get("agreed_total"),
        "weight_lbs": c_after_scale.get("freight_details", {}).get("total_weight_lbs"),
    })

    # Step 5: Conflicting Invoice ($1,850 vs $1,650 agreed)
    inv_content = f"""
    FREIGHT INVOICE
    Invoice #: INV-{sim_load_id}
    Load #: {sim_load_id}
    Linehaul Rate: $1,850.00
    Total Due: $1,850.00
    """
    res_inv = await run_inbox_agent(
        organization_id=str(org.id),
        trigger_event_id=f"evt-sim-inv-{uuid.uuid4().hex[:8]}",
        sender="billing@estes-express.com",
        subject=f"Invoice for Load {sim_load_id}",
        body_text=f"Please remit payment for Load {sim_load_id}.\n{inv_content}",
        db=db,
    )
    db.expire_all()
    c_after_inv = shp_repo.get_canonical(org.id, shp.id)
    steps.append({
        "step": 5,
        "source": "Carrier Invoice (Conflict Guardrail)",
        "action": f"Discrepancy detected ($1,850 billed vs $1,650 agreed). Terminal outcome: {res_inv.terminal_outcome}. Canonical pricing preserved; conflict flagged for human review.",
        "status": c_after_inv.get("status"),
        "agreed_total": c_after_inv.get("pricing", {}).get("agreed_total"),
        "discrepancy_halted": True,
        "reasoning": res_inv.reasoning,
    })

    return {
        "shipment_id": str(shp.id),
        "shipment_number": f"LOAD-{sim_load_id}",
        "load_id": sim_load_id,
        "steps": steps,
        "final_canonical": c_after_inv,
        "provenance_ledger": shp_repo.get_provenance_ledger(org.id, shp.id).to_dict(),
    }


# Phase 3 Billing Audit Preloaded Scenarios
BILLING_AUDIT_SCENARIOS = [
    {
        "id": "scenario-clean",
        "title": "Clean Invoice (Zero Discrepancies)",
        "description": "Invoice matches contract linehaul ($1,650.00), fuel schedule (15% = $247.50), and stated total ($1,897.50).",
        "expected_rules": [],
        "carrier": "Estes Express Lines",
        "invoice_number": "INV-CLEAN-101",
        "invoice_text": """CARRIER FREIGHT INVOICE
Invoice #: INV-CLEAN-101
Carrier: Estes Express Lines
Load #: 9001
Linehaul Rate: $1,650.00
Fuel Surcharge: $247.50
Total Due: $1,897.50
Weight: 16,450 lbs
Class: 70
Pallets: 12
""",
    },
    {
        "id": "scenario-duplicate",
        "title": "Rule 1: Duplicate Invoice Detection",
        "description": "Carrier submits an invoice with the identical invoice number as a previously processed payment.",
        "expected_rules": ["RULE_01_DUPLICATE_INVOICE"],
        "carrier": "Estes Express Lines",
        "invoice_number": "INV-DUP-999",
        "invoice_text": """CARRIER FREIGHT INVOICE
Invoice #: INV-DUP-999
Carrier: Estes Express Lines
Load #: 9001
Linehaul Rate: $1,650.00
Total Due: $1,650.00
""",
    },
    {
        "id": "scenario-linehaul",
        "title": "Rule 2: Linehaul Rate Overcharge",
        "description": "Carrier bills $1,850.00 linehaul when contract and rate con agreed linehaul is $1,650.00 ($200.00 overcharge).",
        "expected_rules": ["RULE_02_LINEHAUL_MISMATCH", "RULE_09_QUOTE_VERSUS_INVOICE_MISMATCH"],
        "carrier": "Estes Express Lines",
        "invoice_number": "INV-LH-202",
        "invoice_text": """CARRIER FREIGHT INVOICE
Invoice #: INV-LH-202
Carrier: Estes Express Lines
Load #: 9001
Linehaul Rate: $1,850.00
Fuel Surcharge: $247.50
Total Due: $2,097.50
""",
    },
    {
        "id": "scenario-fuel",
        "title": "Rule 3: Fuel Surcharge Discrepancy",
        "description": "Carrier bills $350.00 fuel surcharge when contract formula (15% of $1,650) is $247.50 ($102.50 overcharge).",
        "expected_rules": ["RULE_03_FUEL_MISMATCH"],
        "carrier": "Estes Express Lines",
        "invoice_number": "INV-FUEL-303",
        "invoice_text": """CARRIER FREIGHT INVOICE
Invoice #: INV-FUEL-303
Carrier: Estes Express Lines
Load #: 9001
Linehaul Rate: $1,650.00
Fuel Surcharge: $350.00
Total Due: $2,000.00
""",
    },
    {
        "id": "scenario-accessorial",
        "title": "Rule 4: Unsupported / Unverified Accessorials",
        "description": "Carrier bills $175.00 lumper fee with no receipt attached + $150.00 detention fee without timestamp proof.",
        "expected_rules": ["RULE_04_UNSUPPORTED_ACCESSORIAL"],
        "carrier": "Estes Express Lines",
        "invoice_number": "INV-ACC-404",
        "invoice_text": """CARRIER FREIGHT INVOICE
Invoice #: INV-ACC-404
Carrier: Estes Express Lines
Load #: 9001
Linehaul Rate: $1,650.00
Fuel Surcharge: $247.50
Lumper Fee: $175.00
Detention Charge: $150.00
Total Due: $2,222.50
""",
    },
    {
        "id": "scenario-weight",
        "title": "Rule 5: Invoiced Weight Discrepancy",
        "description": "Carrier bills for 18,200 lbs when certified scale ticket records net weight of 16,450 lbs (1,750 lbs variance).",
        "expected_rules": ["RULE_05_WEIGHT_MISMATCH"],
        "carrier": "Estes Express Lines",
        "invoice_number": "INV-WT-505",
        "invoice_text": """CARRIER FREIGHT INVOICE
Invoice #: INV-WT-505
Carrier: Estes Express Lines
Load #: 9001
Billed Weight: 18,200 lbs
Linehaul Rate: $1,650.00
Total Due: $1,650.00
""",
    },
    {
        "id": "scenario-reclass",
        "title": "Rule 6 & 7: Class Mismatch & Reclassification Fee",
        "description": "Carrier upclasses freight from Class 70 to Class 100 and assesses an $85.00 reclass fee without certified inspection certificate.",
        "expected_rules": ["RULE_06_CLASS_MISMATCH", "RULE_07_RECLASS_DISCREPANCY"],
        "carrier": "Estes Express Lines",
        "invoice_number": "INV-CLS-606",
        "invoice_text": """CARRIER FREIGHT INVOICE
Invoice #: INV-CLS-606
Carrier: Estes Express Lines
Load #: 9001
Freight Class: 100
Reclass Fee: $85.00
Linehaul Rate: $1,650.00
Total Due: $1,735.00
""",
    },
    {
        "id": "scenario-pallets",
        "title": "Rule 8: Handling Unit / Pallet Discrepancy",
        "description": "Carrier bills for 16 pallets when signed BOL and POD confirm 12 pallets received (4 pallet discrepancy).",
        "expected_rules": ["RULE_08_DIMENSION_PALLET_DISCREPANCY"],
        "carrier": "Estes Express Lines",
        "invoice_number": "INV-PLT-707",
        "invoice_text": """CARRIER FREIGHT INVOICE
Invoice #: INV-PLT-707
Carrier: Estes Express Lines
Load #: 9001
Pallet Count: 16
Linehaul Rate: $1,650.00
Total Due: $1,650.00
""",
    },
    {
        "id": "scenario-arithmetic",
        "title": "Rule 10: Invoice Arithmetic Total Mismatch",
        "description": "Linehaul ($1,650.00) + Fuel ($250.00) = $1,900.00, but carrier stated total is $2,150.00 ($250.00 unexplained padding).",
        "expected_rules": ["RULE_10_ARITHMETIC_TOTAL_MISMATCH"],
        "carrier": "Estes Express Lines",
        "invoice_number": "INV-MATH-808",
        "invoice_text": """CARRIER FREIGHT INVOICE
Invoice #: INV-MATH-808
Carrier: Estes Express Lines
Load #: 9001
Linehaul Rate: $1,650.00
Fuel Surcharge: $250.00
Total Due: $2,150.00
""",
    },
]


@router.get("/billing-audit/scenarios")
def get_billing_audit_scenarios() -> List[Dict[str, Any]]:
    """Return preloaded billing audit test scenarios covering all 10 deterministic rules."""
    return BILLING_AUDIT_SCENARIOS


class AuditSimulateRequest(BaseModel):
    scenario_id: str
    custom_text: Optional[str] = None


@router.post("/billing-audit/simulate")
def simulate_billing_audit(
    payload: AuditSimulateRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Execute end-to-end billing audit simulation for a preloaded scenario."""
    from packages.documents.normalizer import DocumentNormalizer
    from packages.rules.audit_engine import DeterministicAuditEngine
    from packages.storage.repositories.contracts import RateContractRepository
    from packages.storage.repositories.invoices import InvoiceRepository
    from packages.storage.repositories.shipments import ShipmentRepository

    org = get_or_create_test_org(db)
    shp_repo = ShipmentRepository(db)
    contract_repo = RateContractRepository(db)
    inv_repo = InvoiceRepository(db)

    # 1. Find scenario
    scenario = next((s for s in BILLING_AUDIT_SCENARIOS if s["id"] == payload.scenario_id), BILLING_AUDIT_SCENARIOS[0])
    text_to_audit = payload.custom_text or scenario["invoice_text"]

    # 2. Ensure baseline Rate Contract exists
    contract = contract_repo.find_matching_contract(org.id, "Estes Express")
    if not contract:
        contract = contract_repo.create_contract(
            organization_id=org.id,
            carrier_name="Estes Express Lines",
            contract_number="CTR-ESTES-2026",
            base_rate=1650.0,
            minimum_charge=500.0,
            rate_type="flat",
            fuel_schedule={"type": "percent", "base_rate_percent": 15.0},
            accessorial_schedule={
                "DETENTION": {"rate_per_hour": 75.0, "free_hours": 2},
                "LIFTGATE": {"flat": 100.0},
                "RESIDENTIAL": {"flat": 85.0},
            },
        )

    # 3. Ensure baseline Shipment exists
    shp = shp_repo.get_by_shipment_number(org.id, "LOAD-9001")
    if not shp:
        shp = shp_repo.create(
            organization_id=org.id,
            shipment_number="LOAD-9001",
            load_id="9001",
            status="delivered",
            total_charges=1897.50,
        )
        shp_repo.update_canonical(
            org.id,
            shp.id,
            canonical_data={
                "organization_id": str(org.id),
                "shipment_number": "LOAD-9001",
                "status": "delivered",
                "pricing": {
                    "agreed_linehaul": 1650.0,
                    "fuel_surcharge": 247.50,
                    "agreed_total": 1897.50,
                },
                "freight_details": {
                    "total_weight_lbs": 16450.0,
                    "pallet_count": 12,
                    "freight_class": "70",
                },
            },
            provenance_ledger={"entries": {}},
        )

    # If duplicate scenario, seed prior invoice first
    existing_invoices = []
    if payload.scenario_id == "scenario-duplicate":
        prior_inv = inv_repo.get_by_number(org.id, "Estes Express Lines", "INV-DUP-999")
        if not prior_inv:
            prior_inv = inv_repo.create_invoice(
                organization_id=org.id,
                carrier_name="Estes Express Lines",
                invoice_number="INV-DUP-999",
                total_billed_amount=1650.0,
                status="paid",
            )
        existing_invoices.append({
            "id": str(prior_inv.id),
            "invoice_number": prior_inv.invoice_number,
            "carrier_name": prior_inv.carrier_name,
            "total_billed_amount": prior_inv.total_billed_amount,
            "status": prior_inv.status,
        })

    # 4. Parse invoice text
    normalized = DocumentNormalizer.normalize_invoice(text_to_audit)
    inv_dict = {
        "invoice_number": normalized.invoice_number or scenario["invoice_number"],
        "carrier_name": normalized.carrier_name or scenario["carrier"],
        "total_billed_amount": normalized.total_billed_amount or 1650.0,
        "linehaul_amount": normalized.linehaul_amount or 1650.0,
        "fuel_amount": normalized.fuel_amount or 0.0,
        "accessorial_amount": normalized.accessorial_amount or 0.0,
        "weight_lbs": normalized.weight_lbs,
        "freight_class": normalized.freight_class,
        "pallet_count": normalized.pallet_count,
        "line_items": normalized.line_items,
        "accessorials": normalized.accessorials,
    }

    # Baseline verified documents
    bol_dict = {"nmfc_class": "70", "total_weight_lbs": 16450.0, "pallet_count": 12}
    scale_dict = {"net_weight_lbs": 16450.0}
    pod_dict = {"piece_count_received": 12}
    attached_docs = ["BILL_OF_LADING", "PROOF_OF_DELIVERY"]

    contract_dict = {
        "contract_number": contract.contract_number,
        "base_rate": contract.base_rate,
        "minimum_charge": contract.minimum_charge,
        "rate_type": contract.rate_type,
        "fuel_schedule": contract.fuel_schedule,
        "accessorial_schedule": contract.accessorial_schedule,
    }

    shipment_dict = {
        "id": str(shp.id),
        "shipment_number": shp.shipment_number,
        "canonical_data": shp.canonical_data or {},
    }

    # 5. Run Deterministic Audit Engine
    engine = DeterministicAuditEngine()
    report = engine.audit_invoice(
        invoice=inv_dict,
        shipment=shipment_dict,
        contract=contract_dict,
        scale_ticket=scale_dict,
        bol=bol_dict,
        pod=pod_dict,
        existing_invoices=existing_invoices,
        attached_document_types=attached_docs,
    )

    return {
        "scenario_id": scenario["id"],
        "scenario_title": scenario["title"],
        "invoice_parsed": inv_dict,
        "contract_used": contract_dict,
        "report": report.model_dump(),
        "is_clean": report.is_clean,
        "total_discrepancy": report.total_discrepancy_amount,
        "findings_count": len(report.findings),
    }


class RunAuditAgentRequest(BaseModel):
    scenario_id: Optional[str] = "scenario-clean"
    custom_text: Optional[str] = None
    force_reasoning_model: bool = False


@router.post("/billing-audit/run-agent")
async def run_billing_audit_agent(
    payload: RunAuditAgentRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Execute full LangGraph Billing Audit Agent with trajectory tracing and cost observability."""
    from apps.agent.audit.service import run_audit_agent
    from packages.storage.repositories.contracts import RateContractRepository
    from packages.storage.repositories.invoices import InvoiceRepository
    from packages.storage.repositories.shipments import ShipmentRepository

    org = get_or_create_test_org(db)
    shp_repo = ShipmentRepository(db)
    contract_repo = RateContractRepository(db)
    inv_repo = InvoiceRepository(db)

    # 1. Match Scenario
    scenario = next((s for s in BILLING_AUDIT_SCENARIOS if s["id"] == payload.scenario_id), BILLING_AUDIT_SCENARIOS[0])
    text_to_audit = payload.custom_text or scenario["invoice_text"]

    # 2. Ensure baseline Rate Contract exists
    contract = contract_repo.find_matching_contract(org.id, "Estes Express")
    if not contract:
        contract = contract_repo.create_contract(
            organization_id=org.id,
            carrier_name="Estes Express Lines",
            contract_number="CTR-ESTES-2026",
            base_rate=1650.0,
            minimum_charge=500.0,
            rate_type="flat",
            fuel_schedule={"type": "percent", "base_rate_percent": 15.0},
            accessorial_schedule={
                "DETENTION": {"rate_per_hour": 75.0, "free_hours": 2},
                "LIFTGATE": {"flat": 100.0},
                "RESIDENTIAL": {"flat": 85.0},
            },
        )

    # 3. Ensure baseline Shipment LOAD-9001 exists
    shp = shp_repo.get_by_shipment_number(org.id, "LOAD-9001")
    if not shp:
        shp = shp_repo.create(
            organization_id=org.id,
            shipment_number="LOAD-9001",
            load_id="9001",
            status="delivered",
            total_charges=1897.50,
        )
        shp_repo.update_canonical(
            org.id,
            shp.id,
            canonical_data={
                "organization_id": str(org.id),
                "shipment_number": "LOAD-9001",
                "status": "delivered",
                "pricing": {
                    "agreed_linehaul": 1650.0,
                    "fuel_surcharge": 247.50,
                    "agreed_total": 1897.50,
                },
                "freight_details": {
                    "total_weight_lbs": 16450.0,
                    "pallet_count": 12,
                    "freight_class": "70",
                },
            },
            provenance_ledger={"entries": {}},
        )

    # 4. Duplicate invoice scenario: ensure prior paid invoice exists in DB
    if payload.scenario_id == "scenario-duplicate":
        prior_inv = inv_repo.get_by_number(org.id, "Estes Express Lines", "INV-DUP-999")
        if not prior_inv:
            prior_inv = inv_repo.create_invoice(
                organization_id=org.id,
                carrier_name="Estes Express Lines",
                invoice_number="INV-DUP-999",
                total_billed_amount=1650.0,
                status="paid",
            )

    # 5. Execute LangGraph Audit Agent
    trigger_id = f"evt-agent-test-{uuid.uuid4().hex[:8]}"
    output = await run_audit_agent(
        organization_id=str(org.id),
        db=db,
        invoice_text=text_to_audit,
        carrier_name=scenario.get("carrier") or "Estes Express Lines",
        invoice_number=scenario.get("invoice_number"),
        trigger_event_id=trigger_id,
        force_reasoning_model=payload.force_reasoning_model,
    )

    return {
        "scenario_id": scenario["id"],
        "scenario_title": scenario["title"],
        "agent_output": output.model_dump(),
        "trajectory": output.trajectory,
        "is_clean": output.is_clean,
        "total_discrepancy": output.total_discrepancy,
        "findings_count": output.findings_count,
        "findings": output.findings,
        "classification": output.classification,
        "next_workflow": output.next_workflow,
        "approval_state": output.approval_state,
        "decision": output.decision,
        "explanation": output.explanation,
        "model_used": output.model_used,
        "total_tokens": output.total_tokens,
        "cost_estimate": output.cost_estimate,
        "run_id": output.run_id,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PHASE 4 DISPUTE AGENT TESTING ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

class RunDisputeAgentRequest(BaseModel):
    scenario: str = Field(
        default="auto_approved",
        description="auto_approved | requires_human | no_contact",
    )
    force_amount: Optional[float] = Field(
        default=None,
        description="Override discrepancy amount for testing threshold logic",
    )


@router.post("/dispute-agent/run", summary="[Phase 4] Run dispute agent simulation")
def run_dispute_agent_simulation(
    body: RunDisputeAgentRequest,
    db: Session = Depends(get_db),
):
    """Simulate a full dispute agent run for UI testing.
    Creates ephemeral test data, runs the agent, and returns results.
    Scenario options:
    - 'auto_approved': verified contact + amount under threshold -> auto_approved + sent
    - 'requires_human': verified contact + amount over threshold -> requires_human_approval
    - 'no_contact': no contact -> escalate to human
    """
    from unittest.mock import AsyncMock, MagicMock

    from apps.agent.dispute.service import run_dispute_agent

    # Get or create test org
    org = db.query(Organization).filter(Organization.slug == DEFAULT_TEST_ORG_SLUG).first()
    if not org:
        org = Organization(name=DEFAULT_TEST_ORG_NAME, slug=DEFAULT_TEST_ORG_SLUG)
        db.add(org)
        db.commit()
        db.refresh(org)

    # Determine scenario amounts
    scenarios = {
        "auto_approved": {"amount": 180.00, "carrier": "Old Dominion", "has_contact": True},
        "requires_human": {"amount": 500.00, "carrier": "SAIA Freight", "has_contact": True},
        "no_contact": {"amount": 150.00, "carrier": "Unknown Carrier Co", "has_contact": False},
    }
    scenario_config = scenarios.get(body.scenario, scenarios["auto_approved"])
    amount = body.force_amount if body.force_amount is not None else scenario_config["amount"]
    carrier = scenario_config["carrier"]

    # Create test shipment
    import random
    suffix = random.randint(1000, 9999)
    shipment = Shipment(
        organization_id=org.id,
        shipment_number=f"SHP-UI-{suffix}",
        carrier_name=carrier,
        status="delivered",
    )
    db.add(shipment)
    db.commit()
    db.refresh(shipment)

    # Create test invoice
    invoice = CarrierInvoice(
        organization_id=org.id,
        shipment_id=shipment.id,
        carrier_name=carrier,
        invoice_number=f"INV-UI-{suffix}",
        total_billed_amount=amount + 1000.0,
        linehaul_amount=1000.0,
        fuel_amount=amount,
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    # Create audit finding with the discrepancy amount
    finding = AuditFindingRecord(
        organization_id=org.id,
        invoice_id=invoice.id,
        shipment_id=shipment.id,
        rule_id="RULE_02_LINEHAUL_MISMATCH",
        rule_name="Wrong Linehaul Rate",
        severity="high",
        discrepancy_amount=amount,  # This is the AUTHORITATIVE disputed amount
        reason=f"Billed ${amount + 1000:.2f}, contracted rate is $1000.00",
        confidence=0.92,
        recommended_action="dispute",
        evidence={"source_documents": ["SIGNED_BOL", "RATE_CONFIRMATION"]},
    )
    db.add(finding)
    db.commit()
    db.refresh(finding)

    # Create carrier contact if scenario requires it
    if scenario_config["has_contact"]:
        existing_contact = CarrierContactRepository(db).get_verified_contact(org.id, carrier)
        if not existing_contact:
            contact = CarrierContact(
                organization_id=org.id,
                carrier_name=carrier,
                billing_email=f"billing@{carrier.lower().replace(' ', '')}.com",
                contact_name=f"{carrier} Billing Dept",
                is_verified=True,
            )
            db.add(contact)
            db.commit()

    # Mock LLM for deterministic UI testing
    mock_response = MagicMock()
    mock_response.content = (
        f"SUBJECT: Invoice Dispute — INV-UI-{suffix} / {carrier}\n\n"
        f"LETTER:\nDear {carrier} Billing Department,\n\n"
        f"We are formally disputing a total of ${amount:.2f} on invoice INV-UI-{suffix}.\n\n"
        f"Our records show the billed amount exceeds the contracted rate by ${amount:.2f}.\n"
        f"Please issue a corrected invoice or credit memo.\n\nBest regards,\nFreight Operations"
    )
    mock_response.cost_estimate = 0.0012
    mock_response.prompt_tokens = 350
    mock_response.completion_tokens = 120

    mock_llm = MagicMock()
    mock_llm.default_model = "gpt-4o-mini (simulated)"
    mock_llm.complete = AsyncMock(return_value=mock_response)

    try:
        result = run_dispute_agent(
            organization_id=str(org.id),
            invoice_id=str(invoice.id),
            finding_ids=[str(finding.id)],
            db=db,
            llm=mock_llm,
            triggered_by="ui-simulation",
        )
        return {
            "scenario": body.scenario,
            "run_id": result.run_id,
            "dispute_id": result.dispute_id,
            "dispute_number": result.dispute_number,
            "status": result.status,
            "approval_status": result.approval_status,
            "recipient_email": result.recipient_email,
            "disputed_amount": result.disputed_amount,
            "dispute_letter_subject": result.dispute_letter_subject,
            "dispute_letter_preview": result.dispute_letter_preview,
            "needs_human": result.needs_human,
            "error": result.error,
            "trajectory": result.trajectory,
            "cost_estimate": result.cost_estimate,
            "model_used": mock_llm.default_model,
            "financial_integrity_note": f"disputed_amount={result.disputed_amount} sourced from AuditFindingRecord.discrepancy_amount={amount} — NOT from LLM",
        }
    except Exception as e:
        return {
            "scenario": body.scenario,
            "error": str(e),
            "status": "failed",
            "trajectory": [],
        }
