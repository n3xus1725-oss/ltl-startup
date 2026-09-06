"""Carrier Contacts API — verified carrier billing contact registry.

These contacts are the ONLY permitted source of dispute recipient emails.
The dispute agent will NEVER send to an address not registered here.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from packages.storage.db import get_db
from packages.storage.repositories.carrier_contacts import CarrierContactRepository

router = APIRouter(prefix="/carrier-contacts", tags=["Carrier Contacts"])


class CreateCarrierContactRequest(BaseModel):
    organization_id: str = Field(..., description="Organization UUID")
    carrier_name: str = Field(..., description="Carrier name (must match carrier_name in invoices)")
    billing_email: EmailStr = Field(..., description="Verified carrier billing email — the ONLY source for dispute recipients")
    contact_name: Optional[str] = Field(default=None)
    phone: Optional[str] = Field(default=None)
    notes: Optional[str] = Field(default=None)


class UpdateCarrierContactRequest(BaseModel):
    billing_email: Optional[str] = None
    contact_name: Optional[str] = None
    phone: Optional[str] = None
    notes: Optional[str] = None
    is_verified: Optional[bool] = None


def _serialize_contact(c) -> dict:
    return {
        "id": str(c.id),
        "organization_id": str(c.organization_id),
        "carrier_name": c.carrier_name,
        "billing_email": c.billing_email,
        "contact_name": c.contact_name,
        "phone": c.phone,
        "notes": c.notes,
        "is_verified": c.is_verified,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


@router.get("", summary="List all verified carrier billing contacts")
def list_carrier_contacts(
    organization_id: str,
    db: Session = Depends(get_db),
):
    repo = CarrierContactRepository(db)
    contacts = repo.list_contacts(uuid.UUID(organization_id))
    return {"contacts": [_serialize_contact(c) for c in contacts], "count": len(contacts)}


@router.post("", status_code=status.HTTP_201_CREATED, summary="Register a verified carrier billing contact")
def create_carrier_contact(
    body: CreateCarrierContactRequest,
    db: Session = Depends(get_db),
):
    """Register a verified carrier billing contact. This is the ONLY way to authorize
    a recipient email for the dispute agent. Emails not registered here will never receive disputes."""
    repo = CarrierContactRepository(db)
    try:
        contact = repo.create_contact(
            organization_id=uuid.UUID(body.organization_id),
            carrier_name=body.carrier_name,
            billing_email=body.billing_email,
            contact_name=body.contact_name,
            phone=body.phone,
            notes=body.notes,
        )
        return {"contact": _serialize_contact(contact), "message": "Carrier contact registered successfully"}
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.put("/{contact_id}", summary="Update a carrier contact")
def update_carrier_contact(
    contact_id: str,
    organization_id: str,
    body: UpdateCarrierContactRequest,
    db: Session = Depends(get_db),
):
    repo = CarrierContactRepository(db)
    updated = repo.update_contact(
        organization_id=uuid.UUID(organization_id),
        contact_id=uuid.UUID(contact_id),
        **{k: v for k, v in body.model_dump().items() if v is not None},
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Carrier contact not found")
    return {"contact": _serialize_contact(updated)}


@router.delete("/{contact_id}", summary="Remove a carrier contact")
def delete_carrier_contact(
    contact_id: str,
    organization_id: str,
    db: Session = Depends(get_db),
):
    repo = CarrierContactRepository(db)
    deleted = repo.delete_contact(
        organization_id=uuid.UUID(organization_id),
        contact_id=uuid.UUID(contact_id),
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Carrier contact not found")
    return {"deleted": True, "contact_id": contact_id}
