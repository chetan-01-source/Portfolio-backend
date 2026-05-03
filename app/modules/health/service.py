"""Pings each dependency, returns a structured map."""

import asyncio
from typing import Any

import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis

from app.config import Settings


async def _check_mongo(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    try:
        await db.command("ping")
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _check_redis(redis: Redis) -> dict[str, Any]:
    try:
        pong = await redis.ping()
        return {"ok": bool(pong)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _check_qdrant(qdrant: AsyncQdrantClient) -> dict[str, Any]:
    try:
        await qdrant.get_collections()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


async def _check_openrouter(settings: Settings) -> dict[str, Any]:
    if not settings.openrouter_api_key:
        return {"ok": False, "error": "no api key"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                f"{settings.openrouter_base_url}/models",
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            )
            return {"ok": r.status_code == 200}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class HealthService:
    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        redis: Redis,
        qdrant: AsyncQdrantClient,
        settings: Settings,
    ) -> None:
        self.db = db
        self.redis = redis
        self.qdrant = qdrant
        self.settings = settings

    async def status(self) -> dict[str, Any]:
        mongo, rds, qd, orouter = await asyncio.gather(
            _check_mongo(self.db),
            _check_redis(self.redis),
            _check_qdrant(self.qdrant),
            _check_openrouter(self.settings),
        )
        ok = all(c["ok"] for c in (mongo, rds, qd, orouter))
        return {
            "ok": ok,
            "deps": {
                "mongo": mongo,
                "redis": rds,
                "qdrant": qd,
                "openrouter": orouter,
            },
        }
