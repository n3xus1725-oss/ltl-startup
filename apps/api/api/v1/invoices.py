"""Phase 3.1 & 3.3 — Carrier Invoice Intake and Billing Audit API."""

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from packages.documents.normalizer import DocumentNormalizer
from packages.domain.models import Organization
from packages.domain.resolver import ShipmentResolver
from packages.rules.audit_engine import DeterministicAuditEngine
from packages.storage.db import get_db
from packages.storage.repositories.audit_findings import AuditFindingRepository
from packages.storage.repositories.contracts import RateContractRepository
from packages.storage.repositories.documents import DocumentRepository
from packages.storage.repositories.invoices import InvoiceRepository
from packages.storage.repositories.shipments import ShipmentRepository

router = APIRouter(prefix="/invoices", tags=["Invoices & Billing Audit"])


def _get_or_create_default_org(db: Session) -> Organization:
    org = db.query(Organization).first()
    if not org:
        org = Organization(name="Freight Logistics Corp", slug="freight-logistics")
        db.add(org)
        db.commit()
        db.refresh(org)
    return org


class InvoiceIngestRequest(BaseModel):
    carrier_name: Optional[str] = None
    invoice_number: Optional[str] = None
    document_text: str = Field(..., description="Raw text of invoice document or email body")
    shipment_id: Optional[str] = None
    total_billed_amount: Optional[float] = None
    linehaul_amount: Optional[float] = None
    fuel_amount: Optional[float] = None
    accessorial_amount: Optional[float] = None
    weight_lbs: Optional[float] = None
    freight_class: Optional[str] = None
    pallet_count: Optional[int] = None
    document_id: Optional[str] = None


@router.post("/ingest", status_code=status.HTTP_201_CREATED)
async def ingest_invoice(
    payload: InvoiceIngestRequest,
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Ingest a carrier invoice, extract fields, associate shipment, and detect duplicates."""
    organization = db.query(Organization).filter_by(id=uuid.UUID(org_id)).first() if org_id else _get_or_create_default_org(db)
    inv_repo = InvoiceRepository(db)
    shp_repo = ShipmentRepository(db)
    doc_repo = DocumentRepository(db)

    # 1. Normalize text if fields not fully supplied
    normalized = DocumentNormalizer.normalize_invoice(payload.document_text)

    carrier_name = payload.carrier_name or normalized.carrier_name or "Unknown Carrier"
    inv_num = payload.invoice_number or normalized.invoice_number or f"INV-{uuid.uuid4().hex[:8].upper()}"
    total = payload.total_billed_amount if payload.total_billed_amount is not None else (normalized.total_billed_amount or 0.0)
    linehaul = payload.linehaul_amount if payload.linehaul_amount is not None else (normalized.linehaul_amount or total)
    fuel = payload.fuel_amount if payload.fuel_amount is not None else (normalized.fuel_amount or 0.0)
    acc = payload.accessorial_amount if payload.accessorial_amount is not None else (normalized.accessorial_amount or 0.0)
    weight = payload.weight_lbs if payload.weight_lbs is not None else normalized.weight_lbs
    f_class = payload.freight_class or normalized.freight_class
    pallets = payload.pallet_count if payload.pallet_count is not None else normalized.pallet_count

    # 2. Duplicate Detection
    existing = inv_repo.check_duplicate(organization.id, carrier_name, inv_num)
    is_duplicate = existing is not None

    # 3. Shipment Association via Resolver
    resolved_shipment_id = None
    if payload.shipment_id:
        resolved_shipment_id = uuid.UUID(payload.shipment_id)
    else:
        resolver = ShipmentResolver(shp_repo)
        text_to_search = f"Invoice {inv_num} for Load {normalized.load_id or ''} BOL {normalized.bol_number or ''} {payload.document_text}"
        res = await resolver.resolve(
            organization_id=organization.id,
            subject=f"Invoice {inv_num}",
            body=text_to_search,
        )
        if res.matched_entity:
            entity_id = res.matched_entity.get("id") if isinstance(res.matched_entity, dict) else getattr(res.matched_entity, "id", None)
            if entity_id:
                resolved_shipment_id = uuid.UUID(str(entity_id))

    # If duplicate, return existing record immediately without re-inserting
    if is_duplicate and existing:
        return {
            "invoice_id": str(existing.id),
            "organization_id": str(existing.organization_id),
            "carrier_name": existing.carrier_name,
            "invoice_number": existing.invoice_number,
            "total_billed_amount": existing.total_billed_amount,
            "linehaul_amount": existing.linehaul_amount,
            "fuel_amount": existing.fuel_amount,
            "accessorial_amount": existing.accessorial_amount,
            "shipment_id": str(existing.shipment_id) if existing.shipment_id else None,
            "is_duplicate": True,
            "status": "duplicate",
            "extracted_items": normalized.line_items,
        }

    # 4. Store document reference if not already created
    doc_id = uuid.UUID(payload.document_id) if payload.document_id else None
    if not doc_id:
        doc = doc_repo.create(
            organization_id=organization.id,
            shipment_id=resolved_shipment_id,
            document_type="INVOICE",
            file_name=f"invoice_{inv_num}.txt",
            storage_path=f"/invoices/{organization.id}/{inv_num}.txt",
            extracted_data=normalized.model_dump(),
        )
        doc_id = doc.id

    # 5. Persist Invoice Record
    inv_record = inv_repo.create_invoice(
        organization_id=organization.id,
        carrier_name=carrier_name,
        invoice_number=inv_num,
        total_billed_amount=total,
        shipment_id=resolved_shipment_id,
        document_id=doc_id,
        linehaul_amount=linehaul,
        fuel_amount=fuel,
        accessorial_amount=acc,
        weight_lbs=weight,
        freight_class=f_class,
        pallet_count=pallets,
        line_items=normalized.line_items,
        status="received",
    )

    return {
        "invoice_id": str(inv_record.id),
        "organization_id": str(inv_record.organization_id),
        "carrier_name": inv_record.carrier_name,
        "invoice_number": inv_record.invoice_number,
        "total_billed_amount": inv_record.total_billed_amount,
        "linehaul_amount": inv_record.linehaul_amount,
        "fuel_amount": inv_record.fuel_amount,
        "accessorial_amount": inv_record.accessorial_amount,
        "shipment_id": str(inv_record.shipment_id) if inv_record.shipment_id else None,
        "is_duplicate": is_duplicate,
        "status": inv_record.status,
        "extracted_items": normalized.line_items,
    }


@router.post("/{invoice_id}/audit")
def run_billing_audit(
    invoice_id: str,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Execute the deterministic audit engine across all 10 rules for an invoice."""
    inv_repo = InvoiceRepository(db)
    shp_repo = ShipmentRepository(db)
    contract_repo = RateContractRepository(db)
    finding_repo = AuditFindingRepository(db)
    doc_repo = DocumentRepository(db)

    inv = inv_repo.get_by_invoice_id(uuid.UUID(invoice_id))
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")

    # Load linked shipment
    shipment_dict = None
    if inv.shipment_id:
        shp = shp_repo.get_by_id(inv.organization_id, inv.shipment_id)
        if shp:
            shipment_dict = {
                "id": str(shp.id),
                "shipment_number": shp.shipment_number,
                "status": shp.status,
                "total_charges": shp.total_charges,
                "canonical_data": shp.canonical_data or {},
            }

    # Load matching contract
    contract_dict = None
    contract = contract_repo.find_matching_contract(inv.organization_id, inv.carrier_name)
    if contract:
        contract_dict = {
            "id": str(contract.id),
            "carrier_name": contract.carrier_name,
            "contract_number": contract.contract_number,
            "base_rate": contract.base_rate,
            "minimum_charge": contract.minimum_charge,
            "rate_type": contract.rate_type,
            "fuel_schedule": contract.fuel_schedule or {},
            "accessorial_schedule": contract.accessorial_schedule or {},
            "class_rate_rules": contract.class_rate_rules or {},
        }

    # Load existing invoices for duplicate detection
    existing_invoices = [
        {
            "id": str(i.id),
            "invoice_number": i.invoice_number,
            "carrier_name": i.carrier_name,
            "total_billed_amount": i.total_billed_amount,
            "status": i.status,
        }
        for i in inv_repo.list_invoices(inv.organization_id, limit=100)
    ]

    # Load attached documents and types
    doc_types = []
    scale_ticket_dict = None
    bol_dict = None
    pod_dict = None

    if inv.shipment_id:
        docs = doc_repo.list_by_shipment(inv.organization_id, inv.shipment_id)
        for d in docs:
            doc_types.append(d.document_type)
            if d.document_type == "SCALE_TICKET" and d.extracted_data:
                scale_ticket_dict = d.extracted_data
            elif d.document_type == "BILL_OF_LADING" and d.extracted_data:
                bol_dict = d.extracted_data
            elif d.document_type == "PROOF_OF_DELIVERY" and d.extracted_data:
                pod_dict = d.extracted_data

    # Convert invoice to dict
    inv_dict = {
        "id": str(inv.id),
        "invoice_number": inv.invoice_number,
        "carrier_name": inv.carrier_name,
        "total_billed_amount": inv.total_billed_amount,
        "linehaul_amount": inv.linehaul_amount,
        "fuel_amount": inv.fuel_amount,
        "accessorial_amount": inv.accessorial_amount,
        "weight_lbs": inv.weight_lbs,
        "freight_class": inv.freight_class,
        "pallet_count": inv.pallet_count,
        "line_items": inv.line_items or [],
        "accessorials": [item for item in (inv.line_items or []) if item.get("code") not in ("LINEHAUL", "FUEL")],
    }

    # Execute Deterministic Audit Engine
    engine = DeterministicAuditEngine()
    report = engine.audit_invoice(
        invoice=inv_dict,
        shipment=shipment_dict,
        contract=contract_dict,
        scale_ticket=scale_ticket_dict,
        bol=bol_dict,
        pod=pod_dict,
        existing_invoices=existing_invoices,
        attached_document_types=doc_types,
    )

    # Persist findings to database
    for f in report.findings:
        finding_repo.create_finding(
            organization_id=inv.organization_id,
            invoice_id=inv.id,
            rule_id=f.rule_id,
            rule_name=f.rule_name,
            severity=f.severity.value,
            reason=f.reason,
            discrepancy_amount=f.difference,
            expected_value=f.expected_value,
            billed_value=f.billed_value,
            shipment_id=inv.shipment_id,
            confidence=f.confidence,
            evidence={"source_documents": f.source_documents, "references": f.evidence_references},
            recommended_action=f.recommended_action,
        )

    # Update invoice audit status
    audit_status = "clean" if report.is_clean else "discrepant"
    inv_repo.update_status(inv.organization_id, inv.id, status="audited", audit_status=audit_status)

    return report.model_dump()


@router.get("")
def list_invoices(
    shipment_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """List carrier invoices."""
    organization = db.query(Organization).filter_by(id=uuid.UUID(org_id)).first() if org_id else _get_or_create_default_org(db)
    repo = InvoiceRepository(db)
    shp_uuid = uuid.UUID(shipment_id) if shipment_id else None
    invoices = repo.list_invoices(organization.id, shipment_id=shp_uuid, status=status)
    return [
        {
            "id": str(i.id),
            "invoice_number": i.invoice_number,
            "carrier_name": i.carrier_name,
            "total_billed_amount": i.total_billed_amount,
            "linehaul_amount": i.linehaul_amount,
            "fuel_amount": i.fuel_amount,
            "accessorial_amount": i.accessorial_amount,
            "weight_lbs": i.weight_lbs,
            "freight_class": i.freight_class,
            "pallet_count": i.pallet_count,
            "shipment_id": str(i.shipment_id) if i.shipment_id else None,
            "status": i.status,
            "audit_status": i.audit_status,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        }
        for i in invoices
    ]


@router.get("/{invoice_id}/findings")
def get_invoice_findings(
    invoice_id: str,
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Get audit discrepancy findings for an invoice."""
    inv_repo = InvoiceRepository(db)
    finding_repo = AuditFindingRepository(db)
    inv = inv_repo.get_by_invoice_id(uuid.UUID(invoice_id))
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    findings = finding_repo.list_findings_for_invoice(inv.organization_id, inv.id)
    return [
        {
            "id": str(f.id),
            "rule_id": f.rule_id,
            "rule_name": f.rule_name,
            "severity": f.severity,
            "reason": f.reason,
            "discrepancy_amount": f.discrepancy_amount,
            "expected_value": f.expected_value,
            "billed_value": f.billed_value,
            "confidence": f.confidence,
            "evidence": f.evidence,
            "recommended_action": f.recommended_action,
            "status": f.status,
            "created_at": f.created_at.isoformat() if f.created_at else None,
        }
        for f in findings
    ]
