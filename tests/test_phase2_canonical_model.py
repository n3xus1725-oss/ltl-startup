"""Phase 2.1 Test Suite — Canonical Shipment Model & Field-Level Provenance."""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.domain.canonical import (
    AccessorialCharge,
    CanonicalShipment,
    CarrierDetails,
    FreightDetails,
    ShipmentParties,
    ShipmentParty,
    ShipmentPricing,
)
from packages.domain.provenance import FieldProvenanceRecord, ProvenanceLedger
from packages.storage.db import Base
from packages.storage.repositories.organizations import OrganizationRepository
from packages.storage.repositories.shipments import ShipmentRepository


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_structured_canonical_entities():
    """Verify structured Pydantic models for freight domain entities."""
    shipper = ShipmentParty(
        name="Acme Industrial",
        address_line1="100 Factory Way",
        city="Chicago",
        state="IL",
        postal_code="60601",
    )
    consignee = ShipmentParty(
        name="Target Distribution DC #42",
        address_line1="500 Logistics Blvd",
        city="Dallas",
        state="TX",
        postal_code="75201",
    )
    parties = ShipmentParties(shipper=shipper, consignee=consignee)

    pricing = ShipmentPricing(
        currency="USD",
        agreed_total=1850.0,
        linehaul=1650.0,
        fuel_surcharge=200.0,
        accessorials=[
            AccessorialCharge(code="LFT", name="Liftgate Delivery", amount=75.0, is_approved=True)
        ],
    )

    freight = FreightDetails(
        total_weight_lbs=14250.0,
        pallet_count=8,
        piece_count=120,
        commodity="Automotive Parts",
        nmfc_class="70",
    )

    carrier = CarrierDetails(
        carrier_name="Estes Express",
        scac="EXLA",
        trailer_number="TR-9821",
        seal_number="SL-4412",
    )

    canonical = CanonicalShipment(
        organization_id=str(uuid.uuid4()),
        shipment_number="SHP-2026-001",
        status="booked",
        parties=parties,
        pricing=pricing,
        freight_details=freight,
        carrier=carrier,
    )

    assert canonical.shipment_number == "SHP-2026-001"
    assert canonical.get_field_value("parties.shipper.name") == "Acme Industrial"
    assert canonical.get_field_value("pricing.agreed_total") == 1850.0
    assert canonical.get_field_value("freight_details.total_weight_lbs") == 14250.0
    assert canonical.get_field_value("carrier.trailer_number") == "TR-9821"

    # Test dot-path mutation
    canonical.set_field_value("freight_details.total_weight_lbs", 15000.0)
    assert canonical.freight_details.total_weight_lbs == 15000.0


def test_field_provenance_ledger():
    """Verify field-level provenance records and assertion history tracking."""
    ledger = ProvenanceLedger()

    # 1. First assertion from carrier email
    rec1 = FieldProvenanceRecord(
        field="freight_details.total_weight_lbs",
        value=14000.0,
        source="carrier_email",
        source_id="msg-101",
        authority=40,
        confidence=0.85,
        writer="agent_run_1",
    )
    ledger.record_assertion(rec1, is_active=True)

    assert ledger.get_active("freight_details.total_weight_lbs").value == 14000.0
    assert ledger.get_active("freight_details.total_weight_lbs").source == "carrier_email"

    # 2. Second assertion from signed BOL (higher authority)
    rec2 = FieldProvenanceRecord(
        field="freight_details.total_weight_lbs",
        value=14250.0,
        source="bol",
        source_id="doc-202",
        authority=80,
        confidence=0.98,
        writer="doc_pipeline",
    )
    ledger.record_assertion(rec2, is_active=True)

    assert ledger.get_active("freight_details.total_weight_lbs").value == 14250.0
    assert ledger.get_active("freight_details.total_weight_lbs").source == "bol"

    # 3. Third assertion from scale ticket (certified reweigh)
    rec3 = FieldProvenanceRecord(
        field="freight_details.total_weight_lbs",
        value=14320.0,
        source="scale_ticket",
        source_id="doc-303",
        authority=100,
        confidence=0.99,
        writer="doc_pipeline",
    )
    ledger.record_assertion(rec3, is_active=True)

    assert ledger.get_active("freight_details.total_weight_lbs").value == 14320.0
    assert ledger.get_active("freight_details.total_weight_lbs").authority == 100

    # Verify complete historical audit trail is preserved
    history = ledger.get_history("freight_details.total_weight_lbs")
    assert len(history) == 3
    assert [h.source for h in history] == ["carrier_email", "bol", "scale_ticket"]

    # Test serialization round-trip
    d = ledger.to_dict()
    restored = ProvenanceLedger.from_dict(d)
    assert restored.get_active("freight_details.total_weight_lbs").value == 14320.0
    assert len(restored.get_history("freight_details.total_weight_lbs")) == 3


def test_database_canonical_and_provenance_sync(db_session):
    """Verify ShipmentRepository correctly mutates canonical_data and provenance_ledger."""
    org_repo = OrganizationRepository(db_session)
    shipment_repo = ShipmentRepository(db_session)

    org = org_repo.create("Apex Logistics", "apex-canonical-test")
    shipment = shipment_repo.create(
        organization_id=org.id,
        shipment_number="SHP-CANON-01",
        status="booked",
        weight_lbs=12000.0,
        total_charges=1500.0,
    )

    canonical = shipment_repo.get_canonical(org.id, shipment.id)
    assert canonical["shipment_number"] == "SHP-CANON-01"
    assert canonical["freight_details"]["total_weight_lbs"] == 12000.0
    assert canonical["pricing"]["agreed_total"] == 1500.0

    # Update canonical data with new freight and pricing values
    canonical["freight_details"]["total_weight_lbs"] = 13800.0
    canonical["freight_details"]["pallet_count"] = 6
    canonical["pricing"]["agreed_total"] = 1750.0
    canonical["carrier"] = {"carrier_name": "R+L Carriers", "scac": "RLCA"}

    ledger = ProvenanceLedger()
    ledger.record_assertion(
        FieldProvenanceRecord(
            field="freight_details.total_weight_lbs",
            value=13800.0,
            source="bol",
            source_id="doc-bol-1",
            authority=80,
        ),
        is_active=True,
    )

    updated_shipment = shipment_repo.update_canonical(
        organization_id=org.id,
        shipment_id=shipment.id,
        canonical_data=canonical,
        provenance_ledger=ledger.to_dict(),
        sync_flat_columns=True,
    )

    # Verify flat database columns were synchronized
    assert updated_shipment.weight_lbs == 13800.0
    assert updated_shipment.pallet_count == 6
    assert float(updated_shipment.total_charges) == 1750.0
    assert updated_shipment.carrier_name == "R+L Carriers"
    assert updated_shipment.carrier_reference == "RLCA"
    assert updated_shipment.canonical_data["freight_details"]["total_weight_lbs"] == 13800.0
    assert updated_shipment.provenance_ledger["active_assertions"]["freight_details.total_weight_lbs"]["value"] == 13800.0
