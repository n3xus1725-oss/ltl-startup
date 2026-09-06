"""Phase 3.1 Test Suite — Invoice Intake, Extraction, Association, and Duplicate Detection."""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.documents.normalizer import DocumentNormalizer
from packages.domain.models import Organization
from packages.domain.resolver import ShipmentResolver
from packages.storage.db import Base
from packages.storage.repositories.invoices import InvoiceRepository
from packages.storage.repositories.shipments import ShipmentRepository


@pytest.fixture
def db_session():
    """In-memory SQLite session with full schema initialized."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def test_org(db_session):
    org = Organization(id=uuid.uuid4(), name="Acme Logistics", slug="acme-logistics")
    db_session.add(org)
    db_session.commit()
    db_session.refresh(org)
    return org


def test_invoice_field_extraction_and_normalization():
    """Verify DocumentNormalizer extracts invoice number, carrier, linehaul, fuel, accessorials, weights, and pallets."""
    invoice_text = """
    CARRIER FREIGHT INVOICE
    Carrier: Old Dominion Freight Line
    Invoice #: INV-ODFL-88992
    Load #: 5821
    BOL #: BOL-5821-X
    PRO #: PRO-9823411
    Linehaul Rate: $1,450.00
    Fuel Surcharge: $217.50
    Lumper Fee: $150.00
    Detention Charge: $75.00
    Total Due: $1,892.50
    Weight: 14,200 lbs
    Class: 77.5
    Pallet Count: 10
    """
    normalized = DocumentNormalizer.normalize_invoice(invoice_text)

    assert normalized.invoice_number == "INV-ODFL-88992"
    assert "Old Dominion" in (normalized.carrier_name or "")
    assert normalized.load_id == "5821"
    assert normalized.bol_number == "BOL-5821-X"
    assert normalized.carrier_reference == "PRO-9823411"
    assert normalized.linehaul_amount == 1450.00
    assert normalized.fuel_amount == 217.50
    assert normalized.accessorial_amount == 225.00  # 150 + 75
    assert normalized.total_billed_amount == 1892.50
    assert normalized.weight_lbs == 14200.0
    assert normalized.freight_class == "77.5"
    assert normalized.pallet_count == 10

    # Line items verified
    codes = [item["code"] for item in normalized.line_items]
    assert "LINEHAUL" in codes
    assert "FUEL" in codes
    assert "LUMPER" in codes
    assert "DETENTION" in codes


@pytest.mark.asyncio
async def test_invoice_shipment_association_via_resolver(db_session, test_org):
    """Verify candidate identifiers on an invoice reliably resolve to the correct existing shipment."""
    shp_repo = ShipmentRepository(db_session)
    shp = shp_repo.create(
        organization_id=test_org.id,
        shipment_number="LOAD-5821",
        load_id="5821",
        bol_number="BOL-5821-X",
        status="delivered",
    )

    resolver = ShipmentResolver(shp_repo)
    invoice_text = "Remit payment for Load 5821, BOL BOL-5821-X, Invoice INV-1002"
    res = await resolver.resolve(
        organization_id=test_org.id,
        subject="Invoice for Load 5821",
        body=invoice_text,
    )

    assert res.matched_entity is not None
    matched_id = res.matched_entity.get("id") if isinstance(res.matched_entity, dict) else getattr(res.matched_entity, "id", None)
    assert str(matched_id) == str(shp.id)
    assert res.match_type in ("deterministic_exact", "load_id", "bol_number")
    assert res.confidence == 1.0


def test_duplicate_invoice_detection(db_session, test_org):
    """Verify InvoiceRepository flags duplicate invoices for the same carrier and invoice number."""
    inv_repo = InvoiceRepository(db_session)

    # Ingest original invoice
    inv1 = inv_repo.create_invoice(
        organization_id=test_org.id,
        carrier_name="Estes Express Lines",
        invoice_number="INV-99001",
        total_billed_amount=1650.00,
        status="paid",
    )
    assert inv1.status == "paid"

    # Check duplicate detection
    dup = inv_repo.check_duplicate(test_org.id, "Estes Express Lines", "INV-99001")
    assert dup is not None
    assert dup.id == inv1.id

    # Another carrier with same number should not falsely collide if scoped
    diff_carrier_dup = inv_repo.check_duplicate(test_org.id, "Old Dominion", "INV-99001")
    assert diff_carrier_dup is None


def test_invoice_repository_crud_and_status(db_session, test_org):
    """Verify invoice creation, status transition, and listing."""
    inv_repo = InvoiceRepository(db_session)

    inv = inv_repo.create_invoice(
        organization_id=test_org.id,
        carrier_name="R+L Carriers",
        invoice_number="INV-RL-4432",
        total_billed_amount=2150.00,
        linehaul_amount=1800.00,
        fuel_amount=350.00,
        status="received",
        audit_status="pending",
    )

    assert inv.id is not None
    assert inv.invoice_number == "INV-RL-4432"
    assert inv.status == "received"

    updated = inv_repo.update_status(test_org.id, inv.id, status="audited", audit_status="clean")
    assert updated.status == "audited"
    assert updated.audit_status == "clean"

    invoices = inv_repo.list_invoices(test_org.id)
    assert len(invoices) == 1
    assert invoices[0].carrier_name == "R+L Carriers"
