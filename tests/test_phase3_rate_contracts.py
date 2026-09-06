"""Phase 3.2 Test Suite — Rate / Contract Data Model and Lane Matching."""

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.domain.contracts import FuelSchedule, FuelScheduleType
from packages.domain.models import Organization
from packages.storage.db import Base
from packages.storage.repositories.contracts import RateContractRepository


@pytest.fixture
def db_session():
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


def test_rate_contract_creation_and_fields(db_session, test_org):
    """Verify RateContractRepository creates contract with base rate, min charge, and schedules."""
    repo = RateContractRepository(db_session)

    contract = repo.create_contract(
        organization_id=test_org.id,
        carrier_name="Estes Express Lines",
        contract_number="CTR-ESTES-2026",
        customer_name="Global Steel Supply",
        lane_origin_state="IL",
        lane_dest_state="GA",
        base_rate=1650.00,
        minimum_charge=450.00,
        rate_type="flat",
        fuel_schedule={"type": "percent", "base_rate_percent": 15.0},
        accessorial_schedule={
            "DETENTION": {"rate_per_hour": 75.0, "free_hours": 2},
            "LIFTGATE": {"flat": 100.0},
            "RESIDENTIAL": {"flat": 85.0},
        },
        class_rate_rules={"min_density": 6.0, "reclass_requires_cert": True},
        is_active=True,
    )

    assert contract.id is not None
    assert contract.carrier_name == "Estes Express Lines"
    assert contract.base_rate == 1650.00
    assert contract.minimum_charge == 450.00
    assert contract.accessorial_schedule["DETENTION"]["rate_per_hour"] == 75.0
    assert contract.is_active is True


def test_lane_matching_and_specificity(db_session, test_org):
    """Verify find_matching_contract prioritizes specific origin/dest lanes over generic contracts."""
    repo = RateContractRepository(db_session)

    # Generic nationwide contract
    repo.create_contract(
        organization_id=test_org.id,
        carrier_name="Old Dominion",
        contract_number="ODFL-NATIONWIDE",
        base_rate=1800.00,
        minimum_charge=400.00,
    )

    # Specific lane contract (IL -> GA)
    repo.create_contract(
        organization_id=test_org.id,
        carrier_name="Old Dominion",
        contract_number="ODFL-IL-GA",
        lane_origin_state="IL",
        lane_dest_state="GA",
        base_rate=1450.00,
        minimum_charge=400.00,
    )

    # Query for specific lane IL -> GA
    matched_specific = repo.find_matching_contract(
        organization_id=test_org.id,
        carrier_name="Old Dominion",
        origin_state="IL",
        dest_state="GA",
    )
    assert matched_specific is not None
    assert matched_specific.contract_number == "ODFL-IL-GA"
    assert matched_specific.base_rate == 1450.00

    # Query for unconfigured lane TX -> CA should fallback to nationwide
    matched_generic = repo.find_matching_contract(
        organization_id=test_org.id,
        carrier_name="Old Dominion",
        origin_state="TX",
        dest_state="CA",
    )
    assert matched_generic is not None
    assert matched_generic.contract_number == "ODFL-NATIONWIDE"
    assert matched_generic.base_rate == 1800.00


def test_fuel_schedule_deterministic_calculation():
    """Verify FuelSchedule formula and table calculations in deterministic code."""
    # Flat percentage schedule
    sched_pct = FuelSchedule(
        schedule_type=FuelScheduleType.PERCENT,
        base_surcharge_percent=15.0,
    )
    assert sched_pct.calculate_surcharge_percent(3.50) == 15.0

    # Index formula schedule: base price $3.00, 15% base, +0.5% per $0.05 diesel increase
    sched_formula = FuelSchedule(
        schedule_type=FuelScheduleType.INDEX_FORMULA,
        base_fuel_price=3.00,
        base_surcharge_percent=15.0,
        price_increment=0.05,
        percent_per_increment=0.5,
    )
    # Diesel at $3.25 -> 5 increments of $0.05 -> 15.0 + 2.5 = 17.5%
    surcharge = sched_formula.calculate_surcharge_percent(3.25)
    assert surcharge == 17.5

    # Bracket table schedule
    sched_table = FuelSchedule(
        schedule_type=FuelScheduleType.TABLE,
        bracket_table=[
            {"min_price": 3.00, "max_price": 3.25, "percent": 14.0},
            {"min_price": 3.25, "max_price": 3.50, "percent": 16.5},
            {"min_price": 3.50, "max_price": 3.75, "percent": 19.0},
        ],
    )
    assert sched_table.calculate_surcharge_percent(3.10) == 14.0
    assert sched_table.calculate_surcharge_percent(3.30) == 16.5
    assert sched_table.calculate_surcharge_percent(3.60) == 19.0
