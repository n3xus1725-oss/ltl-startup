"""Phase 2.1 — Canonical Shipment Model.

Structured entities for freight domains:
- Shipment Parties (shipper, consignee, bill-to, carrier, broker, notify-party)
- Locations (origin, destination, stop-offs, intermediate cross-dock facilities, hours)
- Dates/Times (scheduled pickup/delivery, actual pickup/delivery, appointment windows, ETA)
- Freight Details (weight, weight type, pallet count, piece count, commodity, NMFC class, dimensions, hazmat)
- Pricing (currency, agreed rate, billed rate, linehaul, fuel surcharge, itemized accessorials)
- Carrier Details (carrier name, SCAC, MC/DOT, driver, tractor, trailer, seal)
- Billing References (load ID, BOL number, PRO number, invoice ID, PO numbers, customer/carrier refs)
- Operational Events (milestone history)
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PartyContact(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class ShipmentParty(BaseModel):
    name: str
    company_name: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    country: str = "USA"
    account_number: Optional[str] = None
    contact: Optional[PartyContact] = None


class ShipmentParties(BaseModel):
    shipper: Optional[ShipmentParty] = None
    consignee: Optional[ShipmentParty] = None
    bill_to: Optional[ShipmentParty] = None
    carrier: Optional[ShipmentParty] = None
    broker: Optional[ShipmentParty] = None
    notify_party: Optional[ShipmentParty] = None


class LocationPoint(BaseModel):
    facility_name: Optional[str] = None
    address_line1: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    country: str = "USA"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    operating_hours: Optional[str] = None
    special_instructions: Optional[str] = None


class StopOffLocation(LocationPoint):
    stop_sequence: int = 1
    stop_type: str = "stop_off"  # pickup, delivery, cross_dock, customs
    appointment_time: Optional[datetime] = None


class ShipmentLocations(BaseModel):
    origin: Optional[LocationPoint] = None
    destination: Optional[LocationPoint] = None
    stops: List[StopOffLocation] = Field(default_factory=list)


class ShipmentDates(BaseModel):
    scheduled_pickup: Optional[datetime] = None
    actual_pickup: Optional[datetime] = None
    scheduled_delivery: Optional[datetime] = None
    actual_delivery: Optional[datetime] = None
    eta: Optional[datetime] = None
    pickup_appointment_window: Optional[str] = None
    delivery_appointment_window: Optional[str] = None


class Dimensions(BaseModel):
    length_inches: Optional[float] = None
    width_inches: Optional[float] = None
    height_inches: Optional[float] = None


class FreightDetails(BaseModel):
    total_weight_lbs: Optional[float] = None
    weight_type: str = "actual"  # actual, verified_reweigh, estimated
    pallet_count: Optional[int] = None
    piece_count: Optional[int] = None
    commodity: Optional[str] = None
    nmfc_class: Optional[str] = None
    dimensions: Optional[Dimensions] = None
    hazmat: bool = False
    hazmat_class: Optional[str] = None
    temperature_controlled: bool = False
    target_temp_fahrenheit: Optional[float] = None


class AccessorialCharge(BaseModel):
    code: str  # e.g., LFT (Liftgate), DET (Detention), INS (Inside Delivery), RES (Residential)
    name: str
    amount: float
    is_approved: bool = True
    notes: Optional[str] = None


class ShipmentPricing(BaseModel):
    currency: str = "USD"
    agreed_total: Optional[float] = None
    billed_total: Optional[float] = None
    linehaul: Optional[float] = None
    fuel_surcharge: Optional[float] = None
    accessorials: List[AccessorialCharge] = Field(default_factory=list)

    def __init__(self, **data):
        if "total_agreed_rate" in data and "agreed_total" not in data:
            data["agreed_total"] = data.pop("total_agreed_rate")
        if "agreed_linehaul" in data and "linehaul" not in data:
            data["linehaul"] = data.pop("agreed_linehaul")
        if "total_billed_amount" in data and "billed_total" not in data:
            data["billed_total"] = data.pop("total_billed_amount")
        super().__init__(**data)

    @property
    def total_agreed_rate(self) -> Optional[float]:
        return self.agreed_total

    @property
    def agreed_linehaul(self) -> Optional[float]:
        return self.linehaul


class CarrierDetails(BaseModel):
    carrier_name: Optional[str] = None
    scac: Optional[str] = None
    mc_number: Optional[str] = None
    dot_number: Optional[str] = None
    driver_name: Optional[str] = None
    driver_phone: Optional[str] = None
    tractor_number: Optional[str] = None
    trailer_number: Optional[str] = None
    seal_number: Optional[str] = None


class BillingReferences(BaseModel):
    load_id: Optional[str] = None
    bol_number: Optional[str] = None
    pro_number: Optional[str] = None
    invoice_id: Optional[str] = None
    po_numbers: List[str] = Field(default_factory=list)
    customer_ref: Optional[str] = None
    carrier_ref: Optional[str] = None


class CanonicalShipment(BaseModel):
    """Canonical, multi-source reconciled shipment representation."""
    shipment_id: Optional[str] = None
    organization_id: str
    shipment_number: str
    status: str = "created"  # created, booked, dispatched, picked_up, in_transit, out_for_delivery, delivered, cancelled
    parties: ShipmentParties = Field(default_factory=ShipmentParties)
    locations: ShipmentLocations = Field(default_factory=ShipmentLocations)
    dates: ShipmentDates = Field(default_factory=ShipmentDates)
    freight_details: FreightDetails = Field(default_factory=FreightDetails)
    pricing: ShipmentPricing = Field(default_factory=ShipmentPricing)
    carrier: CarrierDetails = Field(default_factory=CarrierDetails)
    billing_references: BillingReferences = Field(default_factory=BillingReferences)
    metadata_payload: Dict[str, Any] = Field(default_factory=dict)
    last_reconciled_at: Optional[datetime] = None

    def get_field_value(self, dot_path: str) -> Any:
        """Retrieve value by nested dot-path (e.g. 'freight_details.total_weight_lbs')."""
        parts = dot_path.split(".")
        curr: Any = self
        for part in parts:
            if curr is None:
                return None
            if isinstance(curr, dict):
                curr = curr.get(part)
            elif hasattr(curr, part):
                curr = getattr(curr, part)
            else:
                return None
        return curr

    def set_field_value(self, dot_path: str, value: Any) -> None:
        """Set value by nested dot-path."""
        parts = dot_path.split(".")
        curr: Any = self
        for i, part in enumerate(parts[:-1]):
            if isinstance(curr, dict):
                curr = curr[part]
            elif hasattr(curr, part):
                curr = getattr(curr, part)
            else:
                raise AttributeError(f"Invalid path component: {part} in {dot_path}")
        leaf = parts[-1]
        if isinstance(curr, dict):
            curr[leaf] = value
        elif hasattr(curr, leaf):
            setattr(curr, leaf, value)
        else:
            raise AttributeError(f"Invalid leaf attribute: {leaf} in {dot_path}")
