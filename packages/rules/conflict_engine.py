"""Phase 2.4 — Conflict Engine.

Detects and categorizes truth discrepancies across 5 dimensions:
1. value_mismatch (e.g., Rate Con agreed rate vs Invoice billed rate; BOL weight vs Reweigh scale ticket)
2. missing_evidence (e.g., accessorial charge claimed without photo/scale ticket, or low confidence)
3. stale_value (e.g., older assertion attempting to overwrite newer verified event)
4. incompatible_status (e.g., attempting delivered before pickup, or reopening delivered shipment)
5. conflicting_party_identity (e.g., carrier name/SCAC or consignee destination mismatch)

Each conflict provides:
- field
- source A
- source B
- severity: low, medium, high, critical
- explanation: plain-language explanation of discrepancy and financial/operational impact
- recommended_workflow: rate_dispute_agent, reweigh_verification, operator_review, carrier_inquiry, missing_information_request
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from packages.domain.provenance import FieldProvenanceRecord


class DetectedConflict(BaseModel):
    field_name: str
    conflict_type: str  # value_mismatch, missing_evidence, stale_value, incompatible_status, conflicting_party_identity
    source_a: Dict[str, Any]
    source_b: Dict[str, Any]
    severity: str = "medium"  # low, medium, high, critical
    explanation: str
    recommended_workflow: str  # rate_dispute_agent, reweigh_verification, operator_review, carrier_inquiry, missing_information_request
    metadata: Dict[str, Any] = Field(default_factory=dict)


# State machine allowed transitions
ALLOWED_STATUS_TRANSITIONS = {
    "created": {"booked", "cancelled"},
    "booked": {"dispatched", "picked_up", "cancelled"},
    "dispatched": {"picked_up", "cancelled"},
    "picked_up": {"in_transit", "delivered"},
    "in_transit": {"out_for_delivery", "delivered"},
    "out_for_delivery": {"delivered"},
    "delivered": set(),  # terminal
    "cancelled": set(),  # terminal
}


class ConflictEngine:
    """Automated discrepancy and conflict detection engine."""

    @staticmethod
    def evaluate_value_mismatch(
        field_name: str,
        current: FieldProvenanceRecord,
        proposed: FieldProvenanceRecord,
    ) -> Optional[DetectedConflict]:
        """Detect numeric and categorical value discrepancies between two authoritative sources."""
        val_a = current.value
        val_b = proposed.value

        # Pricing comparison
        if "pricing" in field_name or "rate" in field_name or "charges" in field_name:
            try:
                amt_a = float(val_a)
                amt_b = float(val_b)
                diff = abs(amt_b - amt_a)
                if diff > 0.01:
                    if diff >= 500.0:
                        severity = "critical"
                    elif diff >= 100.0:
                        severity = "high"
                    else:
                        severity = "medium"

                    return DetectedConflict(
                        field_name=field_name,
                        conflict_type="value_mismatch",
                        source_a=current.to_dict(),
                        source_b=proposed.to_dict(),
                        severity=severity,
                        explanation=(
                            f"Pricing discrepancy of ${diff:.2f} detected between {current.source} "
                            f"(${amt_a:.2f}) and {proposed.source} (${amt_b:.2f})."
                        ),
                        recommended_workflow="rate_dispute_agent",
                        metadata={"delta_amount": diff, "expected": amt_a, "billed": amt_b},
                    )
            except (ValueError, TypeError):
                pass

        # Weight comparison
        if "weight" in field_name:
            try:
                w_a = float(val_a)
                w_b = float(val_b)
                diff = abs(w_b - w_a)
                pct_diff = diff / max(w_a, w_b, 1.0)
                if diff >= 100.0 or pct_diff > 0.02:  # Over 100 lbs or > 2%
                    severity = "high" if pct_diff > 0.10 else "medium"
                    return DetectedConflict(
                        field_name=field_name,
                        conflict_type="value_mismatch",
                        source_a=current.to_dict(),
                        source_b=proposed.to_dict(),
                        severity=severity,
                        explanation=(
                            f"Weight discrepancy of {diff:.1f} lbs ({pct_diff * 100:.1f}%) between "
                            f"{current.source} ({w_a:.1f} lbs) and {proposed.source} ({w_b:.1f} lbs)."
                        ),
                        recommended_workflow="reweigh_verification",
                        metadata={"delta_lbs": diff, "pct_diff": pct_diff},
                    )
            except (ValueError, TypeError):
                pass

        # General string/categorical mismatch
        if str(val_a).strip().lower() != str(val_b).strip().lower():
            return DetectedConflict(
                field_name=field_name,
                conflict_type="value_mismatch",
                source_a=current.to_dict(),
                source_b=proposed.to_dict(),
                severity="medium",
                explanation=f"Value mismatch on {field_name}: '{val_a}' vs '{val_b}'.",
                recommended_workflow="operator_review",
            )

        return None

    @staticmethod
    def evaluate_stale_value(
        field_name: str,
        current: FieldProvenanceRecord,
        proposed: FieldProvenanceRecord,
    ) -> Optional[DetectedConflict]:
        """Detect when an older message or document asserts a value against newer verified state."""
        if proposed.timestamp < current.timestamp:
            return DetectedConflict(
                field_name=field_name,
                conflict_type="stale_value",
                source_a=current.to_dict(),
                source_b=proposed.to_dict(),
                severity="low",
                explanation=(
                    f"Assertion from {proposed.source} is stale (dated {proposed.timestamp.isoformat()}), "
                    f"superseded by {current.source} (dated {current.timestamp.isoformat()})."
                ),
                recommended_workflow="operator_review",
            )
        return None

    @staticmethod
    def evaluate_incompatible_status(
        current_status: str,
        proposed_status: str,
        current_record: Optional[FieldProvenanceRecord] = None,
        proposed_record: Optional[FieldProvenanceRecord] = None,
    ) -> Optional[DetectedConflict]:
        """Verify lifecycle progression and flag illegal state jumps."""
        c_stat = (current_status or "created").lower().strip()
        p_stat = (proposed_status or "").lower().strip()

        if c_stat == p_stat:
            return None

        # Check valid transitions
        allowed = ALLOWED_STATUS_TRANSITIONS.get(c_stat, set())
        if p_stat not in allowed:
            # Illegal jump (e.g. created -> delivered, or delivered -> in_transit)
            src_a = current_record.to_dict() if current_record else {"source": "canonical", "value": c_stat}
            src_b = proposed_record.to_dict() if proposed_record else {"source": "proposed", "value": p_stat}
            return DetectedConflict(
                field_name="status",
                conflict_type="incompatible_status",
                source_a=src_a,
                source_b=src_b,
                severity="high",
                explanation=f"Illegal status jump from '{c_stat}' to '{p_stat}' violates lifecycle rules.",
                recommended_workflow="operator_review",
            )
        return None

    @staticmethod
    def evaluate_missing_evidence(
        field_name: str,
        proposed: FieldProvenanceRecord,
    ) -> Optional[DetectedConflict]:
        """Flag high-risk assertions that lack required evidence or meet low confidence thresholds."""
        # Low confidence assertion (< 0.70)
        if proposed.confidence < 0.70:
            return DetectedConflict(
                field_name=field_name,
                conflict_type="missing_evidence",
                source_a={"source": "policy", "value": "required_confidence >= 0.70"},
                source_b=proposed.to_dict(),
                severity="medium",
                explanation=(
                    f"Assertion on '{field_name}' by {proposed.source} has insufficient confidence ({proposed.confidence:.2f})."
                ),
                recommended_workflow="missing_information_request",
            )
        return None

    @staticmethod
    def evaluate_conflicting_party_identity(
        field_name: str,
        current: FieldProvenanceRecord,
        proposed: FieldProvenanceRecord,
    ) -> Optional[DetectedConflict]:
        """Detect identity mismatches (e.g. carrier name/SCAC mismatch or destination mismatch)."""
        val_a = str(current.value or "").strip().lower()
        val_b = str(proposed.value or "").strip().lower()

        if val_a and val_b and val_a != val_b:
            # Check for substantial string difference
            if len(val_a) > 3 and len(val_b) > 3 and val_a not in val_b and val_b not in val_a:
                return DetectedConflict(
                    field_name=field_name,
                    conflict_type="conflicting_party_identity",
                    source_a=current.to_dict(),
                    source_b=proposed.to_dict(),
                    severity="critical",
                    explanation=(
                        f"Conflicting party identity on {field_name}: '{current.value}' vs '{proposed.value}'. "
                        f"Requires security and dispatch verification."
                    ),
                    recommended_workflow="operator_review",
                )
        return None
