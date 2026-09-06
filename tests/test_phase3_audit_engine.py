"""Phase 3.3 Test Suite — Deterministic Audit Engine (All 10 Audit Rules).

Per Section 6.4:
- Financial correctness tests: billing audit calculations require deterministic expected answers.
- The model must never determine arithmetic.
- All 10 rules must return structured evidence.
"""

import pytest

from packages.rules.audit_engine import DeterministicAuditEngine
from packages.rules.audit_schemas import AuditRuleId, AuditSeverity


@pytest.fixture
def engine():
    return DeterministicAuditEngine()


def test_rule_01_duplicate_invoice(engine):
    """Rule 1: Detect duplicate invoice number for the same carrier."""
    invoice = {
        "id": "inv-curr",
        "invoice_number": "INV-1001",
        "carrier_name": "Estes Express Lines",
        "total_billed_amount": 1650.00,
    }
    existing_invoices = [
        {
            "id": "inv-prior",
            "invoice_number": "INV-1001",
            "carrier_name": "Estes Express Lines",
            "total_billed_amount": 1650.00,
            "status": "paid",
        }
    ]

    report = engine.audit_invoice(invoice=invoice, existing_invoices=existing_invoices)
    assert not report.is_clean
    finding = next((f for f in report.findings if f.rule_id == AuditRuleId.RULE_01_DUPLICATE_INVOICE.value), None)
    assert finding is not None
    assert finding.severity == AuditSeverity.CRITICAL
    assert finding.difference == 1650.00
    assert "previously ingested" in finding.reason


def test_rule_02_linehaul_mismatch(engine):
    """Rule 2: Detect linehaul rate overcharge ($1,850 billed vs $1,650 agreed = $200 variance)."""
    invoice = {
        "invoice_number": "INV-LH-02",
        "carrier_name": "Estes Express Lines",
        "linehaul_amount": 1850.00,
        "total_billed_amount": 1850.00,
    }
    shipment = {
        "canonical_data": {
            "pricing": {"agreed_linehaul": 1650.00, "agreed_total": 1650.00}
        }
    }

    report = engine.audit_invoice(invoice=invoice, shipment=shipment)
    assert not report.is_clean
    finding = next((f for f in report.findings if f.rule_id == AuditRuleId.RULE_02_LINEHAUL_MISMATCH.value), None)
    assert finding is not None
    assert finding.difference == 200.00
    assert finding.expected_value == 1650.00
    assert finding.billed_value == 1850.00


def test_rule_03_fuel_mismatch(engine):
    """Rule 3: Detect fuel surcharge overcharge ($350 billed vs $247.50 contracted formula = $102.50 variance)."""
    invoice = {
        "invoice_number": "INV-FSC-03",
        "carrier_name": "Estes Express Lines",
        "linehaul_amount": 1650.00,
        "fuel_amount": 350.00,
        "total_billed_amount": 2000.00,
    }
    contract = {
        "base_rate": 1650.00,
        "fuel_schedule": {"type": "percent", "base_rate_percent": 15.0},
    }

    report = engine.audit_invoice(invoice=invoice, contract=contract)
    assert not report.is_clean
    finding = next((f for f in report.findings if f.rule_id == AuditRuleId.RULE_03_FUEL_MISMATCH.value), None)
    assert finding is not None
    assert finding.expected_value == 247.50
    assert finding.billed_value == 350.00
    assert finding.difference == 102.50


def test_rule_04_unsupported_accessorials(engine):
    """Rule 4: Detect unapproved accessorials, missing lumper receipts, and detention lacking timestamps."""
    invoice = {
        "invoice_number": "INV-ACC-04",
        "carrier_name": "Estes Express Lines",
        "linehaul_amount": 1650.00,
        "total_billed_amount": 2025.00,
        "accessorials": [
            {"code": "LUMPER", "name": "Lumper Fee", "amount": 175.00},
            {"code": "DETENTION", "name": "Detention Fee", "amount": 200.00},
        ],
    }
    # Contract does not authorize lumper and no receipt attached
    contract = {
        "accessorial_schedule": {"DETENTION": {"rate_per_hour": 75.0}},
    }

    report = engine.audit_invoice(
        invoice=invoice,
        contract=contract,
        attached_document_types=["BILL_OF_LADING"],  # No LUMPER_RECEIPT attached
    )
    assert not report.is_clean
    finding_codes = [f.rule_id for f in report.findings]
    assert AuditRuleId.RULE_04_UNSUPPORTED_ACCESSORIAL.value in finding_codes
    lumper_finding = next((f for f in report.findings if "Lumper" in f.rule_name), None)
    assert lumper_finding is not None
    assert lumper_finding.difference == 175.00


def test_rule_05_weight_mismatch(engine):
    """Rule 5: Detect discrepancy between invoiced weight and verified scale ticket weight."""
    invoice = {
        "invoice_number": "INV-WT-05",
        "weight_lbs": 18200.0,
        "total_billed_amount": 1650.00,
    }
    scale_ticket = {
        "net_weight_lbs": 16450.0,
    }
    contract = {
        "rate_type": "per_cwt",
        "base_rate": 5.00,  # $5.00 per 100 lbs
    }

    report = engine.audit_invoice(invoice=invoice, scale_ticket=scale_ticket, contract=contract)
    assert not report.is_clean
    finding = next((f for f in report.findings if f.rule_id == AuditRuleId.RULE_05_WEIGHT_MISMATCH.value), None)
    assert finding is not None
    # 18,200 - 16,450 = 1,750 lbs variance -> 17.5 cwt * $5.00 = $87.50
    assert finding.difference == 87.50
    assert finding.expected_value == 16450.0
    assert finding.billed_value == 18200.0


def test_rule_06_class_mismatch(engine):
    """Rule 6: Detect freight class discrepancy between invoice (Class 100) and BOL (Class 70)."""
    invoice = {
        "invoice_number": "INV-CLS-06",
        "freight_class": "100",
        "total_billed_amount": 1650.00,
    }
    bol = {
        "nmfc_class": "70",
    }

    report = engine.audit_invoice(invoice=invoice, bol=bol)
    assert not report.is_clean
    finding = next((f for f in report.findings if f.rule_id == AuditRuleId.RULE_06_CLASS_MISMATCH.value), None)
    assert finding is not None
    assert finding.expected_value == "70"
    assert finding.billed_value == "100"


def test_rule_07_reclass_discrepancy(engine):
    """Rule 7: Detect reclassification fee charged without certified inspection certificate."""
    invoice = {
        "invoice_number": "INV-REC-07",
        "total_billed_amount": 1735.00,
        "accessorials": [
            {"code": "RECLASS_FEE", "name": "Reclass Upcharge", "amount": 85.00},
        ],
    }

    report = engine.audit_invoice(invoice=invoice, attached_document_types=["BILL_OF_LADING"])
    assert not report.is_clean
    finding = next((f for f in report.findings if f.rule_id == AuditRuleId.RULE_07_RECLASS_DISCREPANCY.value), None)
    assert finding is not None
    assert finding.difference == 85.00
    assert "inspection certificate" in finding.reason.lower()


def test_rule_08_dimension_pallet_discrepancy(engine):
    """Rule 8: Detect pallet count discrepancy (16 invoiced vs 12 verified on POD)."""
    invoice = {
        "invoice_number": "INV-PLT-08",
        "pallet_count": 16,
        "total_billed_amount": 1650.00,
    }
    pod = {
        "piece_count_received": 12,
    }

    report = engine.audit_invoice(invoice=invoice, pod=pod)
    assert not report.is_clean
    finding = next((f for f in report.findings if f.rule_id == AuditRuleId.RULE_08_DIMENSION_PALLET_DISCREPANCY.value), None)
    assert finding is not None
    assert finding.expected_value == 12
    assert finding.billed_value == 16


def test_rule_09_quote_versus_invoice_mismatch(engine):
    """Rule 9: Detect total invoice exceeding agreed rate confirmation quote ($2,097.50 vs $1,897.50 = $200)."""
    invoice = {
        "invoice_number": "INV-QUOTE-09",
        "total_billed_amount": 2097.50,
        "linehaul_amount": 1850.00,
    }
    shipment = {
        "total_charges": 1897.50,
        "canonical_data": {
            "pricing": {"agreed_total": 1897.50}
        }
    }

    report = engine.audit_invoice(invoice=invoice, shipment=shipment)
    assert not report.is_clean
    finding = next((f for f in report.findings if f.rule_id == AuditRuleId.RULE_09_QUOTE_VERSUS_INVOICE_MISMATCH.value), None)
    assert finding is not None
    assert finding.expected_value == 1897.50
    assert finding.billed_value == 2097.50
    assert finding.difference == 200.00


def test_rule_10_arithmetic_total_mismatch(engine):
    """Rule 10: Detect invoice math error ($1,650 linehaul + $250 fuel = $1,900, but stated total is $2,150)."""
    invoice = {
        "invoice_number": "INV-MATH-10",
        "linehaul_amount": 1650.00,
        "fuel_amount": 250.00,
        "total_billed_amount": 2150.00,
    }

    report = engine.audit_invoice(invoice=invoice)
    assert not report.is_clean
    finding = next((f for f in report.findings if f.rule_id == AuditRuleId.RULE_10_ARITHMETIC_TOTAL_MISMATCH.value), None)
    assert finding is not None
    assert finding.expected_value == 1900.00
    assert finding.billed_value == 2150.00
    assert finding.difference == 250.00
