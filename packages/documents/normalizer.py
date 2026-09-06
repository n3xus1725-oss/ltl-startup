"""Document normalizer: maps raw extracted text and tables into typed normalized models."""

import re
from typing import List, Optional

from packages.documents.schemas import (
    NormalizedBOL,
    NormalizedInvoice,
    NormalizedPOD,
    NormalizedRateCon,
    NormalizedScaleTicket,
)


class DocumentNormalizer:
    """Extracts and normalizes domain fields from raw document text."""

    @staticmethod
    def _extract_amount(pattern: str, text: str) -> Optional[float]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            clean = m.group(1).replace("$", "").replace(",", "").strip()
            try:
                return float(clean)
            except ValueError:
                return None
        return None

    @staticmethod
    def _extract_number(pattern: str, text: str) -> Optional[str]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
        return None

    @staticmethod
    def _extract_float(pattern: str, text: str) -> Optional[float]:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            clean = m.group(1).replace(",", "").strip()
            try:
                return float(clean)
            except ValueError:
                return None
        return None

    @staticmethod
    def normalize_bol(text: str, tables: Optional[List[List[str]]] = None) -> NormalizedBOL:
        bol_num = DocumentNormalizer._extract_number(r"(?:BOL|Bill of Lading)[#:\s-]*([A-Z0-9-]{4,30})", text)
        load_id = DocumentNormalizer._extract_number(r"(?:Load|LOAD)[#:\s-]*([A-Z0-9-]{4,20})", text)
        carrier = DocumentNormalizer._extract_number(r"(?:Carrier|Carrier Name)[#:\s-]*([A-Za-z0-9\s.,&-]{3,40})(?:\n|$)", text)
        weight = DocumentNormalizer._extract_float(r"(?:Weight|Total Weight|Gross Weight)[#:\s-]*([0-9,.]+)\s*(?:lbs|lb|pounds)?", text)
        pallets = DocumentNormalizer._extract_float(r"(?:Pallet Count|Pallets|PLT)[#:\s-]*([0-9]+)", text)
        pieces = DocumentNormalizer._extract_float(r"(?:Piece Count|Pieces|PCS)[#:\s-]*([0-9]+)", text)
        trailer = DocumentNormalizer._extract_number(r"(?:Trailer|Trailer #)[#:\s-]*([A-Z0-9-]{3,20})", text)
        seal = DocumentNormalizer._extract_number(r"(?:Seal|Seal #)[#:\s-]*([A-Z0-9-]{3,20})", text)

        return NormalizedBOL(
            bol_number=bol_num,
            load_id=load_id,
            carrier_name=carrier.strip() if carrier else None,
            trailer_number=trailer,
            seal_number=seal,
            total_weight_lbs=weight,
            pallet_count=int(pallets) if pallets is not None else None,
            piece_count=int(pieces) if pieces is not None else None,
            raw_text=text[:2000],
        )

    @staticmethod
    def normalize_pod(text: str) -> NormalizedPOD:
        bol_num = DocumentNormalizer._extract_number(r"(?:BOL|Bill of Lading)[#:\s-]*([A-Z0-9-]{4,30})", text)
        pro_num = DocumentNormalizer._extract_number(r"(?:PRO|PRO #)[#:\s-]*([A-Z0-9-]{4,30})", text)
        sig = DocumentNormalizer._extract_number(r"(?:Received By|Signature|Signed By)[#:\s-]*([A-Za-z\s.]{3,35})(?:\n|$)", text)
        date_str = DocumentNormalizer._extract_number(r"(?:Delivery Date|Delivered Date|Date)[#:\s-]*(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})", text)
        time_str = DocumentNormalizer._extract_number(r"(?:Time|Delivery Time)[#:\s-]*(\d{1,2}:\d{2}(?:\s*[AaPp][Mm])?)", text)
        has_damage_kw = bool(re.search(r"\b(?:damage|damaged|crushed|broken|leak)\b", text, re.IGNORECASE))
        no_damage = bool(re.search(r"\b(?:no damage|damage[:\s]*none|zero damage|without damage)\b", text, re.IGNORECASE))
        damage = has_damage_kw and not no_damage

        has_shortage_kw = bool(re.search(r"\b(?:shortage|short|missing pieces)\b", text, re.IGNORECASE))
        no_shortage = bool(re.search(r"\b(?:no shortage|shortage[:\s]*none|zero shortage|without shortage)\b", text, re.IGNORECASE))
        shortage = has_shortage_kw and not no_shortage

        return NormalizedPOD(
            bol_number=bol_num,
            pro_number=pro_num,
            delivery_date=date_str,
            delivery_time=time_str,
            received_by_signature=sig.strip() if sig else None,
            damage_noted=damage,
            shortage_noted=shortage,
            raw_text=text[:2000],
        )

    @staticmethod
    def normalize_rate_con(text: str) -> NormalizedRateCon:
        load_id = DocumentNormalizer._extract_number(r"(?:Load|Load ID|Order #|Reference #)[#:\s-]*([A-Z0-9-]{4,20})", text)
        carrier = DocumentNormalizer._extract_number(r"(?:\bCarrier(?:\s*Name)?)[#:\s-]+([A-Za-z0-9\s.,&-]{3,40})(?:\r?\n|$)", text)
        total = DocumentNormalizer._extract_amount(r"(?:Total Rate|Total Agreed|Agreed Amount|Total Charges|Total Pay)[#:\s-]*\$?([0-9,.]+)", text)
        linehaul = DocumentNormalizer._extract_amount(r"(?:Linehaul(?:\s*Rate)?|Base Rate|Freight Charge)[#:\s-]*\$?([0-9,.]+)", text)
        fuel = DocumentNormalizer._extract_amount(r"(?:Fuel|Fuel Surcharge|FSC)[#:\s-]*\$?([0-9,.]+)", text)

        return NormalizedRateCon(
            load_id=load_id,
            carrier_name=carrier.strip() if carrier else None,
            linehaul_rate=linehaul,
            fuel_surcharge=fuel,
            total_agreed_rate=total or (linehaul if linehaul else None),
            raw_text=text[:2000],
        )

    @staticmethod
    def normalize_invoice(text: str) -> NormalizedInvoice:
        inv_num = DocumentNormalizer._extract_number(r"(?:Invoice\s*#|Invoice\s*Number|Invoice\s*No\.?|Inv\s*#)[ \t]*[:#-]*[ \t]*([A-Z0-9-]{3,30})", text)
        if not inv_num:
            inv_num = DocumentNormalizer._extract_number(r"(?:^|\n)[ \t]*Invoice[ \t]*[:#-]+[ \t]*([A-Z0-9-]{3,30})", text)
        carrier_name = DocumentNormalizer._extract_number(r"(?:Carrier|Carrier Name|From|Bill From)[ \t]*[:#-]+[ \t]*([A-Za-z0-9\s.,&-]{3,40})(?:\r?\n|$)", text)
        load_id = DocumentNormalizer._extract_number(r"(?:Load\s*#|Load\s*ID|Load|PO\s*#|Order\s*#)[ \t]*[:#-]*[ \t]*([A-Z0-9-]{4,20})", text)
        bol_num = DocumentNormalizer._extract_number(r"(?:BOL\s*#|BOL|Bill of Lading)[ \t]*[:#-]*[ \t]*([A-Z0-9-]{4,30})", text)
        carrier_ref = DocumentNormalizer._extract_number(r"(?:PRO\s*#|PRO|Carrier Ref)[ \t]*[:#-]*[ \t]*([A-Z0-9-]{4,30})", text)
        total = DocumentNormalizer._extract_amount(r"(?:Total Due|Total Amount|Invoice Total|Amount Due|Balance Due|Total)[ \t]*[:#-]*[ \t]*\$?([0-9,.]+)", text)
        linehaul = DocumentNormalizer._extract_amount(r"(?:Linehaul(?:\s*Rate|\s*Amount)?|Freight Charge|Base Rate)[ \t]*[:#-]*[ \t]*\$?([0-9,.]+)", text)
        fuel = DocumentNormalizer._extract_amount(r"(?:Fuel|Fuel Surcharge|FSC)[ \t]*[:#-]*[ \t]*\$?([0-9,.]+)", text)
        weight = DocumentNormalizer._extract_float(r"(?:Billed Weight|Weight|Total Weight|Actual Weight)[#:\s-]*([0-9,.]+)\s*(?:lbs|lb)?", text)
        f_class = DocumentNormalizer._extract_number(r"(?:Class|Freight Class|NMFC Class)[#:\s-]*([0-9.]{2,6})", text)
        pallets = DocumentNormalizer._extract_float(r"(?:Pallet Count|Pallets|PLT)[#:\s-]*([0-9]+)", text)

        # Detect itemized accessorials
        accessorials = []
        acc_patterns = [
            ("DETENTION", "Detention Charge", r"(?:Detention(?:\s*Charge)?|Wait\s*Time)[ \t]*[:#-]*[ \t]*\$?([0-9,.]+)"),
            ("LUMPER", "Lumper Fee", r"(?:Lumper(?:\s*Fee)?|Unloading(?:\s*Fee)?)[ \t]*[:#-]*[ \t]*\$?([0-9,.]+)"),
            ("LIFTGATE", "Liftgate Service", r"(?:Liftgate(?:\s*Service|\s*Fee)?|Lift\s*Gate)[ \t]*[:#-]*[ \t]*\$?([0-9,.]+)"),
            ("RESIDENTIAL", "Residential Delivery", r"(?:Residential(?:\s*Delivery|\s*Pickup|\s*Fee)?)[ \t]*[:#-]*[ \t]*\$?([0-9,.]+)"),
            ("LAYOVER", "Layover Fee", r"(?:Layover(?:\s*Fee)?)[ \t]*[:#-]*[ \t]*\$?([0-9,.]+)"),
            ("INSIDE_DELIVERY", "Inside Delivery", r"(?:Inside\s*Delivery(?:\s*Fee)?)[ \t]*[:#-]*[ \t]*\$?([0-9,.]+)"),
            ("RECLASS_FEE", "Reclass / Inspection Fee", r"(?:Reclass(?:\s*Fee)?|Inspection\s*Fee|Reweigh(?:\s*Fee)?)[ \t]*[:#-]*[ \t]*\$?([0-9,.]+)"),
        ]
        for code, name, pat in acc_patterns:
            amt = DocumentNormalizer._extract_amount(pat, text)
            if amt and amt > 0:
                accessorials.append({"code": code, "name": name, "amount": amt})

        acc_total = sum(a["amount"] for a in accessorials) if accessorials else 0.0

        # Build itemized lines
        line_items = []
        if linehaul:
            line_items.append({"code": "LINEHAUL", "description": "Freight Linehaul", "amount": linehaul})
        if fuel:
            line_items.append({"code": "FUEL", "description": "Fuel Surcharge", "amount": fuel})
        for a in accessorials:
            line_items.append({"code": a["code"], "description": a["name"], "amount": a["amount"]})

        return NormalizedInvoice(
            invoice_number=inv_num,
            carrier_name=carrier_name.strip() if carrier_name else None,
            load_id=load_id,
            bol_number=bol_num,
            carrier_reference=carrier_ref,
            total_billed_amount=total or (linehaul if linehaul else None),
            linehaul_amount=linehaul or (total if total else None),
            fuel_amount=fuel,
            accessorial_amount=acc_total,
            weight_lbs=weight,
            freight_class=f_class,
            pallet_count=int(pallets) if pallets else None,
            line_items=line_items,
            accessorials=accessorials,
            raw_text=text[:2000],
        )

    @staticmethod
    def normalize_scale_ticket(text: str) -> NormalizedScaleTicket:
        t_num = DocumentNormalizer._extract_number(r"(?:\bScale\s+Ticket|\bTicket)\s*(?:#|Number|No\.?)?[ \t]*[:#-]+[ \t]*([A-Z0-9-]{3,30})", text)
        scale = DocumentNormalizer._extract_number(r"(?:\bScale\s+Name|\bScale\s+Location|\bScale|\bLocation)[ \t]*[:#-]+[ \t]*([A-Za-z0-9\s.,&#-]{3,40})(?:\r?\n|$)", text)
        gross = DocumentNormalizer._extract_float(r"(?:Gross|Gross Weight|Gross Wt)[#:\s-]*([0-9,.]+)\s*(?:lbs|lb)?", text)
        tare = DocumentNormalizer._extract_float(r"(?:Tare|Tare Weight|Tare Wt)[#:\s-]*([0-9,.]+)\s*(?:lbs|lb)?", text)
        net = DocumentNormalizer._extract_float(r"(?:Net|Net Weight|Net Wt)[#:\s-]*([0-9,.]+)\s*(?:lbs|lb)?", text)

        calc_net = net
        if calc_net is None and gross is not None and tare is not None:
            calc_net = gross - tare

        return NormalizedScaleTicket(
            ticket_number=t_num,
            scale_name=scale.strip() if scale else None,
            gross_weight_lbs=gross,
            tare_weight_lbs=tare,
            net_weight_lbs=calc_net,
            raw_text=text[:2000],
        )
