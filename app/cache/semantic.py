"""Semantic cache using RediSearch vector index (HNSW + cosine).

Two-stage verification:
  Stage 1 — Vector similarity: KNN-1, accept iff cosine similarity >= threshold.
  Stage 2 — Keyword overlap: extract key nouns/entities from both queries and
             verify sufficient overlap.  This prevents false positives like
             "Tell me about CSAT project" matching a cached "Schbang experience"
             response despite high embedding similarity.

Layout:
- Index: idx:chat:sem:v2
- Documents: hash entries `chat:sem:v2:{uuid}` with fields:
    embedding (FLOAT32 vector, dim=N)
    query (text)
    answer (text)
    doc_ids (text — JSON list)
    ts (numeric)
"""

import json
import re
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

# Common stopwords to ignore during keyword extraction.
_STOPWORDS = frozenset(
    "i me my we our you your he him his she her it its they them their "
    "what which who whom this that these those am is are was were be been "
    "being have has had having do does did doing a an the and but if or "
    "because as until while of at by for with about against between through "
    "during before after above below to from up down in out on off over "
    "under again further then once here there when where why how all each "
    "every both few more most other some such no nor not only own same so "
    "than too very can will just don should now could would should also "
    "tell me about please show give get let know like want need describe "
    "explain share details information chetan marathe his".split()
)

_WORD_RE = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*", re.IGNORECASE)


def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from a query (lowercase, no stopwords)."""
    words = {w.lower() for w in _WORD_RE.findall(text)}
    return words - _STOPWORDS


def _keyword_overlap(query_a: str, query_b: str) -> float:
    """Jaccard similarity of keyword sets. Returns 0.0–1.0."""
    kw_a = _extract_keywords(query_a)
    kw_b = _extract_keywords(query_b)
    if not kw_a or not kw_b:
        return 0.0
    intersection = kw_a & kw_b
    union = kw_a | kw_b
    return len(intersection) / len(union)


def _to_bytes(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


class SemanticCache:
    # Minimum keyword overlap needed to accept a cache hit (Jaccard 0–1).
    KEYWORD_OVERLAP_MIN = 0.4

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

    async def lookup(
        self, embedding: list[float], *, query_text: str = ""
    ) -> dict[str, Any] | None:
        """Two-stage cache lookup:
        1. Vector similarity >= threshold
        2. Keyword overlap >= KEYWORD_OVERLAP_MIN (prevents topically-adjacent false positives)
        """
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

        # --- Stage 1: Vector similarity ---
        try:
            distance = float(doc.score)
        except (AttributeError, ValueError):
            return None
        similarity = 1.0 - distance
        if similarity < self.threshold:
            logger.debug(
                "sem-cache MISS (vector): sim=%.4f < threshold=%.4f",
                similarity,
                self.threshold,
            )
            return None

        # --- Stage 2: Keyword overlap ---
        cached_query = getattr(doc, "query", "")
        if query_text and cached_query:
            overlap = _keyword_overlap(query_text, cached_query)
            if overlap < self.KEYWORD_OVERLAP_MIN:
                logger.info(
                    "sem-cache MISS (keyword): sim=%.4f ok but keyword_overlap=%.2f < %.2f | "
                    "new=%r cached=%r",
                    similarity,
                    overlap,
                    self.KEYWORD_OVERLAP_MIN,
                    query_text[:80],
                    cached_query[:80],
                )
                return None

        try:
            doc_ids = json.loads(doc.doc_ids) if hasattr(doc, "doc_ids") else []
        except (TypeError, ValueError):
            doc_ids = []

        logger.info(
            "sem-cache HIT: sim=%.4f | new=%r cached=%r",
            similarity,
            query_text[:60],
            cached_query[:60],
        )
        return {
            "answer": getattr(doc, "answer", ""),
            "query": cached_query,
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
