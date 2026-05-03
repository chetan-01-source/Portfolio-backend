"""Qdrant retriever with MMR diversification and kind-based boosting."""

import numpy as np
from qdrant_client import AsyncQdrantClient

from app.config import Settings
from app.rag.reranker import Reranker

# Chunks about Chetan himself should rank higher than random crawled URLs
_KIND_BOOST = {
    "resume": 0.15,
    "exp": 0.12,
    "project": 0.10,
    "faq": 0.08,
    "url": 0.0,  # no boost for generic crawled pages
}


def _cosine(a: list[float], b: list[float]) -> float:
    av = np.array(a, dtype=np.float32)
    bv = np.array(b, dtype=np.float32)
    denom = (np.linalg.norm(av) * np.linalg.norm(bv)) or 1e-9
    return float(np.dot(av, bv) / denom)


def mmr(
    query_vec: list[float],
    candidates: list[dict],
    *,
    k: int = 8,
    lambda_mult: float = 0.5,
) -> list[dict]:
    """Maximal Marginal Relevance over Qdrant candidates.

    Each candidate dict must contain `vector` and `score`. Returns up to k.
    """
    if not candidates:
        return []
    selected: list[dict] = []
    pool = list(candidates)
    while pool and len(selected) < k:
        best, best_score = None, -1e9
        for c in pool:
            relevance = c.get("score", _cosine(query_vec, c["vector"]))
            if not selected:
                diversity = 0.0
            else:
                diversity = max(_cosine(c["vector"], s["vector"]) for s in selected)
            mmr_score = lambda_mult * relevance - (1 - lambda_mult) * diversity
            if mmr_score > best_score:
                best_score, best = mmr_score, c
        if best is None:
            break
        selected.append(best)
        pool.remove(best)
    return selected


class Retriever:
    def __init__(
        self,
        qdrant: AsyncQdrantClient,
        reranker: Reranker,
        settings: Settings,
    ) -> None:
        self.qdrant = qdrant
        self.reranker = reranker
        self.settings = settings

    async def search(
        self,
        *,
        query_vec: list[float],
        query_text: str,
        top_k: int = 20,
        mmr_k: int = 12,
    ) -> list[dict]:
        result = await self.qdrant.query_points(
            collection_name=self.settings.qdrant_collection,
            query=query_vec,
            limit=top_k,
            with_payload=True,
            with_vectors=True,
        )
        candidates = []
        for p in result.points:
            payload = p.payload or {}
            kind = payload.get("kind", "url")
            boost = _KIND_BOOST.get(kind, 0.0)
            candidates.append(
                {
                    "id": str(p.id),
                    "score": float(p.score) + boost,
                    "vector": p.vector if isinstance(p.vector, list) else list(p.vector or []),
                    "payload": payload,
                    "text": payload.get("text", ""),
                }
            )
        diversified = mmr(query_vec, candidates, k=mmr_k)
        return await self.reranker.rerank(query_text, diversified)
