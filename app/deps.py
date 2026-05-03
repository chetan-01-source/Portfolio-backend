"""FastAPI DI providers.

Controllers use these via `Depends(...)`. Services receive concrete clients
through their constructors so they can be unit-tested without FastAPI.
"""

from typing import Annotated

from fastapi import Depends, Request
from motor.motor_asyncio import AsyncIOMotorDatabase
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis

from app.config import Settings, get_settings
from app.core.mongo import get_db
from app.core.qdrant import get_qdrant
from app.core.redis import get_redis


def settings_dep() -> Settings:
    return get_settings()


def db_dep() -> AsyncIOMotorDatabase:
    return get_db()


def redis_dep() -> Redis:
    return get_redis()


def qdrant_dep() -> AsyncQdrantClient:
    return get_qdrant()


class RequestMeta:
    __slots__ = ("ip", "user_agent", "request_id")

    def __init__(self, ip: str, user_agent: str, request_id: str) -> None:
        self.ip = ip
        self.user_agent = user_agent
        self.request_id = request_id


def request_meta(request: Request) -> RequestMeta:
    return RequestMeta(
        ip=getattr(request.state, "client_ip", "-"),
        user_agent=getattr(request.state, "user_agent", "-"),
        request_id=getattr(request.state, "request_id", "-"),
    )


SettingsDep = Annotated[Settings, Depends(settings_dep)]
DbDep = Annotated[AsyncIOMotorDatabase, Depends(db_dep)]
RedisDep = Annotated[Redis, Depends(redis_dep)]
QdrantDep = Annotated[AsyncQdrantClient, Depends(qdrant_dep)]
RequestMetaDep = Annotated[RequestMeta, Depends(request_meta)]
