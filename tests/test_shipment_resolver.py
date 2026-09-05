"""Comprehensive tests for Phase 1.5 Shipment Resolver."""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.domain.resolver import ShipmentResolver
from packages.llm.gateway import LLMGateway, LLMResponse
from packages.storage.db import Base
from packages.storage.repositories.organizations import OrganizationRepository
from packages.storage.repositories.shipments import ShipmentRepository


@pytest.fixture(scope="function")
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def test_org(test_db):
    org_repo = OrganizationRepository(test_db)
    return org_repo.create("Apex Global Logistics", "apex-global")


@pytest.fixture(scope="function")
def other_org(test_db):
    org_repo = OrganizationRepository(test_db)
    return org_repo.create("Rival Freightways", "rival-freight")


def test_candidate_identifier_extraction():
    """Verify regex extraction of all 5 freight identifier types."""
    repo = None
    resolver = ShipmentResolver(shipment_repo=repo)

    subject = "Delivery status for Load 5821 and PRO 9823411"
    body = (
        "Hello team,\n"
        "Please check BOL-847293 and Invoice #8892.\n"
        "Also referenced under Shipment #12345 or LOAD-1234."
    )

    candidates = resolver.extract_candidate_identifiers(subject, body)
    types_found = {c.id_type: c.raw_value for c in candidates}

    assert "load_id" in types_found
    assert "5821" in types_found.values() or "LOAD-1234" in types_found.values()
    assert "pro_number" in types_found
    assert "9823411" in types_found.values()
    assert "bol_number" in types_found
    assert "847293" in types_found.values() or "BOL-847293" in types_found.values()
    assert "invoice_number" in types_found
    assert "8892" in types_found.values()
    assert "shipment_number" in types_found
    assert "12345" in types_found.values()


@pytest.mark.asyncio
async def test_deterministic_exact_match_by_load_id(test_db, test_org):
    """Test deterministic resolution by load ID with 1.0 confidence."""
    shipment_repo = ShipmentRepository(test_db)
    shipment = shipment_repo.create(
        organization_id=test_org.id,
        shipment_number="SHP-001",
        load_id="5821",
        carrier_name="Swift Transport",
    )

    resolver = ShipmentResolver(shipment_repo=shipment_repo)
    result = await resolver.resolve(
        organization_id=test_org.id,
        subject="Status update for Load 5821",
        body="Driver is departing origin shortly.",
    )

    assert result.confidence == 1.0
    assert result.match_type == "deterministic_exact"
    assert result.is_ambiguous is False
    assert result.matched_entity is not None
    assert result.matched_entity["id"] == str(shipment.id)
    assert result.matched_entity["shipment_number"] == "SHP-001"


@pytest.mark.asyncio
async def test_deterministic_exact_match_by_pro_and_bol(test_db, test_org):
    """Test deterministic resolution by PRO and BOL numbers."""
    shipment_repo = ShipmentRepository(test_db)
    s1 = shipment_repo.create(
        organization_id=test_org.id,
        shipment_number="SHP-002",
        carrier_reference="9823411",  # PRO number
        bol_number="BOL-847293",
    )

    resolver = ShipmentResolver(shipment_repo=shipment_repo)

    # Resolve via PRO
    res_pro = await resolver.resolve(
        organization_id=test_org.id,
        subject="Tracking PRO 9823411",
        body="Please confirm ETA.",
    )
    assert res_pro.confidence == 1.0
    assert res_pro.matched_entity["id"] == str(s1.id)

    # Resolve via BOL
    res_bol = await resolver.resolve(
        organization_id=test_org.id,
        subject="Signed paperwork",
        body="Attached bill of lading: BOL-847293",
    )
    assert res_bol.confidence == 1.0
    assert res_bol.matched_entity["id"] == str(s1.id)


@pytest.mark.asyncio
async def test_deterministic_ambiguity_never_arbitrarily_chosen(test_db, test_org):
    """Rule: Never choose arbitrarily when two shipments are plausible; mark as ambiguous."""
    shipment_repo = ShipmentRepository(test_db)
    s1 = shipment_repo.create(
        organization_id=test_org.id,
        shipment_number="SHP-101",
        load_id="LOAD-101",
    )
    s2 = shipment_repo.create(
        organization_id=test_org.id,
        shipment_number="SHP-102",
        load_id="LOAD-102",
    )

    resolver = ShipmentResolver(shipment_repo=shipment_repo)

    # Email mentions BOTH load IDs in a single message
    res = await resolver.resolve(
        organization_id=test_org.id,
        subject="Cross-dock update for LOAD-101 and LOAD-102",
        body="Both loads are being handled at the warehouse.",
    )

    assert res.is_ambiguous is True
    assert res.matched_entity is None
    assert res.match_type == "deterministic_ambiguous"
    assert res.confidence == 0.5
    assert str(s1.id) in res.evidence["plausible_shipment_ids"]
    assert str(s2.id) in res.evidence["plausible_shipment_ids"]


@pytest.mark.asyncio
async def test_no_match_and_tenant_isolation(test_db, test_org, other_org):
    """Ensure shipments in other organizations are never resolved (tenant isolation)."""
    shipment_repo = ShipmentRepository(test_db)
    # Shipment belongs to other_org
    shipment_repo.create(
        organization_id=other_org.id,
        shipment_number="RIVAL-999",
        load_id="5821",
    )

    resolver = ShipmentResolver(shipment_repo=shipment_repo)

    # Query from test_org
    res = await resolver.resolve(
        organization_id=test_org.id,
        subject="Status update for Load 5821",
        body="Any news?",
        enable_llm_fallback=False,
    )

    assert res.confidence == 0.0
    assert res.match_type == "none"
    assert res.matched_entity is None
    assert res.is_ambiguous is False


@pytest.mark.asyncio
async def test_llm_fallback_only_when_deterministic_fails(test_db, test_org):
    """Verify LLM fallback is utilized when deterministic matches are missing,
    and NOT called when deterministic match succeeds.
    """
    shipment_repo = ShipmentRepository(test_db)
    target_shipment = shipment_repo.create(
        organization_id=test_org.id,
        shipment_number="SHP-777",
        carrier_name="Apex Dedicated",
        origin_address={"city": "Dallas", "state": "TX"},
        destination_address={"city": "Atlanta", "state": "GA"},
    )

    class MockGateway(LLMGateway):
        def __init__(self):
            self.call_count = 0

        async def complete(self, messages, **kwargs):
            self.call_count += 1
            # Mock LLM successfully resolves from natural language context
            return LLMResponse(
                content=json.dumps({
                    "matched_shipment_id": str(target_shipment.id),
                    "is_ambiguous": False,
                    "confidence": 0.95,
                    "reasoning": "Matched Dallas to Atlanta dedicated freight route",
                }),
                model="mock-gpt-4o",
                parsed_json={
                    "matched_shipment_id": str(target_shipment.id),
                    "is_ambiguous": False,
                    "confidence": 0.95,
                    "reasoning": "Matched Dallas to Atlanta dedicated freight route",
                },
            )

    mock_llm = MockGateway()
    resolver = ShipmentResolver(shipment_repo=shipment_repo, llm_gateway=mock_llm)

    # 1. Natural language email without explicit identifiers -> invokes LLM fallback
    res = await resolver.resolve(
        organization_id=test_org.id,
        subject="Update for Dallas to Atlanta dedicated run",
        body="Driver is en route to Atlanta.",
        enable_llm_fallback=True,
    )
    assert mock_llm.call_count == 1
    assert res.match_type == "llm_fallback"
    assert res.confidence == 0.95
    assert res.matched_entity["id"] == str(target_shipment.id)

    # 2. Email with explicit deterministic identifier -> LLM fallback MUST NOT BE CALLED
    shipment_repo.create(
        organization_id=test_org.id,
        shipment_number="SHP-888",
        load_id="LOAD-888",
    )
    res_det = await resolver.resolve(
        organization_id=test_org.id,
        subject="Status on LOAD-888",
        body="Departed yard.",
        enable_llm_fallback=True,
    )
    # Call count should still be 1 (deterministic was used, LLM skipped!)
    assert mock_llm.call_count == 1
    assert res_det.match_type == "deterministic_exact"
    assert res_det.confidence == 1.0
