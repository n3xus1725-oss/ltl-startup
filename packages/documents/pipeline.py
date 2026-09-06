"""Phase 2.2 — Document Processing Pipeline.

Pipeline:
Document received
-> file validation
-> text/table extraction
-> document classification
-> field normalization
-> schema validation
-> provenance assertions generation
-> persistence
"""

import hashlib
import uuid
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from packages.documents.classifier import DocumentClassifier
from packages.documents.extractors.image import ImageExtractor
from packages.documents.extractors.pdf import PDFExtractor
from packages.documents.extractors.spreadsheet import SpreadsheetExtractor
from packages.documents.normalizer import DocumentNormalizer
from packages.documents.schemas import (
    NormalizedDocumentHeader,
    ProcessedDocumentResult,
)
from packages.domain.logging import logger
from packages.storage.repositories.documents import DocumentRepository


class DocumentProcessingPipeline:
    """End-to-end document intake, parsing, and normalization pipeline."""

    def __init__(self, db: Optional[Session] = None):
        self.db = db
        self.doc_repo = DocumentRepository(db) if db else None

    def process_content(
        self,
        filename: str,
        content_bytes: bytes,
        mime_type: Optional[str] = None,
        document_id: Optional[str] = None,
        organization_id: Optional[uuid.UUID] = None,
    ) -> ProcessedDocumentResult:
        """Run complete document processing pipeline on raw binary content."""
        # 1. Validation & Checksum
        sha = hashlib.sha256(content_bytes).hexdigest()
        ext = filename.lower().split(".")[-1] if "." in filename else ""

        # 2. Multi-Format Text/Table Extraction
        tables: List[List[str]] = []
        metadata: Dict[str, Any] = {}

        if ext in ("pdf",) or (mime_type and "pdf" in mime_type):
            extracted_text, tables, metadata = PDFExtractor.extract(content_bytes)
        elif ext in ("csv",):
            extracted_text, tables, metadata = SpreadsheetExtractor.extract_csv(content_bytes)
        elif ext in ("xlsx", "xls"):
            extracted_text, tables, metadata = SpreadsheetExtractor.extract_xlsx(content_bytes)
        elif ext in ("png", "jpg", "jpeg", "tiff", "tif") or (mime_type and "image" in mime_type):
            extracted_text, tables, metadata = ImageExtractor.extract(content_bytes)
        else:
            # Fallback to plain text decode
            try:
                extracted_text = content_bytes.decode("utf-8", errors="replace")
            except Exception:
                extracted_text = ""

        # 3. Document Classification
        classified_type = DocumentClassifier.classify(filename, extracted_text)

        # 4. Field Normalization
        extracted_fields: Dict[str, Any] = {}
        provenance_assertions: List[Dict[str, Any]] = []
        doc_ref_id = document_id or f"doc-{sha[:10]}"

        if classified_type == "BOL":
            bol_obj = DocumentNormalizer.normalize_bol(extracted_text, tables)
            extracted_fields = bol_obj.model_dump(exclude_none=True)

            if bol_obj.total_weight_lbs is not None:
                provenance_assertions.append({
                    "field": "freight_details.total_weight_lbs",
                    "value": bol_obj.total_weight_lbs,
                    "source": "bol",
                    "source_id": doc_ref_id,
                    "authority": 80,
                    "confidence": 0.95,
                    "evidence": {"filename": filename, "checksum": sha},
                })
            if bol_obj.pallet_count is not None:
                provenance_assertions.append({
                    "field": "freight_details.pallet_count",
                    "value": bol_obj.pallet_count,
                    "source": "bol",
                    "source_id": doc_ref_id,
                    "authority": 80,
                    "confidence": 0.95,
                })
            if bol_obj.bol_number:
                provenance_assertions.append({
                    "field": "billing_references.bol_number",
                    "value": bol_obj.bol_number,
                    "source": "bol",
                    "source_id": doc_ref_id,
                    "authority": 85,
                    "confidence": 0.98,
                })
            if bol_obj.trailer_number:
                provenance_assertions.append({
                    "field": "carrier.trailer_number",
                    "value": bol_obj.trailer_number,
                    "source": "bol",
                    "source_id": doc_ref_id,
                    "authority": 90,
                    "confidence": 0.95,
                })

        elif classified_type == "POD":
            pod_obj = DocumentNormalizer.normalize_pod(extracted_text)
            extracted_fields = pod_obj.model_dump(exclude_none=True)

            if pod_obj.delivery_date:
                provenance_assertions.append({
                    "field": "dates.actual_delivery",
                    "value": pod_obj.delivery_date,
                    "source": "pod",
                    "source_id": doc_ref_id,
                    "authority": 95,
                    "confidence": 0.98,
                })
            provenance_assertions.append({
                "field": "status",
                "value": "delivered",
                "source": "pod",
                "source_id": doc_ref_id,
                "authority": 95,
                "confidence": 0.98,
            })

        elif classified_type == "RATE_CONFIRMATION":
            rc_obj = DocumentNormalizer.normalize_rate_con(extracted_text)
            extracted_fields = rc_obj.model_dump(exclude_none=True)

            if rc_obj.total_agreed_rate is not None:
                provenance_assertions.append({
                    "field": "pricing.agreed_total",
                    "value": rc_obj.total_agreed_rate,
                    "source": "rate_confirmation",
                    "source_id": doc_ref_id,
                    "authority": 100,
                    "confidence": 0.99,
                })
            if rc_obj.linehaul_rate is not None:
                provenance_assertions.append({
                    "field": "pricing.linehaul",
                    "value": rc_obj.linehaul_rate,
                    "source": "rate_confirmation",
                    "source_id": doc_ref_id,
                    "authority": 100,
                    "confidence": 0.99,
                })
            if rc_obj.carrier_name:
                provenance_assertions.append({
                    "field": "carrier.carrier_name",
                    "value": rc_obj.carrier_name,
                    "source": "rate_confirmation",
                    "source_id": doc_ref_id,
                    "authority": 95,
                    "confidence": 0.95,
                })

        elif classified_type == "INVOICE":
            inv_obj = DocumentNormalizer.normalize_invoice(extracted_text)
            extracted_fields = inv_obj.model_dump(exclude_none=True)

            if inv_obj.total_billed_amount is not None:
                provenance_assertions.append({
                    "field": "pricing.billed_total",
                    "value": inv_obj.total_billed_amount,
                    "source": "invoice",
                    "source_id": doc_ref_id,
                    "authority": 70,
                    "confidence": 0.95,
                })
            if inv_obj.linehaul_amount is not None:
                provenance_assertions.append({
                    "field": "pricing.linehaul",
                    "value": inv_obj.linehaul_amount,
                    "source": "invoice",
                    "source_id": doc_ref_id,
                    "authority": 70,
                    "confidence": 0.95,
                })
            if inv_obj.invoice_number:
                provenance_assertions.append({
                    "field": "billing_references.invoice_id",
                    "value": inv_obj.invoice_number,
                    "source": "invoice",
                    "source_id": doc_ref_id,
                    "authority": 90,
                    "confidence": 0.98,
                })

        elif classified_type == "SCALE_TICKET":
            st_obj = DocumentNormalizer.normalize_scale_ticket(extracted_text)
            extracted_fields = st_obj.model_dump(exclude_none=True)

            if st_obj.net_weight_lbs is not None:
                provenance_assertions.append({
                    "field": "freight_details.total_weight_lbs",
                    "value": st_obj.net_weight_lbs,
                    "source": "scale_ticket",
                    "source_id": doc_ref_id,
                    "authority": 100,  # Certified reweigh has highest authority
                    "confidence": 0.99,
                    "evidence": {"scale_ticket_number": st_obj.ticket_number, "scale_name": st_obj.scale_name},
                })
                provenance_assertions.append({
                    "field": "freight_details.weight_type",
                    "value": "verified_reweigh",
                    "source": "scale_ticket",
                    "source_id": doc_ref_id,
                    "authority": 100,
                    "confidence": 0.99,
                })

        header = NormalizedDocumentHeader(
            document_id=document_id,
            file_name=filename,
            document_type=classified_type,
            mime_type=mime_type or "application/octet-stream",
            checksum_sha256=sha,
            confidence=0.95 if classified_type != "UNKNOWN" else 0.5,
        )

        result = ProcessedDocumentResult(
            header=header,
            classified_type=classified_type,
            extracted_fields=extracted_fields,
            confidence=header.confidence,
            provenance_assertions=provenance_assertions,
            raw_text_snippet=extracted_text[:500],
        )

        # 5. Persist to DB if Document ID is provided and DB session available
        if self.db and document_id and organization_id:
            try:
                doc_uuid = uuid.UUID(document_id)
                doc = self.doc_repo.get_by_id(organization_id, doc_uuid)
                if doc:
                    doc.document_type = classified_type
                    doc.extracted_data = {
                        "classified_type": classified_type,
                        "fields": extracted_fields,
                        "assertions": provenance_assertions,
                    }
                    self.db.commit()
            except Exception as exc:
                logger.error(f"Failed to update document {document_id} extracted_data: {exc}")

        return result
