"""Shipment repository."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from packages.domain.models import Shipment, ShipmentEvent
from packages.storage.repositories.base import BaseRepository


class ShipmentRepository(BaseRepository[Shipment]):
    """Repository for canonical shipment entity queries and event persistence."""

    def __init__(self, db: Session):
        super().__init__(Shipment, db)

    def create(
        self,
        organization_id: uuid.UUID,
        shipment_number: str,
        carrier_name: Optional[str] = None,
        carrier_reference: Optional[str] = None,
        bol_number: Optional[str] = None,
        load_id: Optional[str] = None,
        external_invoice_id: Optional[str] = None,
        origin_address: Optional[Dict[str, Any]] = None,
        destination_address: Optional[Dict[str, Any]] = None,
        status: str = "created",
        pickup_date: Optional[datetime] = None,
        delivery_date: Optional[datetime] = None,
        eta: Optional[datetime] = None,
        weight_lbs: Optional[float] = None,
        pallet_count: Optional[int] = None,
        total_charges: Optional[float] = None,
        metadata_payload: Optional[Dict[str, Any]] = None,
    ) -> Shipment:
        shipment = Shipment(
            id=uuid.uuid4(),
            organization_id=organization_id,
            shipment_number=shipment_number,
            carrier_name=carrier_name,
            carrier_reference=carrier_reference,
            bol_number=bol_number,
            load_id=load_id,
            external_invoice_id=external_invoice_id,
            origin_address=origin_address,
            destination_address=destination_address,
            status=status,
            pickup_date=pickup_date,
            delivery_date=delivery_date,
            eta=eta,
            weight_lbs=weight_lbs,
            pallet_count=pallet_count,
            total_charges=total_charges,
            metadata_payload=metadata_payload or {},
        )
        self.db.add(shipment)
        self.db.commit()
        self.db.refresh(shipment)
        return shipment

    def get_by_shipment_number(self, organization_id: uuid.UUID, shipment_number: str) -> Optional[Shipment]:
        stmt = select(Shipment).where(
            Shipment.organization_id == organization_id,
            Shipment.shipment_number == shipment_number,
        )
        return self.db.execute(stmt).scalars().first()

    def find_by_identifier(
        self, organization_id: uuid.UUID, identifier_value: str
    ) -> List[Shipment]:
        """Find shipments matching any candidate identifier (shipment#, carrier_ref/PRO, BOL, load_id, invoice_id)."""
        stmt = select(Shipment).where(
            Shipment.organization_id == organization_id,
            or_(
                Shipment.shipment_number == identifier_value,
                Shipment.carrier_reference == identifier_value,
                Shipment.bol_number == identifier_value,
                Shipment.load_id == identifier_value,
                Shipment.external_invoice_id == identifier_value,
            ),
        )
        return list(self.db.execute(stmt).scalars().all())

    def update_shipment(
        self,
        organization_id: uuid.UUID,
        shipment_id: uuid.UUID,
        **updates: Any,
    ) -> Optional[Shipment]:
        shipment = self.get_by_id(organization_id, shipment_id)
        if not shipment:
            return None

        for key, value in updates.items():
            if hasattr(shipment, key) and value is not None:
                setattr(shipment, key, value)

        self.db.commit()
        self.db.refresh(shipment)
        return shipment

    def add_event(
        self,
        organization_id: uuid.UUID,
        shipment_id: uuid.UUID,
        event_type: str,
        source: str,
        raw_payload: Optional[Dict[str, Any]] = None,
        normalized_payload: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        event_timestamp: Optional[datetime] = None,
    ) -> ShipmentEvent:
        """Add an immutable event to shipment history. Returns existing event if idempotency_key matches."""
        if idempotency_key:
            existing = self.db.execute(
                select(ShipmentEvent).where(
                    ShipmentEvent.organization_id == organization_id,
                    ShipmentEvent.idempotency_key == idempotency_key,
                )
            ).scalars().first()
            if existing:
                return existing

        event = ShipmentEvent(
            id=uuid.uuid4(),
            organization_id=organization_id,
            shipment_id=shipment_id,
            event_type=event_type,
            event_timestamp=event_timestamp or datetime.now(timezone.utc),
            source=source,
            raw_payload=raw_payload,
            normalized_payload=normalized_payload,
            idempotency_key=idempotency_key,
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    def get_events(self, organization_id: uuid.UUID, shipment_id: uuid.UUID) -> List[ShipmentEvent]:
        stmt = (
            select(ShipmentEvent)
            .where(
                ShipmentEvent.organization_id == organization_id,
                ShipmentEvent.shipment_id == shipment_id,
            )
            .order_by(ShipmentEvent.event_timestamp.asc())
        )
        return list(self.db.execute(stmt).scalars().all())
