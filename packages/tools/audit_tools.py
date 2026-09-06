"""Phase 3.4 — Typed Tools for Billing Audit Agent.

Implements all required audit tools:
1. get_invoice
2. find_matching_contract
3. get_shipment_documents
4. record_audit_findings
5. approve_invoice
6. flag_dispute
"""

import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from packages.storage.repositories.audit_findings import AuditFindingRepository
from packages.storage.repositories.contracts import RateContractRepository
from packages.storage.repositories.documents import DocumentRepository
from packages.storage.repositories.invoices import InvoiceRepository
from packages.tools.registry import BaseTool, ToolContext, ToolRegistry

# ---------------- 1. get_invoice ---------------- #

class GetInvoiceInput(BaseModel):
    invoice_id: Optional[str] = Field(None, description="UUID of the invoice")
    invoice_number: Optional[str] = Field(None, description="External invoice number")
    carrier_name: Optional[str] = Field(None, description="Carrier name")


class GetInvoiceOutput(BaseModel):
    found: bool
    invoice: Optional[Dict[str, Any]] = None


class GetInvoiceTool(BaseTool):
    name = "get_invoice"
    purpose = "Retrieve carrier invoice record and line items within organization scope."
    input_schema = GetInvoiceInput
    output_schema = GetInvoiceOutput
    required_permission = "invoice:read"
    external_side_effects = False

    async def execute(
        self, context: ToolContext, db: Session, input_data: GetInvoiceInput
    ) -> GetInvoiceOutput:
        repo = InvoiceRepository(db)
        inv = None
        if input_data.invoice_id:
            inv = repo.get_by_invoice_id(uuid.UUID(input_data.invoice_id))
        elif input_data.invoice_number and input_data.carrier_name:
            inv = repo.get_by_number(context.organization_id, input_data.carrier_name, input_data.invoice_number)

        if not inv or inv.organization_id != context.organization_id:
            return GetInvoiceOutput(found=False)

        return GetInvoiceOutput(
            found=True,
            invoice={
                "id": str(inv.id),
                "invoice_number": inv.invoice_number,
                "carrier_name": inv.carrier_name,
                "shipment_id": str(inv.shipment_id) if inv.shipment_id else None,
                "total_billed_amount": inv.total_billed_amount,
                "linehaul_amount": inv.linehaul_amount,
                "fuel_amount": inv.fuel_amount,
                "accessorial_amount": inv.accessorial_amount,
                "weight_lbs": inv.weight_lbs,
                "freight_class": inv.freight_class,
                "pallet_count": inv.pallet_count,
                "line_items": inv.line_items or [],
                "accessorials": inv.accessorials or [],
                "status": inv.status,
            },
        )


# ---------------- 2. find_matching_contract ---------------- #

class FindMatchingContractInput(BaseModel):
    carrier_name: str = Field(..., description="Carrier name to match contract")
    origin_state: Optional[str] = Field(None, description="Origin state abbreviation")
    origin_zip: Optional[str] = Field(None, description="Origin 5-digit zip")
    destination_state: Optional[str] = Field(None, description="Destination state abbreviation")
    destination_zip: Optional[str] = Field(None, description="Destination 5-digit zip")


class FindMatchingContractOutput(BaseModel):
    found: bool
    contract: Optional[Dict[str, Any]] = None


class FindMatchingContractTool(BaseTool):
    name = "find_matching_contract"
    purpose = "Locate effective rate contract matching carrier and lane hierarchy."
    input_schema = FindMatchingContractInput
    output_schema = FindMatchingContractOutput
    required_permission = "contract:read"
    external_side_effects = False

    async def execute(
        self, context: ToolContext, db: Session, input_data: FindMatchingContractInput
    ) -> FindMatchingContractOutput:
        repo = RateContractRepository(db)
        contract = repo.find_matching_contract(
            organization_id=context.organization_id,
            carrier_name=input_data.carrier_name,
            origin_state=input_data.origin_state,
            destination_state=input_data.destination_state,
            origin_zip=input_data.origin_zip,
            destination_zip=input_data.destination_zip,
        )

        if not contract:
            return FindMatchingContractOutput(found=False)

        return FindMatchingContractOutput(
            found=True,
            contract={
                "id": str(contract.id),
                "contract_number": contract.contract_number,
                "carrier_name": contract.carrier_name,
                "base_rate": contract.base_rate,
                "minimum_charge": contract.minimum_charge,
                "rate_type": contract.rate_type,
                "fuel_schedule": contract.fuel_schedule,
                "accessorial_schedule": contract.accessorial_schedule,
                "class_rate_rules": getattr(contract, "class_rate_rules", None),
            },
        )


# ---------------- 3. get_shipment_documents ---------------- #

class GetShipmentDocumentsInput(BaseModel):
    shipment_id: str = Field(..., description="Shipment UUID")


class GetShipmentDocumentsOutput(BaseModel):
    count: int
    documents: List[Dict[str, Any]]
    attached_types: List[str]


class GetShipmentDocumentsTool(BaseTool):
    name = "get_shipment_documents"
    purpose = "Fetch verified documents (BOL, POD, scale tickets) attached to shipment."
    input_schema = GetShipmentDocumentsInput
    output_schema = GetShipmentDocumentsOutput
    required_permission = "document:read"
    external_side_effects = False

    async def execute(
        self, context: ToolContext, db: Session, input_data: GetShipmentDocumentsInput
    ) -> GetShipmentDocumentsOutput:
        doc_repo = DocumentRepository(db)
        docs = doc_repo.list_by_shipment(context.organization_id, uuid.UUID(input_data.shipment_id))

        results = []
        types = set()
        for d in docs:
            doc_type = d.document_type or "UNKNOWN"
            types.add(doc_type)
            results.append({
                "id": str(d.id),
                "document_type": doc_type,
                "filename": d.file_name,
                "extracted_data": d.extracted_data or {},
            })

        return GetShipmentDocumentsOutput(
            count=len(results),
            documents=results,
            attached_types=list(types),
        )


# ---------------- 4. record_audit_findings ---------------- #

class RecordAuditFindingsInput(BaseModel):
    invoice_id: str = Field(..., description="Invoice UUID")
    findings: List[Dict[str, Any]] = Field(default_factory=list, description="Structured audit findings")
    is_clean: bool = Field(..., description="True if no discrepancies found")
    total_discrepancy: float = Field(default=0.0, description="Total dollar variance")


class RecordAuditFindingsOutput(BaseModel):
    success: bool
    recorded_count: int
    invoice_status: str


class RecordAuditFindingsTool(BaseTool):
    name = "record_audit_findings"
    purpose = "Persist immutable structured audit findings to ledger."
    input_schema = RecordAuditFindingsInput
    output_schema = RecordAuditFindingsOutput
    required_permission = "invoice:write"
    external_side_effects = True

    async def execute(
        self, context: ToolContext, db: Session, input_data: RecordAuditFindingsInput
    ) -> RecordAuditFindingsOutput:
        finding_repo = AuditFindingRepository(db)
        inv_repo = InvoiceRepository(db)

        inv_uuid = uuid.UUID(input_data.invoice_id)
        count = 0
        for f in input_data.findings:
            finding_repo.create_finding(
                organization_id=context.organization_id,
                invoice_id=inv_uuid,
                rule_id=f.get("rule_id", "UNKNOWN"),
                rule_name=f.get("rule_name", f.get("rule_id", "UNKNOWN")),
                severity=f.get("severity", "medium"),
                expected_value=f.get("expected_value"),
                billed_value=f.get("billed_value"),
                difference=f.get("difference", 0.0),
                reason=f.get("reason", "Discrepancy detected"),
                confidence=f.get("confidence", 1.0),
                source_documents=f.get("source_documents", []),
                evidence_references=f.get("evidence_references", []),
            )
            count += 1

        new_status = "audited" if input_data.is_clean else "disputed"
        inv_repo.update_status(context.organization_id, inv_uuid, new_status)

        return RecordAuditFindingsOutput(
            success=True,
            recorded_count=count,
            invoice_status=new_status,
        )


# ---------------- 5. approve_invoice ---------------- #

class ApproveInvoiceInput(BaseModel):
    invoice_id: str = Field(..., description="Invoice UUID")
    approval_notes: Optional[str] = Field("Passed all 10 deterministic audit rules", description="Approval notes")


class ApproveInvoiceOutput(BaseModel):
    approved: bool
    invoice_id: str
    status: str


class ApproveInvoiceTool(BaseTool):
    name = "approve_invoice"
    purpose = "Approve clean carrier invoice for automated payment scheduling."
    input_schema = ApproveInvoiceInput
    output_schema = ApproveInvoiceOutput
    required_permission = "invoice:approve"
    external_side_effects = True

    async def execute(
        self, context: ToolContext, db: Session, input_data: ApproveInvoiceInput
    ) -> ApproveInvoiceOutput:
        inv_repo = InvoiceRepository(db)
        inv_uuid = uuid.UUID(input_data.invoice_id)
        inv = inv_repo.update_status(context.organization_id, inv_uuid, "approved")
        return ApproveInvoiceOutput(
            approved=True,
            invoice_id=str(inv.id),
            status=inv.status,
        )


# ---------------- 6. flag_dispute ---------------- #

class FlagDisputeInput(BaseModel):
    invoice_id: str = Field(..., description="Invoice UUID")
    dispute_amount: float = Field(..., description="Dollar amount disputed")
    dispute_reason: str = Field(..., description="Factual dispute summary")


class FlagDisputeOutput(BaseModel):
    disputed: bool
    invoice_id: str
    status: str
    dispute_amount: float


class FlagDisputeTool(BaseTool):
    name = "flag_dispute"
    purpose = "Flag carrier invoice for dispute resolution and halt automated payment."
    input_schema = FlagDisputeInput
    output_schema = FlagDisputeOutput
    required_permission = "invoice:write"
    external_side_effects = True

    async def execute(
        self, context: ToolContext, db: Session, input_data: FlagDisputeInput
    ) -> FlagDisputeOutput:
        inv_repo = InvoiceRepository(db)
        inv_uuid = uuid.UUID(input_data.invoice_id)
        inv = inv_repo.update_status(context.organization_id, inv_uuid, "disputed")
        return FlagDisputeOutput(
            disputed=True,
            invoice_id=str(inv.id),
            status=inv.status,
            dispute_amount=input_data.dispute_amount,
        )


def register_audit_tools(registry: ToolRegistry) -> None:
    """Register all billing audit tools with a ToolRegistry."""
    registry.register(GetInvoiceTool())
    registry.register(FindMatchingContractTool())
    registry.register(GetShipmentDocumentsTool())
    registry.register(RecordAuditFindingsTool())
    registry.register(ApproveInvoiceTool())
    registry.register(FlagDisputeTool())
