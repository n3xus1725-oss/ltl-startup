"""
Production Dashboard API endpoints for fetching real-time shipments and audit logs.
"""

import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import desc
from sqlalchemy.orm import Session

from packages.domain.models import AuditLog, Organization, Shipment
from packages.storage.db import get_db

router = APIRouter(prefix="/test-ui", tags=["Dashboard API"])

def _get_org_from_request(request: Request, db: Session) -> Organization:
    auth_token = request.cookies.get("auth_token")
    if auth_token:
        try:
            org = db.query(Organization).filter(Organization.id == uuid.UUID(auth_token)).first()
            if org:
                return org
        except Exception:
            pass
            
    # Fallback for demo if no cookie
    org = db.query(Organization).filter(Organization.slug == "nexus-freight-demo").first()
    if not org:
        org = Organization(name="Nexus Freight Logistics", slug="nexus-freight-demo", is_active=True)
        db.add(org)
        db.commit()
        db.refresh(org)
    return org

@router.get("/shipments")
def list_dashboard_shipments(request: Request, db: Session = Depends(get_db)):
    """List current shipments in Supabase."""
    org = _get_org_from_request(request, db)
    shipments = db.query(Shipment).filter(Shipment.organization_id == org.id).order_by(desc(Shipment.created_at)).all()
    
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
            "total_amount_billed": s.total_amount_billed,
            "weight_lbs": s.weight_lbs,
            "pallet_count": s.pallet_count,
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        }
        for s in shipments
    ]

@router.get("/audit-logs")
def list_dashboard_audit_logs(request: Request, limit: int = 20, db: Session = Depends(get_db)):
    """List recent audit log entries in Supabase."""
    org = _get_org_from_request(request, db)
    records = db.query(AuditLog).filter(AuditLog.organization_id == org.id).order_by(desc(AuditLog.created_at)).limit(limit).all()
    
    return [
        {
            "id": str(r.id),
            "event_type": r.event_id,
            "action_taken": r.action,
            "actor_type": r.actor_type,
            "actor_id": r.actor_id,
            "idempotency_key": r.idempotency_key,
            "timestamp": r.created_at.isoformat() if r.created_at else None,
        }
        for r in records
    ]
