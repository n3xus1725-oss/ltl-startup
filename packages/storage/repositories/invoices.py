"""Phase 3.1 — Carrier Invoice Repository."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from packages.domain.models import CarrierInvoice
from packages.storage.repositories.base import BaseRepository


class InvoiceRepository(BaseRepository[CarrierInvoice]):
    """Repository for managing carrier freight invoices, duplicate detection, and shipment associations."""

    def __init__(self, db: Session):
        super().__init__(CarrierInvoice, db)

    def get_by_invoice_id(self, invoice_id: uuid.UUID) -> Optional[CarrierInvoice]:
        """Look up an invoice directly by primary key."""
        stmt = select(CarrierInvoice).where(CarrierInvoice.id == invoice_id)
        return self.db.execute(stmt).scalars().first()

    def create_invoice(
        self,
        organization_id: uuid.UUID,
        carrier_name: str,
        invoice_number: str,
        total_billed_amount: float,
        shipment_id: Optional[uuid.UUID] = None,
        document_id: Optional[uuid.UUID] = None,
        invoice_date: Optional[datetime] = None,
        due_date: Optional[datetime] = None,
        currency: str = "USD",
        linehaul_amount: float = 0.0,
        fuel_amount: float = 0.0,
        accessorial_amount: float = 0.0,
        weight_lbs: Optional[float] = None,
        freight_class: Optional[str] = None,
        pallet_count: Optional[int] = None,
        remit_to_address: Optional[Dict[str, Any]] = None,
        line_items: Optional[List[Dict[str, Any]]] = None,
        status: str = "received",
        audit_status: str = "pending",
        idempotency_key: Optional[str] = None,
    ) -> CarrierInvoice:
        """Create and persist a carrier freight invoice."""
        inv = CarrierInvoice(
            organization_id=organization_id,
            shipment_id=shipment_id,
            document_id=document_id,
            carrier_name=carrier_name,
            invoice_number=invoice_number,
            invoice_date=invoice_date or datetime.now(timezone.utc),
            due_date=due_date,
            currency=currency,
            total_billed_amount=total_billed_amount,
            linehaul_amount=linehaul_amount,
            fuel_amount=fuel_amount,
            accessorial_amount=accessorial_amount,
            weight_lbs=weight_lbs,
            freight_class=freight_class,
            pallet_count=pallet_count,
            remit_to_address=remit_to_address,
            line_items=line_items or [],
            status=status,
            audit_status=audit_status,
            idempotency_key=idempotency_key or f"inv-{organization_id}-{carrier_name}-{invoice_number}",
        )
        self.db.add(inv)
        self.db.commit()
        self.db.refresh(inv)
        return inv

    def get_by_number(
        self, organization_id: uuid.UUID, carrier_name: str, invoice_number: str
    ) -> Optional[CarrierInvoice]:
        """Look up an invoice by carrier and invoice number within an organization."""
        stmt = select(CarrierInvoice).where(
            and_(
                CarrierInvoice.organization_id == organization_id,
                CarrierInvoice.carrier_name.ilike(f"%{carrier_name}%"),
                CarrierInvoice.invoice_number == invoice_number,
            )
        )
        return self.db.execute(stmt).scalars().first()

    def check_duplicate(
        self, organization_id: uuid.UUID, carrier_name: str, invoice_number: str
    ) -> Optional[CarrierInvoice]:
        """Check if an invoice with this number has already been processed or stored."""
        return self.get_by_number(organization_id, carrier_name, invoice_number)

    def link_shipment(
        self, organization_id: uuid.UUID, invoice_id: uuid.UUID, shipment_id: uuid.UUID
    ) -> Optional[CarrierInvoice]:
        """Associate an invoice with a resolved shipment."""
        inv = self.get_by_id(organization_id, invoice_id)
        if not inv:
            return None
        inv.shipment_id = shipment_id
        self.db.commit()
        self.db.refresh(inv)
        return inv

    def update_status(
        self,
        organization_id: uuid.UUID,
        invoice_id: uuid.UUID,
        status: str,
        audit_status: Optional[str] = None,
    ) -> Optional[CarrierInvoice]:
        """Update workflow status and audit status."""
        inv = self.get_by_id(organization_id, invoice_id)
        if not inv:
            return None
        inv.status = status
        if audit_status:
            inv.audit_status = audit_status
        self.db.commit()
        self.db.refresh(inv)
        return inv

    def list_invoices(
        self,
        organization_id: uuid.UUID,
        shipment_id: Optional[uuid.UUID] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[CarrierInvoice]:
        """List invoices for organization with optional filters."""
        stmt = select(CarrierInvoice).where(CarrierInvoice.organization_id == organization_id)
        if shipment_id:
            stmt = stmt.where(CarrierInvoice.shipment_id == shipment_id)
        if status:
            stmt = stmt.where(CarrierInvoice.status == status)
        stmt = stmt.order_by(CarrierInvoice.created_at.desc()).limit(limit)
        return list(self.db.execute(stmt).scalars().all())
