"""Phase 2.2 Test Suite — Document Processing Pipeline (PDF/CSV/Image/Normalizer)."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.documents.classifier import DocumentClassifier
from packages.documents.extractors.pdf import PDFExtractor
from packages.documents.extractors.spreadsheet import SpreadsheetExtractor
from packages.documents.normalizer import DocumentNormalizer
from packages.documents.pipeline import DocumentProcessingPipeline
from packages.storage.db import Base
from packages.storage.repositories.documents import DocumentRepository
from packages.storage.repositories.organizations import OrganizationRepository


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


def test_document_classification():
    """Verify classification across freight document types."""
    assert DocumentClassifier.classify("Signed_BOL_5821.pdf", "Uniform Straight Bill of Lading Carrier: Estes") == "BOL"
    assert DocumentClassifier.classify("Proof_Of_Delivery.pdf", "Delivery Receipt Received by Signature: John Doe") == "POD"
    assert DocumentClassifier.classify("Rate_Confirmation_9942.pdf", "Linehaul Rate: $1,450.00 Total Agreed: $1,650.00") == "RATE_CONFIRMATION"
    assert DocumentClassifier.classify("Carrier_Invoice_INV-889.pdf", "Freight Invoice Total Due: $1,850.00 Remit To") == "INVOICE"
    assert DocumentClassifier.classify("CAT_Scale_Ticket.pdf", "Certified Scale Ticket Gross Weight: 42,000 lbs Tare Weight: 28,000 lbs") == "SCALE_TICKET"
    assert DocumentClassifier.classify("random_photo.jpg", "random content") == "UNKNOWN"


def test_pdf_extractor_fallback():
    """Verify PDF extractor parses plain/simulated text."""
    content = b"%PDF-1.4\nBill of Lading BOL-99482\nTotal Weight: 14,500 lbs\nPallet Count: 8"
    text, tables, metadata = PDFExtractor.extract(content)
    assert "BOL-99482" in text
    assert "14,500" in text


def test_spreadsheet_extractor_csv():
    """Verify CSV spreadsheet extractor parses rows and text."""
    csv_bytes = b"Load_ID,Origin,Destination,Weight,Rate\n5821,Atlanta GA,Dallas TX,14200,1850.00\n"
    text, rows, meta = SpreadsheetExtractor.extract_csv(csv_bytes)
    assert meta["row_count"] == 2
    assert len(rows) == 2
    assert rows[1][0] == "5821"
    assert rows[1][3] == "14200"


def test_document_normalizers():
    """Verify DocumentNormalizer extracts typed domain attributes."""
    # 1. BOL Normalizer
    bol_text = """
    UNIFORM STRAIGHT BILL OF LADING
    BOL #: BOL-847293
    Load #: 5821
    Carrier Name: Estes Express Lines
    Trailer #: TR-4921
    Seal #: SL-9901
    Total Weight: 16,450 lbs
    Pallet Count: 10
    Piece Count: 150
    """
    bol = DocumentNormalizer.normalize_bol(bol_text)
    assert bol.bol_number == "BOL-847293"
    assert bol.load_id == "5821"
    assert bol.carrier_name == "Estes Express Lines"
    assert bol.total_weight_lbs == 16450.0
    assert bol.pallet_count == 10
    assert bol.piece_count == 150
    assert bol.trailer_number == "TR-4921"
    assert bol.seal_number == "SL-9901"

    # 2. POD Normalizer
    pod_text = """
    PROOF OF DELIVERY RECEIPT
    BOL #: BOL-847293
    PRO #: PRO-9823411
    Delivered Date: 2026-09-08
    Delivery Time: 14:30
    Received By: Jane Smith
    Damage: None
    """
    pod = DocumentNormalizer.normalize_pod(pod_text)
    assert pod.bol_number == "BOL-847293"
    assert pod.pro_number == "PRO-9823411"
    assert pod.delivery_date == "2026-09-08"
    assert pod.received_by_signature == "Jane Smith"
    assert pod.damage_noted is False

    # 3. Rate Confirmation Normalizer
    rc_text = """
    BROKER CARRIER RATE CONFIRMATION
    Load ID: 5821
    Carrier: Estes Express
    Linehaul Rate: $1,650.00
    Fuel Surcharge: $200.00
    Total Agreed Amount: $1,850.00
    """
    rc = DocumentNormalizer.normalize_rate_con(rc_text)
    assert rc.load_id == "5821"
    assert rc.linehaul_rate == 1650.0
    assert rc.fuel_surcharge == 200.0
    assert rc.total_agreed_rate == 1850.0

    # 4. Scale Ticket Normalizer
    st_text = """
    CERTIFIED CAT SCALE TICKET
    Ticket #: ST-99823
    Scale Name: Pilot Travel Center #412
    Gross Weight: 45,200 lbs
    Tare Weight: 30,700 lbs
    Net Weight: 14,500 lbs
    """
    st = DocumentNormalizer.normalize_scale_ticket(st_text)
    assert st.ticket_number == "ST-99823"
    assert st.scale_name == "Pilot Travel Center #412"
    assert st.gross_weight_lbs == 45200.0
    assert st.tare_weight_lbs == 30700.0
    assert st.net_weight_lbs == 14500.0


def test_pipeline_end_to_end(db_session):
    """Verify complete DocumentProcessingPipeline execution with database persistence."""
    org_repo = OrganizationRepository(db_session)
    doc_repo = DocumentRepository(db_session)

    org = org_repo.create("Apex Logistics", "apex-pipeline-test")
    doc_content = b"""
    UNIFORM STRAIGHT BILL OF LADING
    BOL #: BOL-554433
    Load #: 5821
    Total Weight: 18,200 lbs
    Pallet Count: 12
    Trailer #: TR-1122
    """
    doc = doc_repo.create(
        organization_id=org.id,
        file_name="Signed_BOL_5821.pdf",
        storage_path="/attachments/bol-5821.pdf",
        document_type="UNKNOWN",
        file_size_bytes=len(doc_content),
        mime_type="application/pdf",
    )

    pipeline = DocumentProcessingPipeline(db=db_session)
    res = pipeline.process_content(
        filename="Signed_BOL_5821.pdf",
        content_bytes=doc_content,
        mime_type="application/pdf",
        document_id=str(doc.id),
        organization_id=org.id,
    )

    assert res.classified_type == "BOL"
    assert res.extracted_fields["bol_number"] == "BOL-554433"
    assert res.extracted_fields["total_weight_lbs"] == 18200.0
    assert res.extracted_fields["pallet_count"] == 12

    # Check generated provenance assertions
    assertions = res.provenance_assertions
    assert len(assertions) >= 3
    weight_att = next(a for a in assertions if a["field"] == "freight_details.total_weight_lbs")
    assert weight_att["value"] == 18200.0
    assert weight_att["authority"] == 80

    # Verify DB was updated
    db_session.refresh(doc)
    assert doc.document_type == "BOL"
    assert doc.extracted_data["fields"]["bol_number"] == "BOL-554433"
