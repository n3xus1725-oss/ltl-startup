"""Phase 1.5 — Shipment Resolver.

Extracts candidate identifiers from email subject & body (Load ID, Shipment ID, PRO number,
Invoice number, BOL number), matches deterministically against ShipmentRepository, scores
matches, and optionally uses LLM fallback only when deterministic matches are missing or ambiguous.

Rule: Never choose arbitrarily when two shipments are plausible; mark as ambiguous.
"""

import json
import re
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from packages.domain.logging import logger
from packages.domain.models import Shipment
from packages.llm.gateway import LLMGateway
from packages.storage.repositories.shipments import ShipmentRepository


class CandidateIdentifier(BaseModel):
    id_type: str  # load_id, bol_number, pro_number, invoice_number, shipment_number, generic
    raw_value: str
    normalized_value: str


class ShipmentMatchResult(BaseModel):
    matched_entity: Optional[Dict[str, Any]] = None
    shipment_id: Optional[str] = None
    match_type: str = "none"  # deterministic_exact, deterministic_ambiguous, llm_fallback, none
    is_ambiguous: bool = False
    evidence: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0


def shipment_to_dict(shipment: Shipment) -> Dict[str, Any]:
    """Serialize a Shipment SQLAlchemy model to a clean dictionary."""
    return {
        "id": str(shipment.id),
        "organization_id": str(shipment.organization_id),
        "shipment_number": shipment.shipment_number,
        "carrier_name": shipment.carrier_name,
        "carrier_reference": shipment.carrier_reference,
        "bol_number": shipment.bol_number,
        "load_id": shipment.load_id,
        "external_invoice_id": shipment.external_invoice_id,
        "status": shipment.status,
        "pickup_date": shipment.pickup_date.isoformat() if shipment.pickup_date else None,
        "delivery_date": shipment.delivery_date.isoformat() if shipment.delivery_date else None,
        "eta": shipment.eta.isoformat() if shipment.eta else None,
        "weight_lbs": shipment.weight_lbs,
        "pallet_count": shipment.pallet_count,
        "total_charges": float(shipment.total_charges) if shipment.total_charges is not None else None,
        "origin_address": shipment.origin_address or {},
        "destination_address": shipment.destination_address or {},
        "metadata_payload": shipment.metadata_payload or {},
    }


class ShipmentResolver:
    """Deterministic shipment resolver with optional LLM fallback."""

    # Regex patterns for explicit candidate identification
    # Order matters: more specific patterns match before generic patterns
    REGEX_PATTERNS = [
        # Load ID: "Load 5821", "Load #5821", "LOAD-1234", "Load ID: 5821"
        (
            "load_id",
            re.compile(
                r"\b(?:load)\s*(?:id|#|no\.?|num(?:ber)?)?\s*[:#-]?\s*([A-Za-z0-9\-_]{3,25})\b",
                re.IGNORECASE,
            ),
        ),
        # PRO number: "PRO 9823411", "PRO# 9823411", "PRO-9823411", "PRO: 9823411"
        (
            "pro_number",
            re.compile(
                r"\b(?:pro)\s*(?:#|no\.?|num(?:ber)?)?\s*[:#-]?\s*([A-Za-z0-9\-_]{4,25})\b",
                re.IGNORECASE,
            ),
        ),
        # BOL number: "BOL-847293", "BOL #847293", "Bill of Lading: 847293", "B/L 847293"
        (
            "bol_number",
            re.compile(
                r"\b(?:bill\s+of\s+lading|bol|b/l)\s*(?:#|no\.?|num(?:ber)?)?\s*[:#-]?\s*([A-Za-z0-9\-_]{4,25})\b",
                re.IGNORECASE,
            ),
        ),
        # Invoice number: "INV-8892", "Invoice #8892", "Invoice: 8892", "INV #8892"
        (
            "invoice_number",
            re.compile(
                r"\b(?:invoice|inv)\s*(?:#|no\.?|num(?:ber)?)?\s*[:#-]?\s*([A-Za-z0-9\-_]{3,25})\b",
                re.IGNORECASE,
            ),
        ),
        # Shipment ID: "Shipment #12345", "Shipment 12345", "SHP-12345", "Shipment ID: SHP-90"
        (
            "shipment_number",
            re.compile(
                r"\b(?:shipment|shp)\s*(?:#|no\.?|num(?:ber)?|id)?\s*[:#-]?\s*([A-Za-z0-9\-_]{3,25})\b",
                re.IGNORECASE,
            ),
        ),
        # Tagged patterns like LOAD-1234, BOL-847293, INV-8892, SHP-9812, PRO-9823411
        (
            "load_id",
            re.compile(r"\b(LOAD-[A-Za-z0-9]{3,20})\b", re.IGNORECASE),
        ),
        (
            "bol_number",
            re.compile(r"\b(BOL-[A-Za-z0-9]{3,20})\b", re.IGNORECASE),
        ),
        (
            "invoice_number",
            re.compile(r"\b(INV-[A-Za-z0-9]{3,20})\b", re.IGNORECASE),
        ),
        (
            "shipment_number",
            re.compile(r"\b(SHP-[A-Za-z0-9]{3,20})\b", re.IGNORECASE),
        ),
        (
            "pro_number",
            re.compile(r"\b(PRO-[A-Za-z0-9]{3,20})\b", re.IGNORECASE),
        ),
    ]

    def __init__(
        self,
        shipment_repo: ShipmentRepository,
        llm_gateway: Optional[LLMGateway] = None,
    ):
        self.shipment_repo = shipment_repo
        self.llm_gateway = llm_gateway

    def extract_candidate_identifiers(
        self, subject: Optional[str] = None, body: Optional[str] = None
    ) -> List[CandidateIdentifier]:
        """Extract candidate freight identifiers from subject and body text."""
        combined_text = f"{subject or ''}\n{body or ''}".strip()
        if not combined_text:
            return []

        candidates: List[CandidateIdentifier] = []
        seen_values: Set[str] = set()

        for id_type, pattern in self.REGEX_PATTERNS:
            for match in pattern.finditer(combined_text):
                raw_val = match.group(1).strip().strip(".,;:()")
                # Clean up prefix if captured inside
                clean_val = raw_val
                norm_upper = clean_val.upper()

                if norm_upper not in seen_values and len(norm_upper) >= 3:
                    seen_values.add(norm_upper)
                    candidates.append(
                        CandidateIdentifier(
                            id_type=id_type,
                            raw_value=raw_val,
                            normalized_value=norm_upper,
                        )
                    )

        return candidates

    def _generate_candidate_lookup_variations(
        self, candidate: CandidateIdentifier
    ) -> List[str]:
        """Generate lookup variations for a candidate identifier.
        e.g., for '5821' of type 'load_id', checks:
        '5821', 'LOAD-5821', 'LOAD 5821', 'LOAD#5821'.
        For 'PRO 9823411', checks '9823411', 'PRO-9823411', 'PRO 9823411', etc.
        """
        val = candidate.normalized_value
        variations = [val]

        word_map = {
            "load_id": "LOAD",
            "bol_number": "BOL",
            "invoice_number": "INV",
            "shipment_number": "SHP",
            "pro_number": "PRO",
        }

        word = word_map.get(candidate.id_type)
        if word:
            stripped = val
            for p in (f"{word}-", f"{word} ", f"{word}#", f"{word}:", f"{word}."):
                if stripped.startswith(p):
                    stripped = stripped[len(p):].strip()

            variations.extend([
                stripped,
                f"{word}-{stripped}",
                f"{word} {stripped}",
                f"{word}#{stripped}",
            ])

        return list(dict.fromkeys(variations))

    async def resolve(
        self,
        organization_id: uuid.UUID,
        subject: Optional[str] = None,
        body: Optional[str] = None,
        enable_llm_fallback: bool = True,
    ) -> ShipmentMatchResult:
        """Resolve an email to a canonical shipment record.

        Returns ShipmentMatchResult with:
        - matched_entity: dict or None
        - match_type: deterministic_exact | deterministic_ambiguous | llm_fallback | none
        - evidence: structured evidence dict
        - confidence: 1.0 (exact match), 0.0 (none), 0.5 (ambiguous)
        """
        candidates = self.extract_candidate_identifiers(subject, body)

        # 1. Deterministic database search across all candidate identifiers
        matched_shipments_by_id: Dict[uuid.UUID, Shipment] = {}
        matched_evidence: List[Dict[str, Any]] = []

        for candidate in candidates:
            variations = self._generate_candidate_lookup_variations(candidate)
            for var in variations:
                shipments = self.shipment_repo.find_by_identifier(organization_id, var)
                for s in shipments:
                    if s.id not in matched_shipments_by_id:
                        matched_shipments_by_id[s.id] = s
                        matched_evidence.append({
                            "candidate_type": candidate.id_type,
                            "candidate_value": candidate.raw_value,
                            "matched_value": var,
                            "shipment_id": str(s.id),
                            "shipment_number": s.shipment_number,
                            "load_id": s.load_id,
                            "bol_number": s.bol_number,
                            "carrier_reference": s.carrier_reference,
                            "external_invoice_id": s.external_invoice_id,
                        })

        distinct_matches = list(matched_shipments_by_id.values())

        # 2. Score deterministic results
        # Rule: Exact single match -> confidence = 1.0
        if len(distinct_matches) == 1:
            matched_shipment = distinct_matches[0]
            logger.info(
                f"Deterministic match resolved: shipment {matched_shipment.shipment_number} for org {organization_id}"
            )
            return ShipmentMatchResult(
                matched_entity=shipment_to_dict(matched_shipment),
                shipment_id=str(matched_shipment.id),
                match_type="deterministic_exact",
                is_ambiguous=False,
                confidence=1.0,
                evidence={
                    "strategy": "deterministic",
                    "matched_candidates": matched_evidence,
                    "all_candidates": [c.model_dump() for c in candidates],
                },
            )

        # Rule: Multiple plausible shipments -> Never choose arbitrarily; mark as ambiguous!
        if len(distinct_matches) > 1:
            logger.warning(
                f"Ambiguous resolution: {len(distinct_matches)} shipments matched candidates for org {organization_id}"
            )
            return ShipmentMatchResult(
                matched_entity=None,
                shipment_id=None,
                match_type="deterministic_ambiguous",
                is_ambiguous=True,
                confidence=0.5,
                evidence={
                    "strategy": "deterministic_ambiguous",
                    "reason": "Multiple distinct shipments matched extracted candidates",
                    "plausible_shipment_ids": [str(s.id) for s in distinct_matches],
                    "plausible_shipment_numbers": [s.shipment_number for s in distinct_matches],
                    "matched_candidates": matched_evidence,
                    "all_candidates": [c.model_dump() for c in candidates],
                },
            )

        # 3. No deterministic match found -> evaluate LLM fallback if enabled and gateway available
        if enable_llm_fallback and self.llm_gateway:
            llm_result = await self._try_llm_fallback(
                organization_id=organization_id,
                subject=subject,
                body=body,
                candidates=candidates,
            )
            if llm_result:
                return llm_result

        # 4. No matches found
        return ShipmentMatchResult(
            matched_entity=None,
            shipment_id=None,
            match_type="none",
            is_ambiguous=False,
            confidence=0.0,
            evidence={
                "strategy": "none",
                "reason": "No matching shipment found in database",
                "extracted_candidates": [c.model_dump() for c in candidates],
            },
        )

    async def _try_llm_fallback(
        self,
        organization_id: uuid.UUID,
        subject: Optional[str],
        body: Optional[str],
        candidates: List[CandidateIdentifier],
    ) -> Optional[ShipmentMatchResult]:
        """Attempt LLM fallback to resolve shipment when deterministic match is absent or ambiguous.
        Only called when deterministic matches are missing or ambiguous.
        """
        try:
            # Query recent active shipments for this organization (limited window to prevent context overflow)
            active_shipments = self.shipment_repo.list_all(organization_id, limit=25)
            if not active_shipments:
                return None

            shipment_summaries = [
                {
                    "id": str(s.id),
                    "shipment_number": s.shipment_number,
                    "load_id": s.load_id,
                    "carrier_reference": s.carrier_reference,
                    "bol_number": s.bol_number,
                    "carrier_name": s.carrier_name,
                    "status": s.status,
                }
                for s in active_shipments
            ]

            prompt = (
                "You are an expert freight logistics entity resolver.\n"
                "Analyze the email subject and body below and determine if it unambiguously references "
                "one of the existing shipments in the database.\n"
                "CRITICAL RULE: If more than one shipment is plausible, or if you are not completely certain, "
                "set is_ambiguous=true and matched_shipment_id=null. NEVER guess arbitrarily.\n\n"
                f"Candidate database shipments:\n{json.dumps(shipment_summaries, indent=2)}\n\n"
                f"Email Subject: {subject or ''}\n"
                f"Email Body: {body or ''}\n\n"
                "Respond with a JSON object strictly matching this schema:\n"
                "{\n"
                '  "matched_shipment_id": "<uuid or null>",\n'
                '  "is_ambiguous": <true or false>,\n'
                '  "confidence": <float between 0.0 and 1.0>,\n'
                '  "reasoning": "<concise explanation of match or ambiguity>"\n'
                "}"
            )

            response = await self.llm_gateway.complete(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )

            data = response.parsed_json or {}
            matched_id_str = data.get("matched_shipment_id")
            is_ambiguous = data.get("is_ambiguous", False)
            confidence = float(data.get("confidence", 0.0))
            reasoning = data.get("reasoning", "")

            if is_ambiguous:
                return ShipmentMatchResult(
                    matched_entity=None,
                    shipment_id=None,
                    match_type="deterministic_ambiguous",
                    is_ambiguous=True,
                    confidence=confidence or 0.5,
                    evidence={
                        "strategy": "llm_fallback_ambiguous",
                        "reasoning": reasoning,
                    },
                )

            if matched_id_str:
                matched_uuid = uuid.UUID(matched_id_str)
                shipment = self.shipment_repo.get_by_id(organization_id, matched_uuid)
                if shipment and confidence >= 0.8:
                    return ShipmentMatchResult(
                        matched_entity=shipment_to_dict(shipment),
                        shipment_id=str(shipment.id),
                        match_type="llm_fallback",
                        is_ambiguous=False,
                        confidence=confidence,
                        evidence={
                            "strategy": "llm_fallback",
                            "reasoning": reasoning,
                        },
                    )

        except Exception as e:
            logger.warning(f"ShipmentResolver LLM fallback error: {str(e)}")

        return None
