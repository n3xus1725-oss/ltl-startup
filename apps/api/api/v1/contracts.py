"""Phase 3.2 — Rate Contract API Endpoints."""

import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from packages.domain.contracts import RateContractCreate
from packages.domain.models import Organization
from packages.storage.db import get_db
from packages.storage.repositories.contracts import RateContractRepository

router = APIRouter(prefix="/contracts", tags=["Rate Contracts"])


def _get_or_create_default_org(db: Session) -> Organization:
    org = db.query(Organization).first()
    if not org:
        org = Organization(name="Freight Logistics Corp", slug="freight-logistics")
        db.add(org)
        db.commit()
        db.refresh(org)
    return org


@router.post("", status_code=status.HTTP_201_CREATED)
def create_contract(
    payload: RateContractCreate,
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Create a new carrier rate contract."""
    organization = db.query(Organization).filter_by(id=uuid.UUID(org_id)).first() if org_id else _get_or_create_default_org(db)
    repo = RateContractRepository(db)
    contract = repo.create_contract(
        organization_id=organization.id,
        carrier_name=payload.carrier_name,
        contract_number=payload.contract_number,
        customer_name=payload.customer_name,
        lane_origin_state=payload.lane_origin_state,
        lane_origin_zip_prefix=payload.lane_origin_zip_prefix,
        lane_dest_state=payload.lane_dest_state,
        lane_dest_zip_prefix=payload.lane_dest_zip_prefix,
        effective_start_date=payload.effective_start_date,
        effective_end_date=payload.effective_end_date,
        rate_type=payload.rate_type.value if hasattr(payload.rate_type, "value") else str(payload.rate_type),
        base_rate=payload.base_rate,
        minimum_charge=payload.minimum_charge,
        fuel_schedule=payload.fuel_schedule,
        accessorial_schedule=payload.accessorial_schedule,
        class_rate_rules=payload.class_rate_rules,
        is_active=payload.is_active,
    )
    return {
        "id": str(contract.id),
        "organization_id": str(contract.organization_id),
        "carrier_name": contract.carrier_name,
        "contract_number": contract.contract_number,
        "base_rate": contract.base_rate,
        "minimum_charge": contract.minimum_charge,
        "rate_type": contract.rate_type,
        "is_active": contract.is_active,
        "created_at": contract.created_at.isoformat() if contract.created_at else None,
    }


@router.get("")
def list_contracts(
    carrier_name: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """List rate contracts for an organization."""
    organization = db.query(Organization).filter_by(id=uuid.UUID(org_id)).first() if org_id else _get_or_create_default_org(db)
    repo = RateContractRepository(db)
    contracts = repo.list_contracts(organization.id, carrier_name=carrier_name, is_active=is_active)
    return [
        {
            "id": str(c.id),
            "organization_id": str(c.organization_id),
            "carrier_name": c.carrier_name,
            "contract_number": c.contract_number,
            "lane_origin_state": c.lane_origin_state,
            "lane_dest_state": c.lane_dest_state,
            "base_rate": c.base_rate,
            "minimum_charge": c.minimum_charge,
            "rate_type": c.rate_type,
            "fuel_schedule": c.fuel_schedule,
            "accessorial_schedule": c.accessorial_schedule,
            "is_active": c.is_active,
        }
        for c in contracts
    ]


@router.get("/{contract_id}")
def get_contract(
    contract_id: str,
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Get contract details by ID."""
    organization = db.query(Organization).filter_by(id=uuid.UUID(org_id)).first() if org_id else _get_or_create_default_org(db)
    repo = RateContractRepository(db)
    c = repo.get_by_id(organization.id, uuid.UUID(contract_id))
    if not c:
        raise HTTPException(status_code=404, detail="Rate contract not found")
    return {
        "id": str(c.id),
        "organization_id": str(c.organization_id),
        "carrier_name": c.carrier_name,
        "contract_number": c.contract_number,
        "lane_origin_state": c.lane_origin_state,
        "lane_dest_state": c.lane_dest_state,
        "base_rate": c.base_rate,
        "minimum_charge": c.minimum_charge,
        "rate_type": c.rate_type,
        "fuel_schedule": c.fuel_schedule,
        "accessorial_schedule": c.accessorial_schedule,
        "class_rate_rules": c.class_rate_rules,
        "is_active": c.is_active,
    }
