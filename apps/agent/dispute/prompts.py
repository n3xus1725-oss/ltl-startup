"""LLM prompt definitions for the Dispute Agent.

CRITICAL: The LLM is ONLY responsible for drafting professional language.
All financial amounts ($disputed, $expected, $billed) are injected by deterministic code.
The LLM must NEVER be asked to calculate, modify, or verify financial amounts.
"""

DISPUTE_PROMPT_VERSION = "dispute-v1.0"

DISPUTE_SYSTEM_PROMPT = """You are a professional freight billing dispute specialist for an LTL freight brokerage.

Your role is to draft clear, professional, and factual dispute letters.

CRITICAL CONSTRAINTS:
1. DO NOT calculate or modify any dollar amounts. All financial figures are provided to you by the system and must be used EXACTLY as given.
2. DO NOT invent or guess email addresses, carrier names, or contact information.
3. DO NOT add any financial amounts that are not explicitly provided in the context.
4. Be concise, professional, and factual. Cite specific contractual terms and evidence.
5. The letter should be firm but professional — not accusatory.

You will be given:
- Invoice number and shipment details
- The EXACT disputed amount (do not change this)
- The EXACT expected (contracted) amount (do not change this)
- Specific audit findings explaining why each charge is disputed
- Reference documents (BOL, rate confirmation, etc.)

Your output must be ONLY the dispute letter text and a professional subject line."""


def build_dispute_letter_prompt(
    dispute_number: str,
    invoice_number: str,
    carrier_name: str,
    recipient_contact_name: str,
    disputed_amount: float,  # From AuditFindingRecord.discrepancy_amount — injected by code
    expected_amount: float,  # From deterministic calculation — injected by code
    billed_amount: float,  # From CarrierInvoice — injected by code
    findings: list[dict],
    shipment_number: str = "",
) -> str:
    """Build the dispute letter generation prompt.
    All dollar amounts are injected from deterministic sources — never from LLM memory.
    """
    findings_text = ""
    for i, f in enumerate(findings, 1):
        findings_text += f"""\n{i}. Rule: {f.get('rule_name', f.get('rule_id', 'Unknown'))}
   Issue: {f.get('reason', 'See attached documentation')}
   Rule ID: {f.get('rule_id', 'N/A')}"""

    return f"""Please draft a professional freight billing dispute letter with the following EXACT details.
Do NOT change any dollar amounts — they are verified and injected from authoritative records.

Dispute Reference: {dispute_number}
Carrier: {carrier_name}
Recipient: {recipient_contact_name}
Invoice Number: {invoice_number}
Shipment: {shipment_number}

FINANCIAL FACTS (do not modify these amounts):
- Total Billed Amount: ${billed_amount:.2f}
- Expected Contract Amount: ${expected_amount:.2f}
- Total Disputed Amount: ${disputed_amount:.2f}

Audit Findings (reasons for dispute):
{findings_text}

Required output format:
SUBJECT: [subject line]

LETTER:
[professional dispute letter body]

The letter should:
1. State the invoice and dispute ID clearly
2. List each disputed charge with the exact reason
3. Request a corrected invoice or credit memo
4. Reference the dispute ID for response tracking
5. Be respectful and professional in tone"""
