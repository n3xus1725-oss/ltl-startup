"""Phase 2.3 — Source Authority Rules Engine.

Defines field-specific precedence and authority matrices:
- invoice amount: contract/rate agreement (100) > invoice (70) > email quote (40)
- shipment weight: verified reweigh / scale ticket (100) > BOL (80) > rate con (60) > email (40)
- pickup status: verified carrier event (100) > BOL signed (90) > carrier email (60) > manual (30)
- delivery status: POD signed (100) > carrier EDI (95) > carrier email (60)
- Exact precedence is configurable per organization.
"""

from enum import Enum
from typing import Dict, Optional

from packages.domain.provenance import FieldProvenanceRecord


class AssertionDecision(str, Enum):
    ACCEPT_CANONICAL = "accept_canonical"
    REJECT_LOWER_AUTHORITY = "reject_lower_authority"
    CORROBORATE = "corroborate"
    FLAG_CONFLICT = "flag_conflict"


DEFAULT_FIELD_AUTHORITY_MATRIX: Dict[str, Dict[str, int]] = {
    # Pricing fields
    "pricing.agreed_total": {
        "rate_confirmation": 100,
        "contract": 100,
        "invoice": 70,
        "carrier_email": 40,
        "manual_entry": 30,
    },
    "pricing.linehaul": {
        "rate_confirmation": 100,
        "contract": 100,
        "invoice": 70,
        "carrier_email": 40,
        "manual_entry": 30,
    },
    "pricing.billed_total": {
        "invoice": 100,
        "carrier_email": 50,
        "manual_entry": 30,
    },
    # Weight fields
    "freight_details.total_weight_lbs": {
        "scale_ticket": 100,
        "verified_reweigh": 100,
        "bol": 80,
        "rate_confirmation": 60,
        "carrier_email": 40,
        "manual_entry": 30,
    },
    # Pickup status & timestamps
    "dates.actual_pickup": {
        "carrier_edi": 100,
        "bol": 90,
        "carrier_email": 60,
        "manual_entry": 30,
    },
    # Delivery status & timestamps
    "dates.actual_delivery": {
        "pod": 100,
        "carrier_edi": 95,
        "carrier_email": 60,
        "manual_entry": 30,
    },
    # Status lifecycle
    "status": {
        "pod": 100,
        "carrier_edi": 95,
        "bol": 90,
        "rate_confirmation": 80,
        "carrier_email": 60,
        "manual_entry": 30,
    },
    # Carrier equipment
    "carrier.trailer_number": {
        "bol": 90,
        "carrier_edi": 85,
        "carrier_email": 60,
        "manual_entry": 30,
    },
    "carrier.carrier_name": {
        "rate_confirmation": 100,
        "carrier_edi": 95,
        "bol": 90,
        "carrier_email": 60,
    },
}


class SourceAuthorityEngine:
    """Evaluates field assertions against authority matrix and current canonical state."""

    def __init__(self, organization_overrides: Optional[Dict[str, Dict[str, int]]] = None):
        self.matrix = dict(DEFAULT_FIELD_AUTHORITY_MATRIX)
        if organization_overrides:
            for field, overrides in organization_overrides.items():
                if field not in self.matrix:
                    self.matrix[field] = {}
                self.matrix[field].update(overrides)

    def get_authority_score(self, field: str, source: str) -> int:
        """Return configured authority score for field and source."""
        field_rules = self.matrix.get(field)
        if field_rules and source in field_rules:
            return field_rules[source]
        # Generic source fallbacks if field specific rule not explicit
        generic_defaults = {
            "scale_ticket": 100,
            "verified_reweigh": 100,
            "pod": 95,
            "rate_confirmation": 90,
            "bol": 85,
            "invoice": 75,
            "carrier_edi": 80,
            "carrier_email": 50,
            "manual_entry": 30,
        }
        return generic_defaults.get(source, 50)

    def evaluate(
        self,
        current: Optional[FieldProvenanceRecord],
        proposed: FieldProvenanceRecord,
        tolerance_pct: float = 0.01,
    ) -> AssertionDecision:
        """Compare proposed assertion against current canonical state and decide action.

        Returns:
            - ACCEPT_CANONICAL: proposed takes precedence as new canonical truth
            - CORROBORATE: same value asserted, bolstering confidence
            - REJECT_LOWER_AUTHORITY: lower authority than verified truth, recorded to history only
            - FLAG_CONFLICT: equal authority with mismatched values or significant contradiction
        """
        # First assertion on this field -> always accepts
        if current is None or current.value is None:
            return AssertionDecision.ACCEPT_CANONICAL

        curr_val = current.value
        prop_val = proposed.value

        # Calculate authoritative scores
        curr_auth = current.authority or self.get_authority_score(current.field, current.source)
        prop_auth = proposed.authority or self.get_authority_score(proposed.field, proposed.source)
        proposed.authority = prop_auth

        # Check for matching values (allowing float tolerance for numeric fields)
        is_same_value = False
        if isinstance(curr_val, (int, float)) and isinstance(prop_val, (int, float)):
            delta = abs(float(curr_val) - float(prop_val))
            max_val = max(abs(float(curr_val)), abs(float(prop_val)), 1.0)
            if delta / max_val <= tolerance_pct:
                is_same_value = True
        elif str(curr_val).strip().lower() == str(prop_val).strip().lower():
            is_same_value = True

        if is_same_value:
            return AssertionDecision.CORROBORATE

        # Values differ: evaluate precedence
        if prop_auth > curr_auth:
            return AssertionDecision.ACCEPT_CANONICAL
        elif prop_auth < curr_auth:
            return AssertionDecision.REJECT_LOWER_AUTHORITY
        else:
            # Equal authority asserting conflicting values
            return AssertionDecision.FLAG_CONFLICT
