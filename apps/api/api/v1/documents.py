"""Documents API: upload, parse, and normalize freight documents."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from packages.documents.pipeline import DocumentProcessingPipeline
from packages.domain.models import Organization
from packages.domain.provenance import FieldProvenanceRecord, ProvenanceLedger
from packages.rules.authority import AssertionDecision, SourceAuthorityEngine
from packages.rules.conflict_engine import ConflictEngine
from packages.storage.db import get_db
from packages.storage.repositories.conflicts import ConflictRepository
from packages.storage.repositories.documents import DocumentRepository
from packages.storage.repositories.shipments import ShipmentRepository

router = APIRouter(prefix="/documents", tags=["Documents"])


def _get_org(db: Session, org_id_str: Optional[str] = None) -> Organization:
    if org_id_str:
        try:
            org = db.query(Organization).filter(Organization.id == uuid.UUID(org_id_str)).first()
            if org:
                return org
        except Exception:
            pass
    org = db.query(Organization).filter(Organization.slug == "nexus-freight-demo").first()
    if not org:
        org = Organization(name="Nexus Freight Logistics", slug="nexus-freight-demo", is_active=True)
        db.add(org)
        db.commit()
        db.refresh(org)
    return org


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_and_process_document(
    file: UploadFile = File(...),
    organization_id: Optional[str] = Form(default=None),
    shipment_id: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
):
    """Upload freight document, run through extraction & normalization pipeline,
    and reconcile with canonical shipment truth.
    """
    org = _get_org(db, organization_id)
    doc_repo = DocumentRepository(db)
    shipment_repo = ShipmentRepository(db)
    conflict_repo = ConflictRepository(db)
    authority_engine = SourceAuthorityEngine()

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    filename = file.filename or "uploaded_document.pdf"
    content_type = file.content_type or "application/octet-stream"

    # 1. Create document record in database
    target_shipment_uuid = uuid.UUID(shipment_id) if shipment_id else None
    doc = doc_repo.create(
        organization_id=org.id,
        file_name=filename,
        storage_path=f"/documents/{uuid.uuid4()}_{filename}",
        document_type="UNKNOWN",
        file_size_bytes=len(content),
        mime_type=content_type,
        shipment_id=target_shipment_uuid,
    )

    # 2. Run processing pipeline
    pipeline = DocumentProcessingPipeline(db=db)
    result = pipeline.process_content(
        filename=filename,
        content_bytes=content,
        mime_type=content_type,
        document_id=str(doc.id),
        organization_id=org.id,
    )

    # 3. Associate with shipment and reconcile canonical truth
    reconciliation_summary = {
        "updated_fields": [],
        "corroborated_fields": [],
        "rejected_fields": [],
        "conflicts_detected": [],
    }

    if target_shipment_uuid:
        shipment = shipment_repo.get_by_id(org.id, target_shipment_uuid)
        if shipment:
            canonical = shipment_repo.get_canonical(org.id, target_shipment_uuid) or {}
            ledger = ProvenanceLedger.from_dict(shipment.provenance_ledger)

            for assertion in result.provenance_assertions:
                field = assertion["field"]
                rec = FieldProvenanceRecord(
                    field=field,
                    value=assertion["value"],
                    source=assertion["source"],
                    source_id=assertion["source_id"],
                    authority=assertion.get("authority", 50),
                    confidence=assertion.get("confidence", 0.9),
                    writer="document_pipeline",
                    evidence=assertion.get("evidence"),
                )

                active_rec = ledger.get_active(field)
                decision = authority_engine.evaluate(active_rec, rec)

                if decision == AssertionDecision.ACCEPT_CANONICAL:
                    ledger.record_assertion(rec, is_active=True)
                    # Update canonical dictionary
                    parts = field.split(".")
                    curr = canonical
                    for p in parts[:-1]:
                        if p not in curr or not isinstance(curr[p], dict):
                            curr[p] = {}
                        curr = curr[p]
                    curr[parts[-1]] = assertion["value"]
                    reconciliation_summary["updated_fields"].append(field)

                elif decision == AssertionDecision.CORROBORATE:
                    ledger.record_assertion(rec, is_active=False)
                    reconciliation_summary["corroborated_fields"].append(field)

                elif decision == AssertionDecision.REJECT_LOWER_AUTHORITY:
                    ledger.record_assertion(rec, is_active=False)
                    reconciliation_summary["rejected_fields"].append(field)

                elif decision == AssertionDecision.FLAG_CONFLICT:
                    ledger.record_assertion(rec, is_active=False)
                    # Detect and persist conflict
                    conf = ConflictEngine.evaluate_value_mismatch(field, active_rec, rec)
                    if conf:
                        c_rec = conflict_repo.create_conflict(
                            organization_id=org.id,
                            shipment_id=target_shipment_uuid,
                            field_name=conf.field_name,
                            conflict_type=conf.conflict_type,
                            source_a=conf.source_a,
                            source_b=conf.source_b,
                            explanation=conf.explanation,
                            severity=conf.severity,
                            recommended_workflow=conf.recommended_workflow,
                            idempotency_key=f"conf-{target_shipment_uuid}-{field}-{doc.id}",
                        )
                        reconciliation_summary["conflicts_detected"].append({
                            "conflict_id": str(c_rec.id),
                            "field": field,
                            "severity": conf.severity,
                            "explanation": conf.explanation,
                        })

            # Commit updated canonical truth and provenance ledger
            shipment_repo.update_canonical(
                organization_id=org.id,
                shipment_id=target_shipment_uuid,
                canonical_data=canonical,
                provenance_ledger=ledger.to_dict(),
            )

    return {
        "document_id": str(doc.id),
        "file_name": doc.file_name,
        "classified_type": result.classified_type,
        "extracted_fields": result.extracted_fields,
        "assertions_count": len(result.provenance_assertions),
        "reconciliation": reconciliation_summary,
    }


@router.get("/{document_id}")
def get_document_details(
    document_id: str,
    organization_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Retrieve document metadata and extracted data."""
    org = _get_org(db, organization_id)
    doc_repo = DocumentRepository(db)
    doc = doc_repo.get_by_id(org.id, uuid.UUID(document_id))
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")

    return {
        "id": str(doc.id),
        "organization_id": str(doc.organization_id),
        "shipment_id": str(doc.shipment_id) if doc.shipment_id else None,
        "document_type": doc.document_type,
        "file_name": doc.file_name,
        "file_size_bytes": doc.file_size_bytes,
        "mime_type": doc.mime_type,
        "checksum_sha256": doc.checksum_sha256,
        "extracted_data": doc.extracted_data or {},
        "created_at": doc.created_at.isoformat(),
    }
