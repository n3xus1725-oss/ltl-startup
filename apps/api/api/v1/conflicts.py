"""Conflicts API: inspect and resolve detected data discrepancies."""

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from packages.domain.models import Organization
from packages.storage.db import get_db
from packages.storage.repositories.conflicts import ConflictRepository
from packages.storage.repositories.shipments import ShipmentRepository

router = APIRouter(prefix="/conflicts", tags=["Conflicts"])


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


class ResolveConflictRequest(BaseModel):
    organization_id: Optional[str] = None
    resolved_by: str = Field(..., description="User ID or Agent identifier resolving the conflict")
    winning_value: Any = Field(..., description="The chosen authoritative value")
    comment: Optional[str] = Field(default=None, description="Resolution rationale")


@router.get("")
def list_conflicts(
    organization_id: Optional[str] = None,
    shipment_id: Optional[str] = None,
    status_filter: Optional[str] = "open",
    db: Session = Depends(get_db),
):
    """List detected conflicts across organization or for a specific shipment."""
    org = _get_org(db, organization_id)
    conflict_repo = ConflictRepository(db)

    if shipment_id:
        conflicts = conflict_repo.list_by_shipment(org.id, uuid.UUID(shipment_id), status=status_filter)
    else:
        conflicts = conflict_repo.list_by_organization(org.id, status=status_filter)

    return [
        {
            "id": str(c.id),
            "organization_id": str(c.organization_id),
            "shipment_id": str(c.shipment_id),
            "field_name": c.field_name,
            "conflict_type": c.conflict_type,
            "source_a": c.source_a,
            "source_b": c.source_b,
            "severity": c.severity,
            "explanation": c.explanation,
            "recommended_workflow": c.recommended_workflow,
            "status": c.status,
            "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
            "resolved_by": c.resolved_by,
            "resolved_value": c.resolved_value,
            "created_at": c.created_at.isoformat(),
        }
        for c in conflicts
    ]


@router.get("/{conflict_id}")
def get_conflict_detail(
    conflict_id: str,
    organization_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Retrieve details for a single conflict."""
    org = _get_org(db, organization_id)
    conflict_repo = ConflictRepository(db)
    conflict = conflict_repo.get_by_id(org.id, uuid.UUID(conflict_id))
    if not conflict:
        raise HTTPException(status_code=404, detail=f"Conflict {conflict_id} not found")

    return {
        "id": str(conflict.id),
        "organization_id": str(conflict.organization_id),
        "shipment_id": str(conflict.shipment_id),
        "field_name": conflict.field_name,
        "conflict_type": conflict.conflict_type,
        "source_a": conflict.source_a,
        "source_b": conflict.source_b,
        "severity": conflict.severity,
        "explanation": conflict.explanation,
        "recommended_workflow": conflict.recommended_workflow,
        "status": conflict.status,
        "resolved_at": conflict.resolved_at.isoformat() if conflict.resolved_at else None,
        "resolved_by": conflict.resolved_by,
        "resolved_value": conflict.resolved_value,
        "created_at": conflict.created_at.isoformat(),
    }


@router.post("/{conflict_id}/resolve")
def resolve_conflict(
    conflict_id: str,
    payload: ResolveConflictRequest,
    db: Session = Depends(get_db),
):
    """Resolve a conflict by selecting an authoritative winning value."""
    org = _get_org(db, payload.organization_id)
    conflict_repo = ConflictRepository(db)
    shipment_repo = ShipmentRepository(db)

    conflict = conflict_repo.resolve_conflict(
        organization_id=org.id,
        conflict_id=uuid.UUID(conflict_id),
        resolved_by=payload.resolved_by,
        resolved_value=payload.winning_value,
        status="resolved",
    )
    if not conflict:
        raise HTTPException(status_code=404, detail=f"Conflict {conflict_id} not found")

    # Apply resolved value directly to the canonical shipment model
    canonical = shipment_repo.get_canonical(org.id, conflict.shipment_id) or {}
    field = conflict.field_name
    parts = field.split(".")
    curr = canonical
    for p in parts[:-1]:
        if p not in curr or not isinstance(curr[p], dict):
            curr[p] = {}
        curr = curr[p]
    curr[parts[-1]] = payload.winning_value

    shipment_repo.update_canonical(
        organization_id=org.id,
        shipment_id=conflict.shipment_id,
        canonical_data=canonical,
    )

    return {
        "status": "success",
        "conflict_id": str(conflict.id),
        "resolved_status": conflict.status,
        "winning_value": conflict.resolved_value,
        "resolved_by": conflict.resolved_by,
        "resolved_at": conflict.resolved_at.isoformat() if conflict.resolved_at else None,
    }
