"""Phase 2.5 — Vector & Semantic Search Engine with Tenant Scoping."""

import math
import uuid
from typing import Any, Dict, List, Optional

from packages.domain.logging import logger
from packages.llm.gateway import LLMGateway
from packages.retrieval.schemas import ContextChunk, SearchResultItem


class VectorSearchEngine:
    """Semantic chunk indexer and vector similarity searcher."""

    def __init__(self, llm_gateway: Optional[LLMGateway] = None):
        self.llm_gateway = llm_gateway or LLMGateway()
        # In-memory index partitioned by organization_id for fast local / test search
        self._org_indices: Dict[str, List[Dict[str, Any]]] = {}

    @staticmethod
    def _deterministic_embedding(text: str, dim: int = 128) -> List[float]:
        """Produce a deterministic normalized vector representation for testing/offline environments."""
        import hashlib
        vec = [0.0] * dim
        words = text.lower().split()
        for i, word in enumerate(words):
            h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
            idx = h % dim
            sign = 1.0 if ((h >> 4) % 2 == 0) else -1.0
            weight = 1.0 / (1.0 + math.log(1.0 + i))
            vec[idx] += sign * weight

        # Normalize
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec

    @staticmethod
    def _cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
        """Compute cosine similarity between two normalized vectors."""
        if not vec1 or not vec2 or len(vec1) != len(vec2):
            return 0.0
        dot = sum(a * b for a, b in zip(vec1, vec2))
        return max(0.0, min(1.0, (dot + 1.0) / 2.0))

    async def get_embedding(self, text: str) -> List[float]:
        """Compute vector embedding using LiteLLM or deterministic fallback."""
        try:
            # When API key configured, use litellm embedding
            import litellm
            resp = litellm.embedding(model="text-embedding-3-small", input=[text[:8000]])
            if resp and resp.data and len(resp.data) > 0:
                return resp.data[0]["embedding"]
        except Exception as e:
            logger.debug(f"LiteLLM embedding fallback engaged: {e}")

        return self._deterministic_embedding(text)

    async def index_chunks(
        self,
        organization_id: uuid.UUID,
        chunks: List[ContextChunk],
    ) -> int:
        """Index document/message chunks for an organization."""
        org_key = str(organization_id)
        if org_key not in self._org_indices:
            self._org_indices[org_key] = []

        indexed_count = 0
        for chunk in chunks:
            emb = await self.get_embedding(chunk.content)
            self._org_indices[org_key].append({
                "chunk": chunk,
                "embedding": emb,
            })
            indexed_count += 1

        return indexed_count

    async def search(
        self,
        organization_id: uuid.UUID,
        query: str,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> List[SearchResultItem]:
        """Perform semantic search across indexed chunks for an organization."""
        org_key = str(organization_id)
        entries = self._org_indices.get(org_key, [])
        if not entries or not query.strip():
            return []

        query_emb = await self.get_embedding(query)
        scored: List[Dict[str, Any]] = []

        for item in entries:
            score = self._cosine_similarity(query_emb, item["embedding"])
            if score >= min_score:
                scored.append({"item": item["chunk"], "score": score})

        scored.sort(key=lambda x: x["score"], reverse=True)
        results: List[SearchResultItem] = []

        for s in scored[:top_k]:
            c: ContextChunk = s["item"]
            results.append(
                SearchResultItem(
                    entity_type="chunk",
                    entity_id=c.chunk_id,
                    title=f"{c.source_type} snippet ({c.source_id[:8]}...)",
                    matched_fields=["semantic_similarity"],
                    relevance_score=round(s["score"], 4),
                    snippet=c.content[:200].replace("\n", " "),
                    metadata={
                        "source_type": c.source_type,
                        "source_id": c.source_id,
                        **c.metadata,
                    },
                )
            )

        return results
