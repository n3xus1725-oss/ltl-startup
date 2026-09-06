"""Phase 3.3 — Billing Audit Rule Schemas and Findings."""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AuditRuleId(str, Enum):
    RULE_01_DUPLICATE_INVOICE = "RULE_01_DUPLICATE_INVOICE"
    RULE_02_LINEHAUL_MISMATCH = "RULE_02_LINEHAUL_MISMATCH"
    RULE_03_FUEL_MISMATCH = "RULE_03_FUEL_MISMATCH"
    RULE_04_UNSUPPORTED_ACCESSORIAL = "RULE_04_UNSUPPORTED_ACCESSORIAL"
    RULE_05_WEIGHT_MISMATCH = "RULE_05_WEIGHT_MISMATCH"
    RULE_06_CLASS_MISMATCH = "RULE_06_CLASS_MISMATCH"
    RULE_07_RECLASS_DISCREPANCY = "RULE_07_RECLASS_DISCREPANCY"
    RULE_08_DIMENSION_PALLET_DISCREPANCY = "RULE_08_DIMENSION_PALLET_DISCREPANCY"
    RULE_09_QUOTE_VERSUS_INVOICE_MISMATCH = "RULE_09_QUOTE_VERSUS_INVOICE_MISMATCH"
    RULE_10_ARITHMETIC_TOTAL_MISMATCH = "RULE_10_ARITHMETIC_TOTAL_MISMATCH"


class AuditSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AuditFinding(BaseModel):
    """Structured evidence finding produced by a deterministic audit rule."""

    rule_id: str
    rule_name: str
    severity: AuditSeverity = AuditSeverity.MEDIUM
    expected_value: Optional[Any] = None
    billed_value: Optional[Any] = None
    difference: float = 0.0
    reason: str
    confidence: float = 1.0  # Deterministic code always yields 1.0 confidence
    source_documents: List[str] = Field(default_factory=list)
    evidence_references: List[Dict[str, Any]] = Field(default_factory=list)
    recommended_action: Optional[str] = None


class AuditReport(BaseModel):
    """Complete billing audit evaluation report for an invoice."""

    invoice_id: Optional[str] = None
    shipment_id: Optional[str] = None
    is_clean: bool = True
    total_discrepancy_amount: float = 0.0
    findings: List[AuditFinding] = Field(default_factory=list)
    audited_at: str
