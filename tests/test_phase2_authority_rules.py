"""Phase 2.3 Test Suite — Source Authority Rules Engine."""


from packages.domain.provenance import FieldProvenanceRecord
from packages.rules.authority import (
    AssertionDecision,
    SourceAuthorityEngine,
)


def test_authority_pricing_precedence():
    """Verify Pricing: Rate Con (100) > Invoice (70) > Email Quote (40)."""
    engine = SourceAuthorityEngine()

    rate_con_rec = FieldProvenanceRecord(
        field="pricing.agreed_total",
        value=1850.0,
        source="rate_confirmation",
        source_id="rc-101",
        authority=100,
    )
    invoice_rec = FieldProvenanceRecord(
        field="pricing.agreed_total",
        value=2100.0,
        source="invoice",
        source_id="inv-202",
        authority=70,
    )
    email_rec = FieldProvenanceRecord(
        field="pricing.agreed_total",
        value=1900.0,
        source="carrier_email",
        source_id="msg-303",
        authority=40,
    )

    # 1. Starting with email, then Rate Con arrives -> Accept canonical
    dec1 = engine.evaluate(email_rec, rate_con_rec)
    assert dec1 == AssertionDecision.ACCEPT_CANONICAL

    # 2. Starting with Rate Con, then Invoice arrives with different amount -> Reject lower authority
    dec2 = engine.evaluate(rate_con_rec, invoice_rec)
    assert dec2 == AssertionDecision.REJECT_LOWER_AUTHORITY


def test_authority_weight_precedence():
    """Verify Weight: Scale Ticket (100) > BOL (80) > Rate Con (60) > Email (40)."""
    engine = SourceAuthorityEngine()

    email_rec = FieldProvenanceRecord(
        field="freight_details.total_weight_lbs",
        value=14000.0,
        source="carrier_email",
        source_id="msg-1",
        authority=40,
    )
    bol_rec = FieldProvenanceRecord(
        field="freight_details.total_weight_lbs",
        value=14500.0,
        source="bol",
        source_id="doc-bol",
        authority=80,
    )
    scale_rec = FieldProvenanceRecord(
        field="freight_details.total_weight_lbs",
        value=14720.0,
        source="scale_ticket",
        source_id="doc-scale",
        authority=100,
    )

    # BOL supersedes email
    assert engine.evaluate(email_rec, bol_rec) == AssertionDecision.ACCEPT_CANONICAL
    # Certified reweigh scale ticket supersedes BOL
    assert engine.evaluate(bol_rec, scale_rec) == AssertionDecision.ACCEPT_CANONICAL
    # BOL cannot overwrite certified scale ticket
    assert engine.evaluate(scale_rec, bol_rec) == AssertionDecision.REJECT_LOWER_AUTHORITY


def test_authority_corroboration():
    """Verify same value from another source corroborates instead of conflicting."""
    engine = SourceAuthorityEngine()

    bol_rec = FieldProvenanceRecord(
        field="freight_details.total_weight_lbs",
        value=14500.0,
        source="bol",
        source_id="doc-bol",
        authority=80,
    )
    edi_rec = FieldProvenanceRecord(
        field="freight_details.total_weight_lbs",
        value=14500.0,
        source="carrier_edi",
        source_id="edi-01",
        authority=80,
    )

    decision = engine.evaluate(bol_rec, edi_rec)
    assert decision == AssertionDecision.CORROBORATE


def test_authority_equal_authority_conflict():
    """Verify conflicting values with equal authority trigger FLAG_CONFLICT."""
    engine = SourceAuthorityEngine()

    bol_1 = FieldProvenanceRecord(
        field="carrier.trailer_number",
        value="TR-111",
        source="bol",
        source_id="doc-bol-1",
        authority=90,
    )
    bol_2 = FieldProvenanceRecord(
        field="carrier.trailer_number",
        value="TR-999",
        source="bol",
        source_id="doc-bol-2",
        authority=90,
    )

    decision = engine.evaluate(bol_1, bol_2)
    assert decision == AssertionDecision.FLAG_CONFLICT


def test_customer_authority_overrides():
    """Verify organization-specific overrides can alter default precedence."""
    # Organization override: customer wants manual_entry to have 100 authority for pricing
    custom_overrides = {
        "pricing.agreed_total": {
            "manual_entry": 100,
            "rate_confirmation": 90,
        }
    }
    custom_engine = SourceAuthorityEngine(organization_overrides=custom_overrides)

    rc_rec = FieldProvenanceRecord(
        field="pricing.agreed_total",
        value=1850.0,
        source="rate_confirmation",
        source_id="rc-1",
        authority=90,
    )
    manual_rec = FieldProvenanceRecord(
        field="pricing.agreed_total",
        value=1700.0,
        source="manual_entry",
        source_id="usr-1",
        authority=100,
    )

    assert custom_engine.evaluate(rc_rec, manual_rec) == AssertionDecision.ACCEPT_CANONICAL
