"""Phase 1.9 — Evaluation Suite Tests.

Executes the 110-email benchmark through the Inbox Action Agent pipeline
and verifies compliance with Phase 1 Exit Criteria.
"""

import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from packages.evals.evaluator import InboxAgentEvaluator
from packages.storage.db import Base
from packages.storage.repositories.organizations import OrganizationRepository


@pytest.fixture(scope="function")
def eval_db():
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
def eval_org(eval_db):
    org_repo = OrganizationRepository(eval_db)
    return org_repo.create("Apex Global Logistics", "apex-eval")


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("OPENAI_API_KEY", "").startswith("sk-test") or os.getenv("ENVIRONMENT") == "test",
    reason="Full 110-email evaluation suite requires real LLM API keys and shouldn't run in basic CI."
)
async def test_full_evaluation_pipeline_110_emails(eval_db, eval_org):
    """Run full 110-email benchmark across 50 normal, 20 ambiguous,
    20 irrelevant, and 20 conflicting emails.

    Verify:
    - 100% false action rate prevention (0.0% false action rate)
    - 100% duplicate action prevention (0.0% duplicate action rate)
    - >= 95% correct shipment match rate
    - >= 95% correct action rate
    - Accurate human escalation of all ambiguous and conflicting cases
    """
    evaluator = InboxAgentEvaluator(eval_db)
    report = await evaluator.run_evaluation(eval_org.id)

    print("\n" + "=" * 60)
    print("PHASE 1.9 EVALUATION BENCHMARK REPORT:")
    print(report.summary)
    print("=" * 60 + "\n")

    # Critical Phase 1 Exit Criteria Assertions
    assert report.total_cases == 110
    assert report.normal_count == 50
    assert report.ambiguous_count == 20
    assert report.irrelevant_count == 20
    assert report.conflict_count == 20

    # Match and action accuracy
    assert report.correct_shipment_match_rate >= 0.95
    assert report.correct_action_rate >= 0.95

    # Safety Guardrails
    assert report.false_action_rate == 0.0, "False action rate must be exactly 0.0%"
    assert report.duplicate_action_rate == 0.0, "Duplicate action rate must be exactly 0.0%"

    # Escalation rate should be ~36.4% (40/110 cases: 20 ambiguous + 20 conflicting)
    assert 0.30 <= report.human_escalation_rate <= 0.45

    assert report.passed is True
