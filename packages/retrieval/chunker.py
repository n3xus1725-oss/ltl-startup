"""Phase 2.5 — Document Chunker for Structured and Unstructured Freight Evidence."""

import hashlib
from typing import Any, Dict, List, Optional

from packages.retrieval.schemas import ContextChunk


class DocumentChunker:
    """Splits raw text and tables into searchable chunks with provenance metadata."""

    @staticmethod
    def chunk_text(
        text: str,
        source_id: str,
        source_type: str = "document",
        chunk_size: int = 500,
        chunk_overlap: int = 100,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[ContextChunk]:
        """Chunk unstructured or semi-structured text by logical lines / paragraphs."""
        if not text or not text.strip():
            return []

        base_meta = metadata.copy() if metadata else {}
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return []

        chunks: List[ContextChunk] = []
        current_chunk_lines: List[str] = []
        current_length = 0

        for line in lines:
            line_len = len(line)
            if current_length + line_len > chunk_size and current_chunk_lines:
                chunk_str = "\n".join(current_chunk_lines)
                chunk_hash = hashlib.sha256(chunk_str.encode("utf-8")).hexdigest()[:12]
                chunk_meta = {**base_meta, "chunk_hash": chunk_hash, "line_count": len(current_chunk_lines)}
                chunks.append(
                    ContextChunk(
                        source_type=source_type,
                        source_id=source_id,
                        content=chunk_str,
                        metadata=chunk_meta,
                    )
                )
                # Apply overlap by keeping last few lines
                overlap_lines = []
                overlap_len = 0
                for prev_line in reversed(current_chunk_lines):
                    if overlap_len + len(prev_line) <= chunk_overlap:
                        overlap_lines.insert(0, prev_line)
                        overlap_len += len(prev_line)
                    else:
                        break
                current_chunk_lines = overlap_lines
                current_length = overlap_len

            current_chunk_lines.append(line)
            current_length += line_len

        if current_chunk_lines:
            chunk_str = "\n".join(current_chunk_lines)
            chunk_hash = hashlib.sha256(chunk_str.encode("utf-8")).hexdigest()[:12]
            chunk_meta = {**base_meta, "chunk_hash": chunk_hash, "line_count": len(current_chunk_lines)}
            chunks.append(
                ContextChunk(
                    source_type=source_type,
                    source_id=source_id,
                    content=chunk_str,
                    metadata=chunk_meta,
                )
            )

        return chunks

    @staticmethod
    def chunk_table(
        table_rows: List[List[str]],
        source_id: str,
        source_type: str = "document_table",
        rows_per_chunk: int = 10,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[ContextChunk]:
        """Chunk tabular data, preserving table header across chunks."""
        if not table_rows or len(table_rows) < 2:
            return []

        header = " | ".join([c.strip() for c in table_rows[0]])
        data_rows = table_rows[1:]
        chunks: List[ContextChunk] = []
        base_meta = metadata.copy() if metadata else {}

        for i in range(0, len(data_rows), rows_per_chunk):
            batch = data_rows[i : i + rows_per_chunk]
            lines = [header, "---"]
            for r in batch:
                lines.append(" | ".join([str(c).strip() for c in r]))
            content = "\n".join(lines)
            chunks.append(
                ContextChunk(
                    source_type=source_type,
                    source_id=source_id,
                    content=content,
                    metadata={**base_meta, "row_start": i + 1, "row_end": i + len(batch)},
                )
            )

        return chunks
