"""Freight document classifier combining filename heuristics, text regex, and structured keywords."""




class DocumentClassifier:
    """Classifies freight documents based on filename, layout, and content keywords."""

    @staticmethod
    def classify(filename: str, text: str = "") -> str:
        lower_fn = filename.lower()
        lower_text = text.lower()
        combined = f"{lower_fn} {lower_text}"

        # 1. Scale Ticket / Certified Reweigh
        scale_keywords = [
            "scale ticket",
            "certified scale",
            "certified weight",
            "gross weight",
            "tare weight",
            "net weight",
            "cat scale",
            "weigh ticket",
            "reweigh certificate",
        ]
        if any(kw in combined for kw in scale_keywords) and ("tare" in lower_text or "gross" in lower_text or "scale" in lower_fn or "scale" in lower_text):
            return "SCALE_TICKET"

        # 2. Freight Invoice
        invoice_keywords = [
            "freight invoice",
            "carrier invoice",
            "invoice #",
            "invoice number",
            "remit to",
            "total due",
            "amount due",
            "balance due",
            "payment terms",
            "net 30",
        ]
        has_explicit_rc = any(
            kw in combined
            for kw in [
                "rate confirmation",
                "ratecon",
                "rate con",
                "load confirmation",
                "broker carrier agreement",
                "carrier rate agreement",
            ]
        )
        if (
            "invoice" in lower_fn
            or "inv" in lower_fn
            or "invoice" in lower_text
            or any(kw in combined for kw in invoice_keywords)
        ) and not has_explicit_rc:
            return "INVOICE"

        # 3. Rate Confirmation / Tender
        rate_con_keywords = [
            "rate confirmation",
            "ratecon",
            "rate con",
            "load confirmation",
            "broker carrier agreement",
            "carrier rate agreement",
            "linehaul rate",
            "fuel surcharge",
            "total agreed",
            "driver rate",
        ]
        if any(kw in combined for kw in rate_con_keywords):
            return "RATE_CONFIRMATION"

        # 4. Proof of Delivery (POD)
        pod_keywords = [
            "proof of delivery",
            "delivery receipt",
            "received in good order",
            "received by signature",
            "delivery date",
            "delivered date",
            "consignee signature",
            "shortage",
            "short/over/damage",
        ]
        if "pod" in lower_fn or any(kw in combined for kw in pod_keywords):
            return "POD"

        # 5. Bill of Lading (BOL)
        bol_keywords = [
            "bill of lading",
            "uniform straight bill of lading",
            "shipper certification",
            "carrier name",
            "trailer number",
            "seal number",
            "nmfc",
            "commodity",
            "pallet count",
            "piece count",
        ]
        if "bol" in lower_fn or "lading" in lower_fn or any(kw in combined for kw in bol_keywords):
            return "BOL"

        # 6. Fallback checks on filename
        if "bol" in lower_fn or "bill_of_lading" in lower_fn:
            return "BOL"
        if "pod" in lower_fn or "delivery" in lower_fn:
            return "POD"
        if "rate" in lower_fn or "con" in lower_fn:
            return "RATE_CONFIRMATION"
        if "inv" in lower_fn:
            return "INVOICE"
        if "scale" in lower_fn or "weight" in lower_fn:
            return "SCALE_TICKET"

        return "UNKNOWN"
