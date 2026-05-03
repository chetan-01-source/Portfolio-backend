from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import Settings, get_settings
from app.core.logging import logger

_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncIOMotorClient(settings.mongodb_uri, uuidRepresentation="standard")
    return _client


def get_db() -> AsyncIOMotorDatabase:
    settings = get_settings()
    return get_client()[settings.mongodb_db]


async def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


async def ensure_indexes(settings: Settings | None = None) -> None:
    """Idempotent index bootstrap. Called on app startup."""
    settings = settings or get_settings()
    db = get_db()

    await db.leads.create_index("email")
    await db.leads.create_index([("created_at", -1)])
    await db.leads.create_index([("send_details_choice", 1), ("emailed", 1)])

    await db.hire_sessions.create_index("expires_at", expireAfterSeconds=0)

    await db.chat_logs.create_index([("session_id", 1), ("created_at", 1)])
    await db.chat_logs.create_index([("created_at", -1)])

    await db.eval_runs.create_index([("created_at", -1)])
    await db.eval_runs.create_index([("model", 1), ("created_at", -1)])

    await db.request_logs.create_index(
        "created_at", expireAfterSeconds=settings.request_log_ttl_seconds
    )
    await db.request_logs.create_index("request_id")

    logger.info("Mongo indexes ensured")
