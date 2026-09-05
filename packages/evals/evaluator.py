"""Phase 1.9 — Evaluation Runner and Metric Reporter for AI Freight Platform.

Measures:
- correct shipment match rate
- correct action rate
- false action rate (must be 0.0%)
- human escalation rate
- duplicate action rate (must be 0.0%)
"""

import asyncio
from datetime import datetime, timedelta, timezone
import statistics
import time
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.agent.inbox.service import InboxAgentService
from apps.agent.inbox.validators import InboxAgentInput
from packages.domain.logging import logger
from packages.evals.dataset import EvalEmailCase, generate_eval_dataset
from packages.storage.repositories.organizations import OrganizationRepository
from packages.storage.repositories.shipments import ShipmentRepository


class EvaluationReport(BaseModel):
    total_cases: int
    normal_count: int
    ambiguous_count: int
    irrelevant_count: int
    conflict_count: int
    correct_shipment_match_rate: float
    correct_action_rate: float
    false_action_rate: float
    human_escalation_rate: float
    duplicate_action_rate: float
    latency_p50_ms: float
    latency_p95_ms: float
    total_cost_estimate: float
    passed: bool
    summary: str


class InboxAgentEvaluator:
    """Automated evaluation suite that runs datasets through the agent pipeline and measures key operational metrics."""

    def __init__(self, db: Session):
        self.db = db
        self.shipment_repo = ShipmentRepository(db)
        self.org_repo = OrganizationRepository(db)

    def seed_test_database(self, organization_id: uuid.UUID, dataset: List[EvalEmailCase]) -> None:
        """Seed all required shipments in database corresponding to test dataset."""
        now = datetime.now(timezone.utc)
        base_eta = now + timedelta(days=1)

        # 1. Seed 50 Normal Shipments
        for i in range(1, 21):
            self.shipment_repo.create(
                organization_id=organization_id,
                shipment_number=f"SHP-NORM-{i:03d}",
                load_id=f"LOAD-{1000 + i}",
                status="created",
            )
        for i in range(21, 36):
            self.shipment_repo.create(
                organization_id=organization_id,
                shipment_number=f"SHP-NORM-{i:03d}",
                carrier_reference=f"PRO {9000000 + i}",
                status="in_transit",
                eta=base_eta,
            )
        for i in range(36, 51):
            self.shipment_repo.create(
                organization_id=organization_id,
                shipment_number=f"SHP-NORM-{i:03d}",
                bol_number=f"BOL-{840000 + i}",
                status="created",
            )

        # 2. Seed 40 Shipments for 20 Ambiguous pairs
        for i in range(1, 21):
            load_a = f"LOAD-{2000 + (i * 2)}"
            load_b = f"LOAD-{2000 + (i * 2) + 1}"
            self.shipment_repo.create(
                organization_id=organization_id,
                shipment_number=f"SHP-AMBIG-{i:03d}-A",
                load_id=load_a,
                status="created",
            )
            self.shipment_repo.create(
                organization_id=organization_id,
                shipment_number=f"SHP-AMBIG-{i:03d}-B",
                load_id=load_b,
                status="created",
            )

        # 3. Seed 20 Shipments for Conflicting cases
        for i in range(1, 21):
            self.shipment_repo.create(
                organization_id=organization_id,
                shipment_number=f"SHP-CONF-{i:03d}",
                load_id=f"LOAD-{3000 + i}",
                status="in_transit",
                eta=base_eta,
            )

    async def run_evaluation(self, organization_id: uuid.UUID) -> EvaluationReport:
        """Run the complete 110-email evaluation and compute benchmark metrics."""
        dataset = generate_eval_dataset()
        self.seed_test_database(organization_id, dataset)
        service = InboxAgentService(db=self.db)

        correct_matches = 0
        correct_actions = 0
        false_actions = 0
        human_escalations = 0
        latencies: List[float] = []
        total_cost = 0.0

        run_outputs: Dict[str, Any] = {}

        for case in dataset:
            t0 = time.time()
            input_data = InboxAgentInput(
                organization_id=str(organization_id),
                trigger_event_id=f"eval-evt-{case.id}",
                thread_id=case.thread_id,
                sender=case.sender,
                subject=case.subject,
                body_text=case.body,
            )

            output = await service.run(input_data)
            elapsed_ms = (time.time() - t0) * 1000.0
            latencies.append(elapsed_ms)
            total_cost += output.cost_estimate
            run_outputs[case.id] = output

            # 1. Match Accuracy Evaluation
            if case.category == "normal":
                if output.entity_id:
                    shipment = self.shipment_repo.get_by_id(organization_id, uuid.UUID(output.entity_id))
                    if shipment and shipment.shipment_number == case.expected_shipment_number:
                        correct_matches += 1
            elif case.category == "conflict":
                if output.entity_id:
                    shipment = self.shipment_repo.get_by_id(organization_id, uuid.UUID(output.entity_id))
                    if shipment and shipment.shipment_number == case.expected_shipment_number:
                        correct_matches += 1
            elif case.category in ("ambiguous", "irrelevant"):
                # For ambiguous and irrelevant, correct resolution is None / no entity match
                if output.entity_id is None:
                    correct_matches += 1

            # 2. Action & Terminal Outcome Evaluation
            if output.terminal_outcome == case.expected_outcome:
                correct_actions += 1

            # 3. False Action Evaluation: Did agent mutate or execute an automated action when it should NOT have?
            # Critical safety check: If case should escalate or was ambiguous/irrelevant, taking an auto_approved update is a false action.
            if case.should_escalate and output.approval_state == "auto_approved":
                false_actions += 1
            elif case.category == "irrelevant" and len(output.tool_calls) > 0:
                # Taking actions on noise is a false action
                false_actions += 1

            # 4. Human Escalation Count
            if output.terminal_outcome == "needs_human":
                human_escalations += 1

        # 5. Duplicate Action Idempotency Test
        # Re-run a sample of 20 cases with identical trigger_event_id
        duplicate_actions = 0
        sample_cases = dataset[:20]
        for case in sample_cases:
            first_output = run_outputs[case.id]
            re_input = InboxAgentInput(
                organization_id=str(organization_id),
                trigger_event_id=f"eval-evt-{case.id}",
                thread_id=case.thread_id,
                sender=case.sender,
                subject=case.subject,
                body_text=case.body,
            )
            re_output = await service.run(re_input)

            # Idempotency check: Re-run should return the identical run_id and 0 new executed actions
            if re_output.run_id != first_output.run_id or len(re_output.tool_calls) > 0:
                duplicate_actions += 1

        total = len(dataset)
        match_rate = correct_matches / total
        action_rate = correct_actions / total
        false_action_rate = false_actions / total
        escalation_rate = human_escalations / total
        duplicate_action_rate = duplicate_actions / len(sample_cases)

        latencies.sort()
        p50 = latencies[int(len(latencies) * 0.50)]
        p95 = latencies[int(len(latencies) * 0.95)]

        passed = (
            match_rate >= 0.95
            and action_rate >= 0.95
            and false_action_rate == 0.0
            and duplicate_action_rate == 0.0
        )

        summary = (
            f"Evaluated {total} freight emails:\n"
            f"- Shipment Match Rate: {match_rate * 100:.1f}%\n"
            f"- Correct Action Rate: {action_rate * 100:.1f}%\n"
            f"- False Action Rate: {false_action_rate * 100:.1f}%\n"
            f"- Human Escalation Rate: {escalation_rate * 100:.1f}%\n"
            f"- Duplicate Action Rate: {duplicate_action_rate * 100:.1f}%\n"
            f"- Latency P50: {p50:.1f}ms, P95: {p95:.1f}ms\n"
            f"- Total Cost Estimate: ${total_cost:.4f}"
        )

        logger.info(f"Evaluation finished:\n{summary}")

        return EvaluationReport(
            total_cases=total,
            normal_count=50,
            ambiguous_count=20,
            irrelevant_count=20,
            conflict_count=20,
            correct_shipment_match_rate=match_rate,
            correct_action_rate=action_rate,
            false_action_rate=false_action_rate,
            human_escalation_rate=escalation_rate,
            duplicate_action_rate=duplicate_action_rate,
            latency_p50_ms=p50,
            latency_p95_ms=p95,
            total_cost_estimate=total_cost,
            passed=passed,
            summary=summary,
        )
