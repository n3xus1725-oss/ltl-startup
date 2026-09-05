"""Phase 1.9 — Evaluation Dataset Generation.

Generates the standardized 110-email golden test suite:
- 50 normal freight emails (Pickup confirmations, ETA updates, Missing info)
- 20 ambiguous freight emails (multiple plausible shipments)
- 20 irrelevant emails (marketing, newsletters, out of office)
- 20 conflicting emails (extreme delays > 48h, contradictory data)
"""

from typing import List, Optional

from pydantic import BaseModel


class EvalEmailCase(BaseModel):
    id: str
    category: str  # normal, ambiguous, irrelevant, conflict
    subject: str
    body: str
    sender: str
    thread_id: str
    expected_match: bool
    expected_shipment_number: Optional[str] = None
    expected_intent: str
    expected_outcome: str  # completed, needs_human
    should_escalate: bool


def generate_eval_dataset() -> List[EvalEmailCase]:
    """Generate the 110-item canonical evaluation dataset."""
    dataset: List[EvalEmailCase] = []

    # ---------------- 1. 50 Normal Emails ---------------- #
    # 20 Pickup confirmations
    for i in range(1, 21):
        shp_num = f"SHP-NORM-{i:03d}"
        load_num = f"LOAD-{1000 + i}"
        dataset.append(
            EvalEmailCase(
                id=f"norm-pickup-{i:03d}",
                category="normal",
                subject=f"Pickup Confirmation: {load_num}",
                body=f"Driver has departed the shipper for {load_num}. All pallets loaded in good condition.",
                sender=f"driver{i}@swiftlogistics.com",
                thread_id=f"thread-norm-{i}",
                expected_match=True,
                expected_shipment_number=shp_num,
                expected_intent="pickup_confirmation",
                expected_outcome="completed",
                should_escalate=False,
            )
        )

    # 15 ETA updates (within normal SLA)
    for i in range(21, 36):
        shp_num = f"SHP-NORM-{i:03d}"
        pro_num = f"PRO {9000000 + i}"
        dataset.append(
            EvalEmailCase(
                id=f"norm-eta-{i:03d}",
                category="normal",
                subject=f"{pro_num} - Updated ETA",
                body=f"Traffic on I-80 cleared. Revised arrival for {pro_num} is tomorrow at 16:00.",
                sender=f"dispatch{i}@carrierline.com",
                thread_id=f"thread-norm-{i}",
                expected_match=True,
                expected_shipment_number=shp_num,
                expected_intent="eta_update",
                expected_outcome="completed",
                should_escalate=False,
            )
        )

    # 15 Missing-information requests
    for i in range(36, 51):
        shp_num = f"SHP-NORM-{i:03d}"
        bol_num = f"BOL-{840000 + i}"
        dataset.append(
            EvalEmailCase(
                id=f"norm-missing-{i:03d}",
                category="normal",
                subject=f"Missing Information on {bol_num}",
                body=f"We received the staging request for {bol_num} but are missing paperwork. Please provide weight and piece count.",
                sender=f"warehouse{i}@shippers.com",
                thread_id=f"thread-norm-{i}",
                expected_match=True,
                expected_shipment_number=shp_num,
                expected_intent="missing_information",
                expected_outcome="completed",
                should_escalate=False,
            )
        )

    # ---------------- 2. 20 Ambiguous Emails ---------------- #
    for i in range(1, 21):
        load_a = f"LOAD-{2000 + (i * 2)}"
        load_b = f"LOAD-{2000 + (i * 2) + 1}"
        dataset.append(
            EvalEmailCase(
                id=f"ambig-{i:03d}",
                category="ambiguous",
                subject=f"Cross-dock Status: {load_a} & {load_b}",
                body=f"Both loads {load_a} and {load_b} were delivered to the hub. Please confirm which one needs repalletizing.",
                sender=f"terminal{i}@freighthub.com",
                thread_id=f"thread-ambig-{i}",
                expected_match=False,
                expected_shipment_number=None,
                expected_intent="ambiguous",
                expected_outcome="needs_human",
                should_escalate=True,
            )
        )

    # ---------------- 3. 20 Irrelevant Emails ---------------- #
    irrelevant_subjects = [
        ("Weekly Fuel Surcharge Digest", "Please find attached our national diesel price index newsletter."),
        ("Happy Holidays from Great Lakes Transport", "Wishing your team a wonderful Thanksgiving holiday."),
        ("Out of Office: Regional Logistics Manager", "I will be out of office until Monday with limited access to email."),
        ("Quarterly Freight Rate Outlook", "Download our comprehensive dry van and reefer market analysis."),
        ("Invitation: Annual Transportation Summit", "Join us for the 2026 Chicago Logistics Conference."),
    ]
    for i in range(1, 21):
        subj, body = irrelevant_subjects[(i - 1) % len(irrelevant_subjects)]
        dataset.append(
            EvalEmailCase(
                id=f"irrel-{i:03d}",
                category="irrelevant",
                subject=f"{subj} #{i}",
                body=f"{body} To unsubscribe, click here.",
                sender=f"marketing{i}@industrynews.com",
                thread_id=f"thread-irrel-{i}",
                expected_match=False,
                expected_shipment_number=None,
                expected_intent="unrelated",
                expected_outcome="completed",
                should_escalate=False,
            )
        )

    # ---------------- 4. 20 Conflicting Emails ---------------- #
    for i in range(1, 21):
        shp_num = f"SHP-CONF-{i:03d}"
        load_num = f"LOAD-{3000 + i}"
        # 5 days delay = 120 hours (> 48h threshold)
        dataset.append(
            EvalEmailCase(
                id=f"conflict-{i:03d}",
                category="conflict",
                subject=f"{load_num} - Catastrophic Breakdown Delay",
                body=f"Major engine blowout on {load_num}. Truck is in shop for repair. Revised arrival delayed to 2026-09-18T18:00:00+00:00.",
                sender=f"breakdown{i}@expressline.com",
                thread_id=f"thread-conflict-{i}",
                expected_match=True,
                expected_shipment_number=shp_num,
                expected_intent="eta_update",
                expected_outcome="needs_human",
                should_escalate=True,
            )
        )

    assert len(dataset) == 110, f"Expected 110 cases, generated {len(dataset)}"
    return dataset
