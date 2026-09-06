"""Phase 2.2 — Normalized Freight Document Schemas."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class NormalizedDocumentHeader(BaseModel):
    document_id: Optional[str] = None
    file_name: str
    document_type: str  # BOL, POD, RATE_CONFIRMATION, INVOICE, SCALE_TICKET, UNKNOWN
    mime_type: Optional[str] = None
    checksum_sha256: Optional[str] = None
    confidence: float = 1.0


class NormalizedBOL(BaseModel):
    bol_number: Optional[str] = None
    load_id: Optional[str] = None
    shipper_name: Optional[str] = None
    shipper_address: Optional[str] = None
    consignee_name: Optional[str] = None
    consignee_address: Optional[str] = None
    carrier_name: Optional[str] = None
    trailer_number: Optional[str] = None
    seal_number: Optional[str] = None
    pickup_date: Optional[str] = None
    total_weight_lbs: Optional[float] = None
    pallet_count: Optional[int] = None
    piece_count: Optional[int] = None
    commodity: Optional[str] = None
    nmfc_class: Optional[str] = None
    special_instructions: Optional[str] = None
    raw_text: Optional[str] = None


class NormalizedPOD(BaseModel):
    bol_number: Optional[str] = None
    pro_number: Optional[str] = None
    delivery_date: Optional[str] = None
    delivery_time: Optional[str] = None
    received_by_signature: Optional[str] = None
    consignee_name: Optional[str] = None
    piece_count_received: Optional[int] = None
    shortage_noted: bool = False
    damage_noted: bool = False
    notes: Optional[str] = None
    raw_text: Optional[str] = None


class NormalizedRateCon(BaseModel):
    load_id: Optional[str] = None
    carrier_name: Optional[str] = None
    origin_city_state: Optional[str] = None
    destination_city_state: Optional[str] = None
    pickup_date: Optional[str] = None
    delivery_date: Optional[str] = None
    linehaul_rate: Optional[float] = None
    fuel_surcharge: Optional[float] = None
    accessorial_total: Optional[float] = None
    total_agreed_rate: Optional[float] = None
    currency: str = "USD"
    equipment_type: Optional[str] = None
    raw_text: Optional[str] = None


class NormalizedInvoice(BaseModel):
    invoice_number: Optional[str] = None
    load_id: Optional[str] = None
    bol_number: Optional[str] = None
    carrier_reference: Optional[str] = None
    invoice_date: Optional[str] = None
    total_billed_amount: Optional[float] = None
    linehaul_amount: Optional[float] = None
    fuel_amount: Optional[float] = None
    accessorial_amount: Optional[float] = None
    currency: str = "USD"
    due_date: Optional[str] = None
    raw_text: Optional[str] = None


class NormalizedScaleTicket(BaseModel):
    ticket_number: Optional[str] = None
    scale_name: Optional[str] = None
    gross_weight_lbs: Optional[float] = None
    tare_weight_lbs: Optional[float] = None
    net_weight_lbs: Optional[float] = None
    weigh_date: Optional[str] = None
    vehicle_id: Optional[str] = None
    raw_text: Optional[str] = None


class ProcessedDocumentResult(BaseModel):
    header: NormalizedDocumentHeader
    classified_type: str
    extracted_fields: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0
    provenance_assertions: List[Dict[str, Any]] = Field(default_factory=list)
    raw_text_snippet: Optional[str] = None
