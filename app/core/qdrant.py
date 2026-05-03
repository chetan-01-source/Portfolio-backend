from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from app.config import get_settings
from app.core.logging import logger

_client: AsyncQdrantClient | None = None


def get_qdrant() -> AsyncQdrantClient:
    global _client
    if _client is None:
        s = get_settings()
        _client = AsyncQdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key or None, timeout=30)
    return _client


async def ensure_collection() -> None:
    s = get_settings()
    client = get_qdrant()
    collections = await client.get_collections()
    names = {c.name for c in collections.collections}
    if s.qdrant_collection in names:
        return
    await client.create_collection(
        collection_name=s.qdrant_collection,
        vectors_config=qmodels.VectorParams(size=s.embed_dim, distance=qmodels.Distance.COSINE),
    )
    logger.info(f"Created Qdrant collection {s.qdrant_collection}")


async def close_qdrant() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
