"""Exact-match query cache.

Key: chat:exact:v2:sha256(normalized_query|model)
Value: JSON {answer, doc_ids, model, ts}
"""

import hashlib
import json
import re
import time
from typing import Any

from redis.asyncio import Redis

from app.config import Settings

_WS = re.compile(r"\s+")
KEY_PREFIX = "chat:exact:v2:"


def normalize(q: str) -> str:
    return _WS.sub(" ", (q or "").strip().lower())


def cache_key(query: str, model: str) -> str:
    h = hashlib.sha256(f"{normalize(query)}|{model}".encode()).hexdigest()
    return f"{KEY_PREFIX}{h}"


class ExactCache:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self.redis = redis
        self.ttl = settings.exact_cache_ttl_seconds

    async def get(self, query: str, model: str) -> dict[str, Any] | None:
        raw = await self.redis.get(cache_key(query, model))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    async def flush_all(self) -> int:
        """Delete all exact-cache entries. Returns count deleted."""
        keys: list[bytes] = []
        async for key in self.redis.scan_iter("chat:exact:*"):
            keys.append(key)
        if keys:
            await self.redis.delete(*keys)
        return len(keys)

    async def set(
        self, query: str, model: str, *, answer: str, doc_ids: list[str]
    ) -> None:
        payload = {
            "answer": answer,
            "doc_ids": doc_ids,
            "model": model,
            "ts": int(time.time()),
        }
        await self.redis.setex(cache_key(query, model), self.ttl, json.dumps(payload))
