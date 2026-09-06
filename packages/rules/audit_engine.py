"""Phase 3.3 — Deterministic Billing Audit Engine.

Per Non-Negotiable Rule 3 & 22:
- Business rules stay in deterministic code.
- Financial arithmetic, thresholds, variances, and totals are computed strictly in code.
- Every rule returns structured, explainable evidence.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from packages.rules.audit_schemas import (
    AuditFinding,
    AuditReport,
    AuditRuleId,
    AuditSeverity,
)


class DeterministicAuditEngine:
    """Evaluates carrier invoices against contracts, rate confirmations, scale tickets, and BOLs."""

    def __init__(self, tolerance_dollars: float = 0.01):
        self.tolerance = tolerance_dollars

    def audit_invoice(
        self,
        invoice: Dict[str, Any],
        shipment: Optional[Dict[str, Any]] = None,
        contract: Optional[Dict[str, Any]] = None,
        scale_ticket: Optional[Dict[str, Any]] = None,
        bol: Optional[Dict[str, Any]] = None,
        pod: Optional[Dict[str, Any]] = None,
        existing_invoices: Optional[List[Dict[str, Any]]] = None,
        attached_document_types: Optional[List[str]] = None,
    ) -> AuditReport:
        """Run all 10 deterministic audit rules against the invoice and related evidence."""
        findings: List[AuditFinding] = []
        doc_types = [dt.upper() for dt in (attached_document_types or [])]

        # 1. Duplicate invoice check
        f1 = self._check_duplicate_invoice(invoice, existing_invoices)
        if f1:
            findings.append(f1)

        # 2. Linehaul mismatch
        f2 = self._check_linehaul_mismatch(invoice, shipment, contract)
        if f2:
            findings.append(f2)

        # 3. Fuel surcharge mismatch
        f3 = self._check_fuel_mismatch(invoice, shipment, contract)
        if f3:
            findings.append(f3)

        # 4. Unsupported or unverified accessorials
        f4_list = self._check_unsupported_accessorials(invoice, contract, bol, pod, doc_types)
        findings.extend(f4_list)

        # 5. Weight mismatch
        f5 = self._check_weight_mismatch(invoice, scale_ticket, bol, contract)
        if f5:
            findings.append(f5)

        # 6. Class mismatch
        f6 = self._check_class_mismatch(invoice, bol, shipment)
        if f6:
            findings.append(f6)

        # 7. Reclassification discrepancy
        f7 = self._check_reclass_discrepancy(invoice, doc_types)
        if f7:
            findings.append(f7)

        # 8. Pallet count / dimension discrepancy
        f8 = self._check_pallet_discrepancy(invoice, bol, pod)
        if f8:
            findings.append(f8)

        # 9. Quote versus invoice mismatch
        f9 = self._check_quote_versus_invoice_mismatch(invoice, shipment)
        if f9:
            findings.append(f9)

        # 10. Arithmetic total mismatch
        f10 = self._check_arithmetic_total_mismatch(invoice)
        if f10:
            findings.append(f10)

        # Compute total monetary discrepancy
        total_discrepancy = sum(f.difference for f in findings if f.difference > 0)
        is_clean = len(findings) == 0

        return AuditReport(
            invoice_id=str(invoice.get("id") or invoice.get("invoice_number") or ""),
            shipment_id=str(shipment.get("id") or "") if shipment else None,
            is_clean=is_clean,
            total_discrepancy_amount=round(total_discrepancy, 2),
            findings=findings,
            audited_at=datetime.now(timezone.utc).isoformat(),
        )

    # -------------------------------------------------------------------------
    # Rule 1: Duplicate Invoice
    # -------------------------------------------------------------------------
    def _check_duplicate_invoice(
        self,
        invoice: Dict[str, Any],
        existing_invoices: Optional[List[Dict[str, Any]]],
    ) -> Optional[AuditFinding]:
        if not existing_invoices:
            return None

        current_num = str(invoice.get("invoice_number", "")).strip().upper()
        current_carrier = str(invoice.get("carrier_name", "")).strip().lower()
        current_id = str(invoice.get("id", ""))

        for ex in existing_invoices:
            ex_id = str(ex.get("id", ""))
            if ex_id and ex_id == current_id:
                continue
            ex_num = str(ex.get("invoice_number", "")).strip().upper()
            ex_carrier = str(ex.get("carrier_name", "")).strip().lower()

            if current_num and ex_num == current_num and (not current_carrier or not ex_carrier or current_carrier in ex_carrier or ex_carrier in current_carrier):
                billed = float(invoice.get("total_billed_amount", 0.0) or 0.0)
                return AuditFinding(
                    rule_id=AuditRuleId.RULE_01_DUPLICATE_INVOICE.value,
                    rule_name="Duplicate Invoice Detected",
                    severity=AuditSeverity.CRITICAL,
                    expected_value=None,
                    billed_value=current_num,
                    difference=billed,
                    reason=f"Invoice #{current_num} from carrier '{invoice.get('carrier_name')}' was previously ingested/processed in invoice {ex_id or ex_num}.",
                    source_documents=[str(invoice.get("document_id") or "current_invoice"), str(ex.get("document_id") or "prior_invoice")],
                    evidence_references=[{"prior_invoice_id": ex_id, "prior_invoice_status": ex.get("status")}],
                    recommended_action="reject_duplicate_invoice",
                )
        return None

    # -------------------------------------------------------------------------
    # Rule 2: Linehaul Mismatch
    # -------------------------------------------------------------------------
    def _check_linehaul_mismatch(
        self,
        invoice: Dict[str, Any],
        shipment: Optional[Dict[str, Any]],
        contract: Optional[Dict[str, Any]],
    ) -> Optional[AuditFinding]:
        billed_linehaul = float(invoice.get("linehaul_amount") or 0.0)
        if billed_linehaul <= 0:
            return None

        expected_linehaul: Optional[float] = None
        source_doc = "rate_confirmation"

        # Check shipment canonical agreed linehaul first
        if shipment:
            canonical = shipment.get("canonical_data") or shipment
            pricing = canonical.get("pricing") or {}
            if "agreed_linehaul" in pricing and pricing["agreed_linehaul"]:
                expected_linehaul = float(pricing["agreed_linehaul"])
            elif "agreed_total" in pricing and pricing["agreed_total"] and not pricing.get("fuel_surcharge"):
                expected_linehaul = float(pricing["agreed_total"])

        # Fallback to contract base rate
        if expected_linehaul is None and contract:
            expected_linehaul = float(contract.get("base_rate") or 0.0)
            source_doc = f"contract_{contract.get('contract_number', 'std')}"

        if expected_linehaul is not None and billed_linehaul > (expected_linehaul + self.tolerance):
            diff = round(billed_linehaul - expected_linehaul, 2)
            sev = AuditSeverity.CRITICAL if diff >= 100.0 or (diff / expected_linehaul) >= 0.10 else AuditSeverity.HIGH
            return AuditFinding(
                rule_id=AuditRuleId.RULE_02_LINEHAUL_MISMATCH.value,
                rule_name="Linehaul Rate Overcharge",
                severity=sev,
                expected_value=expected_linehaul,
                billed_value=billed_linehaul,
                difference=diff,
                reason=f"Billed linehaul (${billed_linehaul:.2f}) exceeds agreed linehaul rate (${expected_linehaul:.2f}) by ${diff:.2f}.",
                source_documents=[source_doc],
                evidence_references=[{"expected_linehaul": expected_linehaul, "billed_linehaul": billed_linehaul}],
                recommended_action="dispute_linehaul_overcharge",
            )
        return None

    # -------------------------------------------------------------------------
    # Rule 3: Fuel Surcharge Mismatch
    # -------------------------------------------------------------------------
    def _check_fuel_mismatch(
        self,
        invoice: Dict[str, Any],
        shipment: Optional[Dict[str, Any]],
        contract: Optional[Dict[str, Any]],
    ) -> Optional[AuditFinding]:
        billed_fuel = float(invoice.get("fuel_amount") or 0.0)
        if billed_fuel <= 0:
            return None

        expected_fuel: Optional[float] = None
        source_doc = "rate_confirmation"

        if shipment:
            canonical = shipment.get("canonical_data") or shipment
            pricing = canonical.get("pricing") or {}
            if "fuel_surcharge" in pricing and pricing["fuel_surcharge"] is not None:
                expected_fuel = float(pricing["fuel_surcharge"])

        if expected_fuel is None and contract and contract.get("fuel_schedule"):
            f_sched = contract.get("fuel_schedule")
            base_rate = float(contract.get("base_rate") or invoice.get("linehaul_amount") or 0.0)
            if isinstance(f_sched, dict):
                percent = float(f_sched.get("base_rate_percent") or f_sched.get("percent") or 0.0)
                if percent > 0 and base_rate > 0:
                    expected_fuel = round(base_rate * (percent / 100.0), 2)
                    source_doc = f"contract_fuel_formula_{percent}%"

        if expected_fuel is not None and billed_fuel > (expected_fuel + 1.00):
            diff = round(billed_fuel - expected_fuel, 2)
            sev = AuditSeverity.HIGH if diff >= 50.0 else AuditSeverity.MEDIUM
            return AuditFinding(
                rule_id=AuditRuleId.RULE_03_FUEL_MISMATCH.value,
                rule_name="Fuel Surcharge Mismatch",
                severity=sev,
                expected_value=expected_fuel,
                billed_value=billed_fuel,
                difference=diff,
                reason=f"Billed fuel surcharge (${billed_fuel:.2f}) exceeds contracted/agreed surcharge (${expected_fuel:.2f}) by ${diff:.2f}.",
                source_documents=[source_doc],
                evidence_references=[{"expected_fuel": expected_fuel, "billed_fuel": billed_fuel}],
                recommended_action="recalculate_fuel_surcharge",
            )
        return None

    # -------------------------------------------------------------------------
    # Rule 4: Unsupported / Unverified Accessorial
    # -------------------------------------------------------------------------
    def _check_unsupported_accessorials(
        self,
        invoice: Dict[str, Any],
        contract: Optional[Dict[str, Any]],
        bol: Optional[Dict[str, Any]],
        pod: Optional[Dict[str, Any]],
        attached_doc_types: List[str],
    ) -> List[AuditFinding]:
        findings = []
        accessorials = invoice.get("accessorials") or []

        # If not explicit accessorials list, check line_items
        if not accessorials and invoice.get("line_items"):
            for item in invoice["line_items"]:
                code = item.get("code", "").upper()
                if code not in ("LINEHAUL", "FUEL", "BASE_RATE"):
                    accessorials.append({"code": code, "name": item.get("description", code), "amount": item.get("amount", 0.0)})

        contract_acc = (contract.get("accessorial_schedule") if contract else {}) or {}

        for acc in accessorials:
            code = acc.get("code", "").upper()
            amt = float(acc.get("amount") or 0.0)
            name = acc.get("name") or code
            if amt <= 0:
                continue

            # Check if accessorial is allowed in contract
            if contract and contract_acc and code not in contract_acc:
                findings.append(
                    AuditFinding(
                        rule_id=AuditRuleId.RULE_04_UNSUPPORTED_ACCESSORIAL.value,
                        rule_name=f"Unsupported Accessorial Charge: {name}",
                        severity=AuditSeverity.HIGH,
                        expected_value=0.0,
                        billed_value=amt,
                        difference=amt,
                        reason=f"Accessorial charge '{name}' (${amt:.2f}) is not approved in carrier contract.",
                        source_documents=["carrier_contract"],
                        evidence_references=[{"code": code, "billed_amount": amt}],
                        recommended_action="dispute_unauthorized_accessorial",
                    )
                )
                continue

            # Check proof requirements
            if code == "LUMPER" and "RECEIPT" not in attached_doc_types and "LUMPER_RECEIPT" not in attached_doc_types:
                findings.append(
                    AuditFinding(
                        rule_id=AuditRuleId.RULE_04_UNSUPPORTED_ACCESSORIAL.value,
                        rule_name="Lumper Fee Missing Receipt Proof",
                        severity=AuditSeverity.HIGH,
                        expected_value=0.0,
                        billed_value=amt,
                        difference=amt,
                        reason=f"Lumper charge of ${amt:.2f} requires attached payment receipt or signed lumper ticket, which is missing.",
                        source_documents=["carrier_invoice"],
                        evidence_references=[{"code": code, "billed_amount": amt, "missing_document": "LUMPER_RECEIPT"}],
                        recommended_action="request_lumper_receipt_or_dispute",
                    )
                )

            elif code == "DETENTION":
                # Check for timestamp evidence on BOL or POD
                has_times = False
                if pod and (pod.get("delivery_time") or pod.get("delivery_appointment")):
                    has_times = True
                if bol and bol.get("pickup_date"):
                    has_times = True

                if not has_times and "SCALE_TICKET" not in attached_doc_types:
                    findings.append(
                        AuditFinding(
                            rule_id=AuditRuleId.RULE_04_UNSUPPORTED_ACCESSORIAL.value,
                            rule_name="Detention Fee Lacks Timestamp Evidence",
                            severity=AuditSeverity.HIGH,
                            expected_value=0.0,
                            billed_value=amt,
                            difference=amt,
                            reason=f"Detention charge of ${amt:.2f} lacks verifiable driver arrival/departure timestamps on signed BOL/POD.",
                            source_documents=["pod", "bol"],
                            evidence_references=[{"code": code, "billed_amount": amt}],
                            recommended_action="request_detention_timesheet",
                        )
                    )

        return findings

    # -------------------------------------------------------------------------
    # Rule 5: Weight Mismatch
    # -------------------------------------------------------------------------
    def _check_weight_mismatch(
        self,
        invoice: Dict[str, Any],
        scale_ticket: Optional[Dict[str, Any]],
        bol: Optional[Dict[str, Any]],
        contract: Optional[Dict[str, Any]],
    ) -> Optional[AuditFinding]:
        billed_weight = float(invoice.get("weight_lbs") or 0.0)
        if billed_weight <= 0:
            return None

        verified_weight: Optional[float] = None
        source_doc = "scale_ticket"

        if scale_ticket and scale_ticket.get("net_weight_lbs"):
            verified_weight = float(scale_ticket["net_weight_lbs"])
            source_doc = "scale_ticket"
        elif bol and bol.get("total_weight_lbs"):
            verified_weight = float(bol["total_weight_lbs"])
            source_doc = "bill_of_lading"

        if verified_weight and billed_weight > (verified_weight + 100.0):
            diff_lbs = round(billed_weight - verified_weight, 1)
            # If contract is per_cwt, calculate financial difference
            cwt_rate = float(contract.get("base_rate") or 0.0) if (contract and contract.get("rate_type") == "per_cwt") else 0.0
            monetary_diff = round((diff_lbs / 100.0) * cwt_rate, 2) if cwt_rate > 0 else 0.0

            sev = AuditSeverity.HIGH if diff_lbs >= 500.0 else AuditSeverity.MEDIUM
            return AuditFinding(
                rule_id=AuditRuleId.RULE_05_WEIGHT_MISMATCH.value,
                rule_name="Invoiced Weight Discrepancy",
                severity=sev,
                expected_value=verified_weight,
                billed_value=billed_weight,
                difference=monetary_diff,
                reason=f"Invoiced weight ({billed_weight:,.0f} lbs) exceeds verified {source_doc} weight ({verified_weight:,.0f} lbs) by {diff_lbs:,.0f} lbs.",
                source_documents=[source_doc],
                evidence_references=[{"verified_weight_lbs": verified_weight, "billed_weight_lbs": billed_weight, "weight_variance_lbs": diff_lbs}],
                recommended_action="dispute_reweigh_variance",
            )
        return None

    # -------------------------------------------------------------------------
    # Rule 6: Class Mismatch
    # -------------------------------------------------------------------------
    def _check_class_mismatch(
        self,
        invoice: Dict[str, Any],
        bol: Optional[Dict[str, Any]],
        shipment: Optional[Dict[str, Any]],
    ) -> Optional[AuditFinding]:
        billed_class = invoice.get("freight_class")
        if not billed_class:
            return None

        agreed_class = None
        source_doc = "bill_of_lading"

        if bol and bol.get("nmfc_class"):
            agreed_class = str(bol["nmfc_class"]).strip()
        elif shipment:
            canonical = shipment.get("canonical_data") or shipment
            freight = canonical.get("freight_details") or {}
            if freight.get("freight_class"):
                agreed_class = str(freight["freight_class"]).strip()
                source_doc = "canonical_shipment"

        if agreed_class and str(billed_class).strip() != agreed_class:
            return AuditFinding(
                rule_id=AuditRuleId.RULE_06_CLASS_MISMATCH.value,
                rule_name="Freight Class Mismatch",
                severity=AuditSeverity.HIGH,
                expected_value=agreed_class,
                billed_value=billed_class,
                difference=0.0,
                reason=f"Billed freight class '{billed_class}' does not match agreed class '{agreed_class}' on {source_doc}.",
                source_documents=[source_doc],
                evidence_references=[{"agreed_class": agreed_class, "billed_class": billed_class}],
                recommended_action="verify_nmfc_classification",
            )
        return None

    # -------------------------------------------------------------------------
    # Rule 7: Reclass Discrepancy
    # -------------------------------------------------------------------------
    def _check_reclass_discrepancy(
        self,
        invoice: Dict[str, Any],
        attached_doc_types: List[str],
    ) -> Optional[AuditFinding]:
        # Check if invoice contains a RECLASS_FEE or inspection charge
        accessorials = invoice.get("accessorials") or []
        reclass_fee = 0.0
        for acc in accessorials:
            if "RECLASS" in acc.get("code", "").upper() or "INSPECTION" in acc.get("code", "").upper():
                reclass_fee += float(acc.get("amount") or 0.0)

        if reclass_fee > 0 and "INSPECTION_CERTIFICATE" not in attached_doc_types and "WEIGHT_INSPECTION" not in attached_doc_types:
            return AuditFinding(
                rule_id=AuditRuleId.RULE_07_RECLASS_DISCREPANCY.value,
                rule_name="Reclassification Fee Lacks Certified Inspection Proof",
                severity=AuditSeverity.HIGH,
                expected_value=0.0,
                billed_value=reclass_fee,
                difference=reclass_fee,
                reason=f"Carrier assessed ${reclass_fee:.2f} reclassification/inspection charge without providing required certified inspection certificate.",
                source_documents=["carrier_invoice"],
                evidence_references=[{"reclass_fee": reclass_fee, "missing_evidence": "INSPECTION_CERTIFICATE"}],
                recommended_action="dispute_unsupported_reclassification",
            )
        return None

    # -------------------------------------------------------------------------
    # Rule 8: Dimension / Pallet Discrepancy
    # -------------------------------------------------------------------------
    def _check_pallet_discrepancy(
        self,
        invoice: Dict[str, Any],
        bol: Optional[Dict[str, Any]],
        pod: Optional[Dict[str, Any]],
    ) -> Optional[AuditFinding]:
        billed_pallets = invoice.get("pallet_count")
        if not billed_pallets or int(billed_pallets) <= 0:
            return None

        verified_pallets: Optional[int] = None
        source_doc = "bill_of_lading"

        if pod and pod.get("piece_count_received"):
            verified_pallets = int(pod["piece_count_received"])
            source_doc = "proof_of_delivery"
        elif bol and bol.get("pallet_count"):
            verified_pallets = int(bol["pallet_count"])
            source_doc = "bill_of_lading"

        if verified_pallets and int(billed_pallets) > verified_pallets:
            diff = int(billed_pallets) - verified_pallets
            return AuditFinding(
                rule_id=AuditRuleId.RULE_08_DIMENSION_PALLET_DISCREPANCY.value,
                rule_name="Pallet Count Discrepancy",
                severity=AuditSeverity.MEDIUM,
                expected_value=verified_pallets,
                billed_value=int(billed_pallets),
                difference=0.0,
                reason=f"Invoiced pallet count ({billed_pallets}) exceeds verified {source_doc} count ({verified_pallets}) by {diff} pallets.",
                source_documents=[source_doc],
                evidence_references=[{"verified_pallets": verified_pallets, "billed_pallets": int(billed_pallets), "variance": diff}],
                recommended_action="verify_handling_unit_count",
            )
        return None

    # -------------------------------------------------------------------------
    # Rule 9: Quote-versus-Invoice Mismatch
    # -------------------------------------------------------------------------
    def _check_quote_versus_invoice_mismatch(
        self,
        invoice: Dict[str, Any],
        shipment: Optional[Dict[str, Any]],
    ) -> Optional[AuditFinding]:
        billed_total = float(invoice.get("total_billed_amount") or 0.0)
        if billed_total <= 0 or not shipment:
            return None

        canonical = shipment.get("canonical_data") or shipment
        pricing = canonical.get("pricing") or {}
        agreed_total = float(pricing.get("agreed_total") or pricing.get("total_agreed_rate") or shipment.get("total_charges") or 0.0)

        if agreed_total > 0 and billed_total > (agreed_total + self.tolerance):
            diff = round(billed_total - agreed_total, 2)
            sev = AuditSeverity.CRITICAL if diff >= 100.0 or (diff / agreed_total) >= 0.10 else AuditSeverity.HIGH
            return AuditFinding(
                rule_id=AuditRuleId.RULE_09_QUOTE_VERSUS_INVOICE_MISMATCH.value,
                rule_name="Quote Versus Invoice Total Mismatch",
                severity=sev,
                expected_value=agreed_total,
                billed_value=billed_total,
                difference=diff,
                reason=f"Total billed invoice (${billed_total:.2f}) exceeds agreed Rate Confirmation total (${agreed_total:.2f}) by ${diff:.2f}.",
                source_documents=["rate_confirmation"],
                evidence_references=[{"agreed_total": agreed_total, "billed_total": billed_total}],
                recommended_action="create_invoice_dispute",
            )
        return None

    # -------------------------------------------------------------------------
    # Rule 10: Arithmetic / Total Mismatch
    # -------------------------------------------------------------------------
    def _check_arithmetic_total_mismatch(
        self,
        invoice: Dict[str, Any],
    ) -> Optional[AuditFinding]:
        total_billed = float(invoice.get("total_billed_amount") or 0.0)
        if total_billed <= 0:
            return None

        # Calculate sum of line items or components
        line_items = invoice.get("line_items") or []
        if line_items:
            sum_components = round(sum(float(item.get("amount") or 0.0) for item in line_items), 2)
        else:
            lh = float(invoice.get("linehaul_amount") or 0.0)
            fsc = float(invoice.get("fuel_amount") or 0.0)
            acc = float(invoice.get("accessorial_amount") or 0.0)
            sum_components = round(lh + fsc + acc, 2)

        if sum_components > 0 and abs(sum_components - total_billed) > 0.05:
            diff = round(abs(total_billed - sum_components), 2)
            return AuditFinding(
                rule_id=AuditRuleId.RULE_10_ARITHMETIC_TOTAL_MISMATCH.value,
                rule_name="Invoice Arithmetic Total Mismatch",
                severity=AuditSeverity.CRITICAL,
                expected_value=sum_components,
                billed_value=total_billed,
                difference=diff,
                reason=f"Invoice arithmetic discrepancy: sum of line charges (${sum_components:.2f}) does not equal stated total billed amount (${total_billed:.2f}) (difference: ${diff:.2f}).",
                source_documents=["carrier_invoice"],
                evidence_references=[{"sum_of_lines": sum_components, "stated_total": total_billed, "arithmetic_variance": diff}],
                recommended_action="request_corrected_invoice",
            )
        return None
