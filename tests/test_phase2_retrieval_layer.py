"""Phase 2.5 Test Suite — Multi-Tenant Retrieval Layer."""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.domain.canonical import (
    CanonicalShipment,
    FreightDetails,
    ShipmentParties,
    ShipmentParty,
)
from packages.domain.provenance import FieldProvenanceRecord, ProvenanceLedger
from packages.retrieval.chunker import DocumentChunker
from packages.retrieval.engine import ShipmentRetrievalEngine
from packages.retrieval.keyword import KeywordSearchEngine
from packages.retrieval.schemas import ContextChunk
from packages.retrieval.vector import VectorSearchEngine
from packages.storage.db import Base
from packages.storage.repositories.documents import DocumentRepository
from packages.storage.repositories.messages import MessageRepository
from packages.storage.repositories.organizations import OrganizationRepository
from packages.storage.repositories.shipments import ShipmentRepository


@pytest.fixture
def db_session():
    """Create a fresh in-memory SQLite database for test isolation."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def test_document_chunker():
    """Verify document chunking across text passages and structured tables."""
    # 1. Text chunking
    long_text = "\n".join([f"Line {i}: Details of freight shipment and BOL requirements." for i in range(40)])
    chunks = DocumentChunker.chunk_text(
        text=long_text,
        source_id="doc-123",
        source_type="bol",
        chunk_size=200,
        chunk_overlap=50,
    )
    assert len(chunks) > 1
    assert all(c.source_id == "doc-123" for c in chunks)
    assert all(c.source_type == "bol" for c in chunks)
    assert "Line 0" in chunks[0].content
    assert "chunk_hash" in chunks[0].metadata

    # 2. Table chunking
    table = [
        ["Item", "Weight", "Pieces", "Class"],
        ["Pallet 1", "1,200", "50", "70"],
        ["Pallet 2", "1,450", "60", "70"],
        ["Pallet 3", "2,100", "80", "85"],
        ["Pallet 4", "1,800", "70", "70"],
    ]
    tbl_chunks = DocumentChunker.chunk_table(
        table_rows=table,
        source_id="doc-table-1",
        rows_per_chunk=2,
    )
    assert len(tbl_chunks) == 2
    assert "Item | Weight | Pieces | Class" in tbl_chunks[0].content
    assert "Item | Weight | Pieces | Class" in tbl_chunks[1].content
    assert "Pallet 1" in tbl_chunks[0].content
    assert "Pallet 3" in tbl_chunks[1].content


def test_keyword_search_and_tenant_isolation(db_session):
    """Verify keyword search finds relevant entities and enforces organization boundaries."""
    org_repo = OrganizationRepository(db_session)
    shp_repo = ShipmentRepository(db_session)
    doc_repo = DocumentRepository(db_session)
    msg_repo = MessageRepository(db_session)

    org_a = org_repo.create("Apex Logistics", "apex-search-test")
    org_b = org_repo.create("Beta Global", "beta-search-test")

    # Create shipment for Org A
    shp_a = shp_repo.create(
        organization_id=org_a.id,
        shipment_number="LOAD-7788",
        status="booked",
        carrier_name="Old Dominion Freight Line",
        bol_number="BOL-778899",
    )

    # Create document for Org A
    doc_repo.create(
        organization_id=org_a.id,
        file_name="rate_con_7788.pdf",
        storage_path="/docs/rate_con_7788.pdf",
        document_type="RATE_CONFIRMATION",
        extracted_data={"raw_text": "Rate Confirmation for Load 7788 agreed amount $2,450 to Old Dominion"},
        checksum_sha256="dummy-hash-7788",
        shipment_id=shp_a.id,
    )

    # Create message for Org A
    msg_repo.create(
        organization_id=org_a.id,
        external_message_id="msg-7788",
        thread_id="th-7788",
        direction="inbound",
        sender="dispatch@odfl.com",
        recipients=["ops@apex.com"],
        subject="Rate Con Confirmation LOAD-7788",
        body_text="Here is the confirmed rate for LOAD-7788 scheduled for tomorrow morning.",
        shipment_id=shp_a.id,
    )

    engine = KeywordSearchEngine(db_session)

    # 1. Org A finds its shipment
    res_a = engine.search_shipments(org_a.id, "7788")
    assert len(res_a) == 1
    assert res_a[0].entity_id == str(shp_a.id)
    assert res_a[0].relevance_score >= 0.85

    # 2. Org B cannot see Org A's shipment (Strict Tenant Isolation)
    res_b = engine.search_shipments(org_b.id, "7788")
    assert len(res_b) == 0

    # 3. Document text search
    doc_res = engine.search_documents(org_a.id, "Old Dominion")
    assert len(doc_res) == 1
    assert "rate_con_7788" in doc_res[0].title
    assert "2,450" in doc_res[0].snippet

    # 4. Message search
    msg_res = engine.search_messages(org_a.id, "confirmed rate")
    assert len(msg_res) == 1
    assert msg_res[0].metadata["sender"] == "dispatch@odfl.com"


@pytest.mark.asyncio
async def test_vector_search_engine():
    """Verify semantic chunk indexing and cosine similarity ranking."""
    v_engine = VectorSearchEngine()
    org_id = uuid.uuid4()

    chunks = [
        ContextChunk(
            source_type="bol",
            source_id="bol-1",
            content="Heavy machinery shipment requires flatbed trailer with tie-down straps.",
        ),
        ContextChunk(
            source_type="invoice",
            source_id="inv-1",
            content="Detention charges incurred at receiver dock after 2 hours free time.",
        ),
        ContextChunk(
            source_type="pod",
            source_id="pod-1",
            content="Received 10 pallets undamaged in good condition signed by warehouse manager.",
        ),
    ]

    indexed = await v_engine.index_chunks(org_id, chunks)
    assert indexed == 3

    # Semantic search query
    results = await v_engine.search(org_id, "dock waiting detention fee", top_k=2)
    assert len(results) > 0
    # The detention chunk should rank highest
    assert results[0].metadata["source_type"] == "invoice"
    assert "Detention charges" in results[0].snippet


def test_shipment_retrieval_engine_canonical_context(db_session):
    """Verify unified retrieval engine compiles canonical context, provenance, and active conflicts."""
    org_repo = OrganizationRepository(db_session)
    shp_repo = ShipmentRepository(db_session)

    org = org_repo.create("Apex Logistics", "apex-engine-test")
    shp = shp_repo.create(
        organization_id=org.id,
        shipment_number="LOAD-5522",
        status="booked",
    )

    # Initialize canonical data and provenance
    canonical = CanonicalShipment(
        organization_id=str(org.id),
        shipment_number="LOAD-5522",
        status="booked",
        parties=ShipmentParties(
            shipper=ShipmentParty(name="Acme Industrial", city="Chicago", state="IL"),
            carrier=ShipmentParty(name="Estes Express Lines"),
        ),
        freight_details=FreightDetails(total_weight_lbs=12400.0, pallet_count=8),
    )
    ledger = ProvenanceLedger()
    rec = FieldProvenanceRecord(
        field="freight_details.total_weight_lbs",
        value=12400.0,
        source="bol",
        source_id="BOL-9911",
        authority=80,
        confidence=0.95,
        evidence={"raw_snippet": "Total Weight: 12,400 lbs"},
    )
    ledger.record_assertion(rec)

    shp_repo.update_canonical(
        organization_id=org.id,
        shipment_id=shp.id,
        canonical_data=canonical.model_dump(),
        provenance_ledger=ledger.to_dict(),
    )

    retrieval_engine = ShipmentRetrievalEngine(db_session)
    context_res = retrieval_engine.get_canonical_context(org.id, shp.id)

    assert context_res is not None
    assert context_res.canonical_data is not None
    assert context_res.canonical_data["freight_details"]["total_weight_lbs"] == 12400.0
    assert context_res.canonical_data["parties"]["carrier"]["name"] == "Estes Express Lines"

    # Provenance trail verified
    assert "freight_details.total_weight_lbs" in context_res.provenance_trail
    assert context_res.provenance_trail["freight_details.total_weight_lbs"]["authority_score"] == 80
    assert context_res.provenance_trail["freight_details.total_weight_lbs"]["source_type"] == "bol"

    # Field specific trail
    field_trail = retrieval_engine.get_field_provenance_trail(org.id, shp.id, "freight_details.total_weight_lbs")
    assert field_trail is not None
    assert field_trail["current_value"] == 12400.0
    assert len(field_trail["history"]) == 1
