"""Test Tool Failure Recovery for Billing Audit Agent."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.agent.audit.service import run_audit_agent
from packages.storage.db import Base
from packages.storage.repositories.organizations import OrganizationRepository


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


@pytest.mark.asyncio
async def test_empty_invoice_input_graceful_failure(test_db, test_org):
    """Test that missing/empty invoice inputs produce a structured error without crashing."""
    output = await run_audit_agent(
        organization_id=str(test_org.id),
        db=test_db,
        invoice_id=None,
        invoice_text=None,
        trigger_event_id="evt-fail-001",
    )

    assert output.terminal_outcome == "failed"
    assert len(output.errors) > 0
    assert "Unable to ingest invoice payload" in output.errors[0]
