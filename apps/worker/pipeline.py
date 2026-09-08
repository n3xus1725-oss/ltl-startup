import asyncio
import logging
from typing import Dict, Any, Optional
import uuid

from sqlalchemy.orm import Session
from fastapi import BackgroundTasks

from packages.storage.db import SessionLocal
from packages.domain.models import Shipment, Document, Organization
from packages.storage.repositories.documents import DocumentRepository
from packages.storage.repositories.shipments import ShipmentRepository
from packages.storage.repositories.contracts import RateContractRepository
from packages.storage.repositories.invoices import InvoiceRepository
from apps.agent.audit.service import run_audit_agent
from apps.agent.dispute.service import run_dispute_agent
from packages.domain.logging import logger

async def process_e2e_pipeline(organization_id: str, shipment_id: str):
    """
    End-to-End Orchestration:
    1. Check if the shipment has an invoice attached.
    2. If yes, run the Billing Audit Agent.
    3. If the Audit Agent finds a discrepancy (terminal_outcome == "discrepancy_found" or similar),
       run the Dispute Agent.
    """
    logger.info(f"[E2E Pipeline] Started for Shipment {shipment_id}")
    
    # We need an isolated DB session since this runs in the background
    db = SessionLocal()
    try:
        org_uuid = uuid.UUID(organization_id)
        ship_uuid = uuid.UUID(shipment_id)
        
        # 1. Look for an invoice document
        doc_repo = DocumentRepository(db)
        docs = db.query(Document).filter(
            Document.organization_id == org_uuid,
            Document.shipment_id == ship_uuid
        ).all()
        
        invoice_doc = None
        for d in docs:
            # check if it's an invoice, or if it has invoice in the name/content
            if d.document_type and "invoice" in d.document_type.lower():
                invoice_doc = d
                break
            elif "invoice" in (d.file_name or "").lower():
                invoice_doc = d
                break
                
        if not invoice_doc:
            logger.info(f"[E2E Pipeline] No invoice found for Shipment {shipment_id}. Stopping.")
            return

        # Get invoice text from extracted data or use a fallback
        text_to_audit = ""
        if invoice_doc.extracted_data and "text" in invoice_doc.extracted_data:
            text_to_audit = invoice_doc.extracted_data["text"]
        else:
            # For testing with preloaded scenarios or if text extraction isn't hooked up:
            logger.warning(f"[E2E Pipeline] Invoice {invoice_doc.id} has no extracted text! Simulating extraction...")
            text_to_audit = f"INVOICE Total: $1850.00 (Linehaul: $1650, Fuel: $200)" # dummy text if needed

        # Prepare for Audit
        shipment_repo = ShipmentRepository(db)
        shipment = shipment_repo.get_by_id(org_uuid, ship_uuid)
        if not shipment:
            return
        carrier_name = shipment.carrier_name or "Estes Express Lines"
        
        logger.info(f"[E2E Pipeline] Triggering Audit Agent for Invoice on Shipment {shipment_id}")
        
        # Ensure a Rate Contract exists for the carrier
        contract_repo = RateContractRepository(db)
        contract = contract_repo.find_matching_contract(org_uuid, carrier_name)
        if not contract:
            logger.warning(f"[E2E Pipeline] No contract found for {carrier_name}. Creating default.")
            contract = contract_repo.create_contract(
                organization_id=org_uuid,
                carrier_name=carrier_name,
                contract_reference=f"RC-{carrier_name[:3].upper()}-AUTO",
                effective_date=None,
                expiration_date=None,
                rules_payload=[
                    {"rule_id": "linehaul_match", "description": "Linehaul must match exact quoted rate."},
                    {"rule_id": "fuel_surcharge", "description": "FSC is 15% of linehaul."},
                ],
                rates_payload={"default_linehaul": 1650.0}
            )

        trigger_id = f"evt-audit-auto-{uuid.uuid4().hex[:8]}"
        
        audit_output = await run_audit_agent(
            organization_id=str(org_uuid),
            db=db,
            invoice_text=text_to_audit,
            carrier_name=carrier_name,
            invoice_number=f"INV-{shipment.shipment_number}",
            trigger_event_id=trigger_id,
            force_reasoning_model=False,
        )
        
        logger.info(f"[E2E Pipeline] Audit Output Outcome: {audit_output.terminal_outcome}")
        
        # If discrepancy found, trigger Dispute Agent
        if audit_output.terminal_outcome in ["discrepancy_found", "requires_human_approval", "discrepancies_found"]:
            logger.info(f"[E2E Pipeline] Discrepancy detected! Triggering Dispute Agent for Shipment {shipment_id}")
            
            # Find the new findings (AuditFindingRecord)
            from packages.domain.models import AuditFindingRecord
            findings = db.query(AuditFindingRecord).filter(
                AuditFindingRecord.organization_id == org_uuid,
                AuditFindingRecord.shipment_id == ship_uuid,
                AuditFindingRecord.status == "open"
            ).all()
            
            if not findings:
                logger.warning("[E2E Pipeline] Audit agent reported discrepancy but no open findings found in DB.")
                return
                
            finding_ids = [str(f.id) for f in findings]
            
            # Find the invoice record (Audit Agent should have created it)
            from packages.domain.models import CarrierInvoice
            invoice = db.query(CarrierInvoice).filter(
                CarrierInvoice.organization_id == org_uuid,
                CarrierInvoice.shipment_id == ship_uuid
            ).order_by(CarrierInvoice.created_at.desc()).first()
            
            if not invoice:
                logger.warning("[E2E Pipeline] No CarrierInvoice record found to dispute.")
                return
                
            # Need a mock LLM for Dispute Agent if we don't want to burn tokens, or use the real one.
            from packages.llm.gateway import LLMGateway
            llm = LLMGateway()
            
            dispute_result = run_dispute_agent(
                organization_id=str(org_uuid),
                invoice_id=str(invoice.id),
                finding_ids=finding_ids,
                db=db,
                llm=llm
            )
            
            logger.info(f"[E2E Pipeline] Dispute Agent Outcome: {dispute_result.status}")
            
    except Exception as e:
        logger.error(f"[E2E Pipeline] Failed processing: {e}", exc_info=True)
    finally:
        db.close()
