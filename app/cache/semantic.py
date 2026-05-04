"""Semantic cache using RediSearch vector index (HNSW + cosine).

Three-stage verification:
  Stage 1 — Vector similarity: KNN-1, accept iff cosine similarity >= threshold.
  Stage 2 — Entity conflict gate: if the new query and cached query reference
             different known entities (companies, projects), force MISS.
  Stage 3 — Keyword overlap: extract key nouns/entities from both queries and
             verify sufficient Jaccard overlap.

Layout:
- Index: idx:chat:sem:v2
- Documents: hash entries `chat:sem:v2:{uuid}` with fields:
    embedding (FLOAT32 vector, dim=N)
    query (text)
    answer (text)
    doc_ids (text — JSON list)
    entities (text — JSON list of detected entity tags)
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

# ──────────────────────────────────────────────────────────────────────
#  Stopwords — expanded with generic portfolio/HR filler terms
# ──────────────────────────────────────────────────────────────────────
_STOPWORDS = frozenset(
    # Standard English stopwords
    "i me my we our you your he him his she her it its they them their "
    "what which who whom this that these those am is are was were be been "
    "being have has had having do does did doing a an the and but if or "
    "because as until while of at by for with about against between through "
    "during before after above below to from up down in out on off over "
    "under again further then once here there when where why how all each "
    "every both few more most other some such no nor not only own same so "
    "than too very can will just don should now could would should also "
    # Query filler words
    "tell me about please show give get let know like want need describe "
    "explain share details information help "
    # Generic professional/HR terms (low signal for distinguishing queries)
    "role responsibilities overview info working worked work done "
    "experience current currently job position "
    # Person-specific stopwords
    "chetan marathe".split()
)

# ──────────────────────────────────────────────────────────────────────
#  Known entities — companies, projects, and key topics
#  Used for the entity conflict gate (Stage 2).
# ──────────────────────────────────────────────────────────────────────
_KNOWN_ENTITIES: dict[str, str] = {
    # Companies
    "schbang": "company:schbang",
    "cometchat": "company:cometchat",
    # Projects
    "csat": "project:csat",
    "chet.ai": "project:chet.ai",
    "chet": "project:chet.ai",
    "gittogether": "project:gittogether",
    "britannia": "project:britannia",
    "whatsapp": "project:whatsapp-clone",
    "secondbrain": "project:secondbrain",
    # Key technical domains (help distinguish "voice calling" from "CSAT")
    "webrtc": "tech:voice",
    "voip": "tech:voice",
    "elevenlabs": "tech:voice",
    "voice": "tech:voice",
    "webflow": "tech:webflow",
    "cms": "tech:webflow",
}

_WORD_RE = re.compile(r"[a-z0-9]+(?:[.\-'][a-z0-9]+)*", re.IGNORECASE)


def _extract_keywords(text: str) -> set[str]:
    """Extract meaningful keywords from a query (lowercase, no stopwords)."""
    words = {w.lower() for w in _WORD_RE.findall(text)}
    return words - _STOPWORDS


def _extract_entities(text: str) -> set[str]:
    """Extract known entity tags from text. Returns e.g. {'company:schbang', 'project:csat'}."""
    lowered = text.lower()
    found: set[str] = set()
    for token, entity_tag in _KNOWN_ENTITIES.items():
        if token in lowered:
            found.add(entity_tag)
    return found


def _entities_conflict(entities_a: set[str], entities_b: set[str]) -> bool:
    """Return True if both sets contain entities from the same category but different values.

    Example:
      {'company:schbang'} vs {'company:cometchat'} → True (conflict)
      {'company:schbang'} vs {'project:csat'} → False (different categories, no conflict)
      {'project:csat'} vs {'project:gittogether'} → True (conflict)
      {} vs {'company:schbang'} → False (no conflict if one side has no entities)
    """
    if not entities_a or not entities_b:
        return False

    # Group by category
    cats_a: dict[str, set[str]] = {}
    for e in entities_a:
        cat = e.split(":")[0]
        cats_a.setdefault(cat, set()).add(e)

    for e in entities_b:
        cat = e.split(":")[0]
        if cat in cats_a and e not in cats_a[cat]:
            return True  # same category, different entity

    return False


def _keyword_overlap(query_a: str, query_b: str) -> float:
    """Jaccard similarity of keyword sets. Returns 0.0–1.0."""
    kw_a = _extract_keywords(query_a)
    kw_b = _extract_keywords(query_b)
    if not kw_a or not kw_b:
        return 0.0
    intersection = kw_a & kw_b
    union = kw_a | kw_b
    return len(intersection) / len(union)


def should_accept_cache_hit(
    new_query: str,
    cached_query: str,
    *,
    vector_similarity: float,
    vector_threshold: float = 0.93,
    keyword_threshold: float = 0.4,
) -> tuple[bool, str]:
    """Three-stage cache acceptance check.

    Returns (accepted: bool, reason: str) for logging/debugging.
    """
    # Stage 1: Vector similarity
    if vector_similarity < vector_threshold:
        return False, f"vector sim {vector_similarity:.4f} < {vector_threshold}"

    # Stage 2: Entity conflict gate
    new_entities = _extract_entities(new_query)
    cached_entities = _extract_entities(cached_query)
    if _entities_conflict(new_entities, cached_entities):
        return False, (
            f"entity conflict: new={new_entities} cached={cached_entities}"
        )

    # Stage 3: Keyword overlap
    overlap = _keyword_overlap(new_query, cached_query)
    if overlap < keyword_threshold:
        return False, (
            f"keyword overlap {overlap:.2f} < {keyword_threshold} | "
            f"new_kw={_extract_keywords(new_query)} "
            f"cached_kw={_extract_keywords(cached_query)}"
        )

    return True, f"accepted: sim={vector_similarity:.4f} overlap={overlap:.2f}"


def _to_bytes(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


class SemanticCache:
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
            TextField("entities"),
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
        """Three-stage cache lookup:
        1. Vector similarity >= threshold
        2. Entity conflict gate (same category, different entity → MISS)
        3. Keyword overlap >= KEYWORD_OVERLAP_MIN
        """
        await self.ensure_index()
        q = (
            Query("*=>[KNN 1 @embedding $vec AS score]")
            .sort_by("score")
            .return_fields("query", "answer", "doc_ids", "entities", "model", "score")
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

        # Vector similarity
        try:
            distance = float(doc.score)
        except (AttributeError, ValueError):
            return None
        similarity = 1.0 - distance

        # Three-stage acceptance check
        cached_query = getattr(doc, "query", "")
        if query_text and cached_query:
            accepted, reason = should_accept_cache_hit(
                query_text,
                cached_query,
                vector_similarity=similarity,
                vector_threshold=self.threshold,
                keyword_threshold=self.KEYWORD_OVERLAP_MIN,
            )
            if not accepted:
                logger.info(
                    "sem-cache MISS: %s | new=%r cached=%r",
                    reason,
                    query_text[:80],
                    cached_query[:80],
                )
                return None
            logger.info(
                "sem-cache HIT: %s | new=%r cached=%r",
                reason,
                query_text[:60],
                cached_query[:60],
            )
        elif similarity < self.threshold:
            return None

        try:
            doc_ids = json.loads(doc.doc_ids) if hasattr(doc, "doc_ids") else []
        except (TypeError, ValueError):
            doc_ids = []

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
        entities = _extract_entities(query)
        key = f"{KEY_PREFIX}{uuid.uuid4()}"
        mapping = {
            "embedding": _to_bytes(embedding),
            "query": query,
            "answer": answer,
            "doc_ids": json.dumps(doc_ids),
            "entities": json.dumps(sorted(entities)),
            "ts": int(time.time()),
            "model": model,
        }
        await self.redis.hset(key, mapping=mapping)
        await self.redis.expire(key, self.ttl)
