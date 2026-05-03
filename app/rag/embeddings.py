"""Batched embedding wrapper around the OpenRouter client."""

from app.config import Settings
from app.llm.openrouter import OpenRouterClient


class Embedder:
    def __init__(self, client: OpenRouterClient, settings: Settings, batch_size: int = 100) -> None:
        self.client = client
        self.settings = settings
        self.batch_size = batch_size

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            vecs = await self.client.embed(texts=batch, model=self.settings.embed_model)
            out.extend(vecs)
        return out

    async def embed_one(self, text: str) -> list[float]:
        vecs = await self.client.embed(texts=[text], model=self.settings.embed_model)
        return vecs[0]
