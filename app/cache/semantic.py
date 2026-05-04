"""Semantic cache using RediSearch vector index (HNSW + cosine).

Layout:
- Index: idx:chat:sem:v2
- Documents: hash entries `chat:sem:v2:{uuid}` with fields:
    embedding (FLOAT32 vector, dim=N)
    query (text)
    answer (text)
    doc_ids (text — JSON list)
    ts (numeric)

Lookup: KNN-1, accept iff (1 - cosine_distance) >= threshold.
"""

import json
import struct
import time
import uuid
from typing import Any

from redis.asyncio import Redis
from redis.commands.search.field import NumericField, TagField, TextField, VectorField
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.commands.search.query import Query

from app.config import Settings
from app.core.logging import logger

INDEX_NAME = "idx:chat:sem:v2"
KEY_PREFIX = "chat:sem:v2:"


def _to_bytes(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


class SemanticCache:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self.redis = redis
        self.dim = settings.embed_dim
        self.threshold = settings.semantic_cache_threshold
        self.ttl = settings.semantic_cache_ttl_seconds

    async def ensure_index(self) -> None:
        try:
            await self.redis.ft(INDEX_NAME).info()
            return
        except Exception:
            pass
        schema = (
            VectorField(
                "embedding",
                "HNSW",
                {"TYPE": "FLOAT32", "DIM": self.dim, "DISTANCE_METRIC": "COSINE"},
            ),
            TextField("query"),
            TextField("answer"),
            TextField("doc_ids"),
            NumericField("ts"),
            TagField("model"),
        )
        defn = IndexDefinition(prefix=[KEY_PREFIX], index_type=IndexType.HASH)
        try:
            await self.redis.ft(INDEX_NAME).create_index(schema, definition=defn)
            logger.info(f"Created RediSearch index {INDEX_NAME}")
        except Exception as e:
            logger.warning(f"Could not create RediSearch index (already exists?): {e}")

    async def lookup(self, embedding: list[float]) -> dict[str, Any] | None:
        await self.ensure_index()
        q = (
            Query("*=>[KNN 1 @embedding $vec AS score]")
            .sort_by("score")
            .return_fields("query", "answer", "doc_ids", "model", "score")
            .dialect(2)
        )
        try:
            res = await self.redis.ft(INDEX_NAME).search(
                q, query_params={"vec": _to_bytes(embedding)}
            )
        except Exception as e:
            logger.warning(f"semantic cache lookup failed: {e}")
            return None
        if not res.docs:
            return None
        doc = res.docs[0]
        # cosine distance ∈ [0, 2]; similarity = 1 - distance.
        try:
            distance = float(doc.score)
        except (AttributeError, ValueError):
            return None
        similarity = 1.0 - distance
        if similarity < self.threshold:
            return None
        try:
            doc_ids = json.loads(doc.doc_ids) if hasattr(doc, "doc_ids") else []
        except (TypeError, ValueError):
            doc_ids = []
        return {
            "answer": getattr(doc, "answer", ""),
            "query": getattr(doc, "query", ""),
            "doc_ids": doc_ids,
            "model": getattr(doc, "model", ""),
            "similarity": similarity,
        }

    async def flush_all(self) -> int:
        """Delete all semantic-cache entries. Returns count deleted."""
        keys: list[bytes] = []
        async for key in self.redis.scan_iter("chat:sem:*"):
            keys.append(key)
        if keys:
            await self.redis.delete(*keys)
        return len(keys)

    async def store(
        self,
        *,
        embedding: list[float],
        query: str,
        answer: str,
        doc_ids: list[str],
        model: str,
    ) -> None:
        key = f"{KEY_PREFIX}{uuid.uuid4()}"
        mapping = {
            "embedding": _to_bytes(embedding),
            "query": query,
            "answer": answer,
            "doc_ids": json.dumps(doc_ids),
            "ts": int(time.time()),
            "model": model,
        }
        await self.redis.hset(key, mapping=mapping)
        await self.redis.expire(key, self.ttl)
