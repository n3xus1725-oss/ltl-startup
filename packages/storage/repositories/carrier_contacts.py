"""Repository for CarrierContact — verified carrier billing contact registry."""

import uuid
from typing import Optional

from sqlalchemy.orm import Session

from packages.domain.models import CarrierContact


class CarrierContactRepository:
    """CRUD repository for verified carrier billing contacts.
    These are the ONLY source of truth for dispute recipient email resolution."""

    def __init__(self, db: Session):
        self.db = db

    def get_verified_contact(self, organization_id: uuid.UUID, carrier_name: str) -> Optional[CarrierContact]:
        """Return the verified billing contact for a carrier, or None if not found.
        The caller MUST escalate to human review if this returns None.
        NEVER synthesize or guess an email address."""
        return (
            self.db.query(CarrierContact)
            .filter(
                CarrierContact.organization_id == organization_id,
                CarrierContact.carrier_name == carrier_name,
                CarrierContact.is_verified.is_(True),
            )
            .first()
        )

    def create_contact(
        self,
        organization_id: uuid.UUID,
        carrier_name: str,
        billing_email: str,
        contact_name: Optional[str] = None,
        phone: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> CarrierContact:
        """Register a new verified carrier billing contact."""
        contact = CarrierContact(
            organization_id=organization_id,
            carrier_name=carrier_name,
            billing_email=billing_email,
            contact_name=contact_name,
            phone=phone,
            notes=notes,
            is_verified=True,
        )
        self.db.add(contact)
        self.db.commit()
        self.db.refresh(contact)
        return contact

    def list_contacts(self, organization_id: uuid.UUID) -> list[CarrierContact]:
        """List all carrier contacts for an organization."""
        return (
            self.db.query(CarrierContact)
            .filter(CarrierContact.organization_id == organization_id)
            .order_by(CarrierContact.carrier_name)
            .all()
        )

    def update_contact(
        self,
        organization_id: uuid.UUID,
        contact_id: uuid.UUID,
        **kwargs,
    ) -> Optional[CarrierContact]:
        """Update a carrier contact record."""
        contact = (
            self.db.query(CarrierContact)
            .filter(
                CarrierContact.id == contact_id,
                CarrierContact.organization_id == organization_id,
            )
            .first()
        )
        if not contact:
            return None
        for key, value in kwargs.items():
            if hasattr(contact, key):
                setattr(contact, key, value)
        self.db.commit()
        self.db.refresh(contact)
        return contact

    def delete_contact(self, organization_id: uuid.UUID, contact_id: uuid.UUID) -> bool:
        """Remove a carrier contact."""
        contact = (
            self.db.query(CarrierContact)
            .filter(
                CarrierContact.id == contact_id,
                CarrierContact.organization_id == organization_id,
            )
            .first()
        )
        if not contact:
            return False
        self.db.delete(contact)
        self.db.commit()
        return True
