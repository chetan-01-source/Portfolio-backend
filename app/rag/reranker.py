"""Optional cross-encoder reranker client.

If RERANKER_URL is set, POST {"query": ..., "documents": [...]} and expect
{"scores": [...]} in response. Otherwise this is a no-op identity reranker.
"""

import httpx

from app.config import Settings
from app.core.logging import logger


class Reranker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.enabled = bool(settings.reranker_url)
        self.top_n = settings.reranker_top_n

    async def rerank(self, query: str, docs: list[dict]) -> list[dict]:
        if not docs:
            return docs
        if not self.enabled:
            return docs[: self.top_n]
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    self.settings.reranker_url,
                    json={"query": query, "documents": [d.get("text", "") for d in docs]},
                )
                r.raise_for_status()
                scores = r.json().get("scores", [])
        except Exception as e:
            logger.warning(f"reranker failed, falling back to identity: {e}")
            return docs[: self.top_n]
        ranked = sorted(zip(docs, scores), key=lambda p: p[1], reverse=True)
        return [d for d, _ in ranked[: self.top_n]]
