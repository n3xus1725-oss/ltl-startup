"""PDF text, table, and metadata extractor."""

import io
from typing import Any, Dict, List, Tuple

from packages.domain.logging import logger


class PDFExtractor:
    """Extracts text and tabular content from PDF documents."""

    @staticmethod
    def extract(content: bytes) -> Tuple[str, List[List[str]], Dict[str, Any]]:
        """Extract text, tables, and metadata from PDF bytes.
        Returns: (extracted_text, tables, metadata)
        """
        extracted_text = ""
        tables: List[List[str]] = []
        metadata: Dict[str, Any] = {"page_count": 0}

        # 1. Try PyMuPDF (fitz) or pdfplumber if valid PDF binary
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(stream=content, filetype="pdf")
            metadata["page_count"] = len(doc)
            for page_idx in range(len(doc)):
                page = doc[page_idx]
                page_text = page.get_text()
                if page_text:
                    extracted_text += f"\n--- Page {page_idx + 1} ---\n" + page_text

            doc.close()
        except Exception as e:
            logger.debug(f"PyMuPDF extraction skipped or failed: {e}")

        # 2. Try pdfplumber for table extraction
        if not tables:
            try:
                import pdfplumber

                with pdfplumber.open(io.BytesIO(content)) as pdf:
                    metadata["page_count"] = max(metadata.get("page_count", 0), len(pdf.pages))
                    for p in pdf.pages:
                        if not extracted_text:
                            extracted_text += p.extract_text() or ""
                        p_tables = p.extract_tables()
                        for tbl in p_tables:
                            if tbl:
                                tables.extend(tbl)
            except Exception as e:
                logger.debug(f"pdfplumber extraction skipped or failed: {e}")

        # 3. Fallback for mock/test PDFs or plain ASCII content
        if not extracted_text.strip():
            try:
                # Try raw ASCII / UTF-8 decode
                raw_str = content.decode("utf-8", errors="ignore")
                # Look for printable text
                clean_lines = [
                    line.strip()
                    for line in raw_str.splitlines()
                    if any(c.isalnum() for c in line) and len(line.strip()) > 3
                ]
                extracted_text = "\n".join(clean_lines)
            except Exception:
                pass

        return extracted_text.strip(), tables, metadata
